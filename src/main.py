"""HCP → Slack for FUSE Monterey.

    python src/main.py live                  # approved estimates + new leads
    python src/main.py daily                 # yesterday's jobs per technician
    python src/main.py daily --date 2026-09-17

DRY_RUN=1 prints instead of posting. SLACK_TEST_CHANNEL=<id> sends everything
there instead of the real channels.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, time, timedelta, timezone

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import messages
import rules
from config import Settings, load_settings
from hcp import HCPClient
from slack import APPROVED_ESTIMATE, DAILY_JOBS, NEW_LEAD, Slack

log = logging.getLogger("hcp_monterey")

# How far back Slack history is read to find the last posted lead number.
LEAD_HISTORY_DAYS = 30


class Out:
    """Posts to Slack, or prints in a dry run. Dedup reads Slack whenever a token is
    available, so a dry run shows exactly what a real run would add.
    """

    def __init__(self, settings: Settings):
        self.dry_run = settings.dry_run
        self.slack = Slack(settings.slack_bot_token) if settings.slack_bot_token else None

    def posted(self, channel: str, event_type: str, since: datetime) -> list[dict]:
        if not self.slack or not channel:
            return []
        return self.slack.posted(channel, event_type, since)

    def post(self, channel: str, text: str, blocks: list[dict],
             event_type: str, payload: dict[str, str]) -> None:
        if self.dry_run:
            print(f"--- [{event_type} → {channel or '(no channel)'}] {payload}\n{text}\n")
            return
        self.slack.post(channel, text, blocks, event_type, payload)
        log.info("posted %s %s", event_type, payload)


# ---- live: approved estimates + new leads -------------------------------------------

def post_approved_estimates(s: Settings, hcp: HCPClient, out: Out, now: datetime) -> None:
    since = now - timedelta(hours=s.lookback_hours)
    estimates = hcp.recent_estimates(now - timedelta(days=s.estimate_max_age_days))
    approved = rules.approved_options(estimates, since)
    channel = s.channel("estimates")
    done = {p.get("option_id") for p in out.posted(channel, APPROVED_ESTIMATE, since - timedelta(days=1))}
    log.info("estimates: scanned %d, approved in window %d, already posted %d",
             len(estimates), len(approved), sum(a.option.get("id") in done for a in approved))
    for item in approved:
        if item.option.get("id") in done:
            continue
        est_id, opt_id = str(item.estimate.get("id")), str(item.option.get("id"))
        description = rules.estimate_description(
            item.estimate, item.option, hcp.option_line_items(est_id, opt_id))
        cid = str((item.estimate.get("customer") or {}).get("id") or "")
        job = rules.job_for_estimate(item.estimate, hcp.jobs_for_customer(cid), s.tz) if cid else None
        text, blocks = messages.approved_estimate(item, description, job, s.slack_ids,
                                                  s.links, s.estimate_cc)
        out.post(channel, text, blocks, APPROVED_ESTIMATE, {
            "option_id": str(item.option.get("id")),
            "estimate_id": str(item.estimate.get("id")),
        })


def post_new_leads(s: Settings, hcp: HCPClient, out: Out, now: datetime) -> None:
    since = now - timedelta(hours=s.lookback_hours)
    leads = hcp.recent_leads(since)
    channel = s.channel("leads")
    history = out.posted(channel, NEW_LEAD, now - timedelta(days=LEAD_HISTORY_DAYS))
    posted_ids = {p.get("lead_id") for p in history}
    numbers = [int(p["number"]) for p in history if str(p.get("number", "")).isdigit()]
    fresh = rules.new_leads(leads, since, posted_ids, max(numbers) if numbers else None)
    log.info("leads: fetched %d, new %d", len(leads), len(fresh))

    job_types: dict[str, str] | None = None
    for lead in fresh:
        type_id = (lead.get("job_fields") or {}).get("job_type_uuid")
        if type_id and job_types is None:
            job_types = hcp.job_types()
        service = (job_types or {}).get(type_id, "") if type_id else ""
        if not service:
            items = hcp.lead_line_items(str(lead["id"]))
            service = next((i.get("name") for i in items if i.get("name")), "")
        source = rules.classify_lead_source(lead.get("lead_source"), s.lead_sources,
                                            s.default_lead_source)
        text, blocks = messages.new_lead(lead, source, service, s.links)
        out.post(channel, text, blocks, NEW_LEAD, {
            "lead_id": str(lead.get("id")),
            "number": str(lead.get("number") or ""),
        })


# ---- daily: yesterday's jobs per technician -----------------------------------------

def post_daily(s: Settings, hcp: HCPClient, out: Out, now: datetime, day: date | None) -> None:
    local_now = now.astimezone(s.tz)
    if day is None:
        # Cron fires at several UTC hours so the post lands after post_after local
        # time in both PDT and PST; the early run just exits here.
        if local_now.time() < s.post_after:
            log.info("daily: %s local is before %s, nothing to do yet",
                     local_now.strftime("%H:%M"), s.post_after.strftime("%H:%M"))
            return
        day = local_now.date() - timedelta(days=1)

    start = datetime.combine(day, time.min, tzinfo=s.tz)
    jobs = [j for j in hcp.jobs_scheduled_between(start, start + timedelta(days=1))
            if not rules.is_canceled(j)]
    log.info("daily %s: %d jobs", day, len(jobs))

    estimates: dict[str, list[dict]] = {}
    for cid in {_customer_id(j) for j in jobs} - {"None"}:
        estimates[cid] = hcp.estimates_for_customer(cid)
    # Line items tell a repair from a diagnostic visit; invoices show check payments.
    details = {j["id"]: (hcp.job_line_items(j["id"]), hcp.job_invoices(j["id"])) for j in jobs}

    channel = s.channel("daily")
    done = {(p.get("date"), p.get("employee_id"))
            for p in out.posted(channel, DAILY_JOBS, start - timedelta(days=1))}

    for report in rules.group_by_technician(jobs):
        if (day.isoformat(), report.employee_id) in done:
            log.info("daily: %s already posted", report.name)
            continue
        lines = [rules.job_line(j, estimates.get(_customer_id(j), []),
                                details[j["id"]][0], details[j["id"]][1], day, s.tz)
                 for j in sorted(report.jobs, key=lambda j: str(j.get("invoice_number") or ""))]
        who = (f"*{report.name}*" if report.employee_id == rules.UNASSIGNED
               else messages.mention(report.employee, s.slack_ids))
        text, blocks = messages.daily_report(who, day, lines, s.links)
        out.post(channel, text, blocks, DAILY_JOBS,
                 {"date": day.isoformat(), "employee_id": report.employee_id})


def _customer_id(job: dict) -> str:
    return str((job.get("customer") or {}).get("id"))


# ---- entrypoint ---------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["live", "daily"])
    ap.add_argument("--date", type=date.fromisoformat,
                    help="daily only: report this day (YYYY-MM-DD) regardless of the time")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    s = load_settings()
    hcp = HCPClient(s.hcp_api_key, s.hcp_company_id)
    out = Out(s)
    now = datetime.now(timezone.utc)

    if args.mode == "daily":
        post_daily(s, hcp, out, now, args.date)
        return 0

    # The two live feeds are independent: a failure in one must not silence the other.
    failed = False
    for step in (post_approved_estimates, post_new_leads):
        try:
            step(s, hcp, out, now)
        except Exception:
            log.exception("%s failed", step.__name__)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
