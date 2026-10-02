from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

import main
import messages
import rules
from config import Settings
from slack import APPROVED_ESTIMATE, DAILY_JOBS, NEW_LEAD

TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)  # 08:00 PDT, Friday


def settings(**kw) -> Settings:
    base = dict(
        hcp_api_key="k", hcp_company_id="", slack_bot_token="xoxb", dry_run=False, test_channel="", tz=TZ,
        channels={"estimates": "CEST", "leads": "CLEAD", "daily": "CDAILY"},
        links={"estimate": "https://hcp/est/{id}", "job": "https://hcp/job/{id}"},
        lookback_hours=6, estimate_max_age_days=60, post_after=time(7, 0),
        lead_sources=[{"label": "Yelp", "emoji": ":yelp:", "match": ["yelp"]}],
        default_lead_source={"label": "HCP", "emoji": ":fuse:"},
        slack_ids={"p1": "URAY"}, estimate_cc=["UOLEKSII"], exclude_employees=set(),
    )
    base.update(kw)
    return Settings(**base)


class FakeHCP:
    def __init__(self, estimates=(), leads=(), jobs=(), customer_estimates=None):
        self._estimates, self._leads, self._jobs = list(estimates), list(leads), list(jobs)
        self._customer_estimates = customer_estimates or {}
        self.windows = []

    def recent_estimates(self, since):
        return self._estimates

    def jobs_for_customer(self, cid):
        return [{"id": "job_v", "invoice_number": "5823",
                 "schedule": {"scheduled_start": "2026-09-18T16:00:00Z"}, "work_status": "complete rated"}]

    def recent_leads(self, since):
        return self._leads

    def job_types(self):
        return {"jt1": "HVAC Repair"}

    def option_line_items(self, estimate_id, option_id):
        return [{"name": "Washer repair", "kind": "labor"}]

    def lead_line_items(self, lead_id):
        return [{"name": "Dryer repair"}]

    def job_line_items(self, job_id):
        return {"j0": [{"kind": "labor", "name": "Service Call - Appliance Diagnostics"}],
                "j1": [{"kind": "labor", "name": "Service Call - Appliance Diagnostics"}]}.get(job_id, [])

    def job_invoices(self, job_id):
        if job_id == "j0":
            return [{"status": "paid", "payments": [{"status": "succeeded", "category": "check"}]}]
        return []

    def jobs_scheduled_between(self, start, end):
        self.windows.append((start, end))
        return self._jobs

    def estimates_for_customer(self, cid):
        return self._customer_estimates.get(cid, [])


class FakeOut:
    def __init__(self, history=None):
        self.history = history or {}
        self.sent = []

    def posted(self, channel, event_type, since):
        return self.history.get((channel, event_type), [])

    def post(self, channel, text, blocks, event_type, payload):
        self.sent.append((channel, event_type, payload, text))


RAY = {"id": "p1", "first_name": "Raymond", "last_name": "Smith"}


def approved_estimate(option_id="o1", at="2026-09-18T14:00:00Z"):
    return {"id": "e1", "estimate_number": "3064", "created_at": "2026-09-18T17:00:00Z",
            "customer": {"id": "cus_1", "first_name": "Phil", "last_name": "Chase"},
            "estimate_fields": {"job_type": None},
            "assigned_employees": [RAY],
            "options": [{"id": option_id, "name": "Option #1", "total_amount": 46900,
                         "approval_status": "approved", "approval_status_updated_at": at}]}


def test_approved_estimate_message_matches_manager_format():
    out = FakeOut()
    main.post_approved_estimates(settings(), FakeHCP(estimates=[approved_estimate()]), out, NOW)
    [(channel, event, payload, text)] = out.sent
    assert (channel, event, payload) == ("CEST", APPROVED_ESTIMATE, {"option_id": "o1", "estimate_id": "e1"})
    assert text == ("\n".join([
        ":bell: *APPROVED ESTIMATE* :bell:",
        "*Washer repair*",
        "*Customer Name:* Phil Chase",
        "*Estimate Number:* 3064",
        "*Job:* <https://hcp/job/job_v|#5823>",
        "*Amount:* $469",
        "*Estimator:* <@URAY>",
        "*CC:* <@UOLEKSII>",
    ]))


def test_approved_estimate_has_link_button():
    item = rules.approved_options([approved_estimate()], datetime(2026, 9, 1, tzinfo=timezone.utc))[0]
    _, blocks = messages.approved_estimate(item, "", None, {}, {"estimate": "https://hcp/est/{id}"}, [])
    assert blocks[-1]["elements"][0]["url"] == "https://hcp/est/o1"  # option id


def test_approved_estimate_not_reposted():
    out = FakeOut({("CEST", APPROVED_ESTIMATE): [{"option_id": "o1"}]})
    main.post_approved_estimates(settings(), FakeHCP(estimates=[approved_estimate()]), out, NOW)
    assert out.sent == []


def test_test_channel_overrides_real_channels():
    out = FakeOut()
    main.post_approved_estimates(settings(test_channel="CTEST"), FakeHCP(estimates=[approved_estimate()]), out, NOW)
    assert out.sent[0][0] == "CTEST"


def test_new_lead_message_per_source_and_service_fallback():
    leads = [
        {"id": "l1", "number": 7, "created_at": "2026-09-18T14:10:00Z", "lead_source": "Yelp",
         "customer": {"first_name": "Roshna", "last_name": "T."}, "job_fields": {"job_type_uuid": "jt1"}},
        {"id": "l2", "number": 8, "created_at": "2026-09-18T14:20:00Z", "lead_source": "Website",
         "customer": {"first_name": "Ann", "mobile_number": "+18315550100"}},
    ]
    out = FakeOut()
    main.post_new_leads(settings(), FakeHCP(leads=leads), out, NOW)
    texts = [t for *_, t in out.sent]
    assert texts[0] == ":yelp: *New Lead from Yelp*\n*Name:* Roshna T.\n*Service:* HVAC Repair"
    assert texts[1] == ":fuse: *New Lead from HCP*\n*Name:* Ann\n*Phone:* +18315550100\n*Service:* Dryer repair"
    assert out.sent[0][2] == {"lead_id": "l1", "number": "7"}


def daily_jobs():
    return [
        {"id": "j1", "invoice_number": "5760", "customer": {"id": "c1", "first_name": "Jim", "last_name": "Shepner"},
         "assigned_employees": [RAY], "work_status": "complete rated", "attachments": [],
         "total_amount": 20000, "outstanding_balance": 0},
        {"id": "j0", "invoice_number": "5688", "customer": {"id": "c2", "first_name": "Liz", "last_name": "Patania"},
         "assigned_employees": [RAY], "work_status": "complete rated",
         "attachments": [{"file_type": "image/jpeg"}], "total_amount": 9900, "outstanding_balance": 0},
        {"id": "jx", "invoice_number": "5790", "customer": {"id": "c3"}, "assigned_employees": [RAY],
         "work_status": "pro canceled"},
    ]


def test_daily_report_format_and_window():
    hcp = FakeHCP(jobs=daily_jobs(), customer_estimates={
        "c1": [{"id": "e5", "created_at": "2026-09-17T19:00:00Z", "options": [{"approval_status": None}]}],
        "c2": [{"id": "e6", "created_at": "2026-09-17T17:00:00Z", "options": [{"approval_status": "approved"}]}],
    })
    out = FakeOut()
    main.post_daily(settings(), hcp, out, NOW, None)

    start, end = hcp.windows[0]
    assert start == datetime(2026, 9, 17, tzinfo=TZ) and (end - start).days == 1
    [(channel, event, payload, text)] = out.sent
    assert (channel, event, payload) == ("CDAILY", DAILY_JOBS, {"date": "2026-09-17", "employee_id": "p1"})
    assert text == "\n".join([
        "<@URAY> Jobs — Thursday, September 17",
        "",
        "*<https://hcp/job/j0|#5688> — Liz Patania*",
        "Estimate: :white_check_mark: Approved",
        "Photos: :white_check_mark:",
        "Paid: :white_check_mark: check",
        "",
        "*<https://hcp/job/j1|#5760> — Jim Shepner*",
        "Estimate: :white_check_mark:",
        "Photos: :x:",
        "Paid: :white_check_mark:",
    ])


def test_daily_waits_for_post_after_and_skips_posted():
    early = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)  # 06:30 PDT
    out = FakeOut()
    hcp = FakeHCP(jobs=daily_jobs())
    main.post_daily(settings(), hcp, out, early, None)
    assert hcp.windows == [] and out.sent == []

    out = FakeOut({("CDAILY", DAILY_JOBS): [{"date": "2026-09-17", "employee_id": "p1"}]})
    main.post_daily(settings(), FakeHCP(jobs=daily_jobs()), out, NOW, None)
    assert out.sent == []


def test_daily_explicit_date_ignores_clock():
    early = datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc)
    out = FakeOut()
    main.post_daily(settings(), FakeHCP(jobs=daily_jobs()), out, early, date(2026, 9, 10))
    assert out.sent[0][2]["date"] == "2026-09-10"
    assert "Jobs — Thursday, September 10" in out.sent[0][3]


def test_missing_channel_fails_loudly():
    with pytest.raises(RuntimeError, match="channels.leads"):
        settings(channels={}).channel("leads")


def test_new_lead_has_link_button():
    source = rules.LeadSource("Thumbtack", ":thumbtack:")
    _, blocks = messages.new_lead({"id": "lea_1"}, source, "", {"lead": "https://hcp/leads/{id}"})
    assert blocks[-1]["elements"][0]["url"] == "https://hcp/leads/lea_1"
    _, blocks = messages.new_lead({"id": "lea_1"}, source, "", {})
    assert len(blocks) == 1


def test_daily_skips_excluded_employee():
    out = FakeOut()
    main.post_daily(settings(exclude_employees={"p1"}), FakeHCP(jobs=daily_jobs()), out, NOW, None)
    assert out.sent == []
