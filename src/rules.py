"""Business rules, no I/O: which estimates count as approved, which leads are new,
and what each line of the daily technician report says.

Everything here works on raw HCP dicts so tests can feed in trimmed API payloads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from hcp import parse_ts

CHECK = ":white_check_mark:"
CROSS = ":x:"

# Job work_status values (spec: "needs scheduling", "scheduled", "in progress",
# "complete rated", "complete unrated", "user canceled", "pro canceled").
_CANCELED = {"user canceled", "pro canceled", "canceled", "cancelled"}
_COMPLETE = {"complete rated", "complete unrated", "completed", "complete"}

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif")
_GENERIC_OPTION = re.compile(r"^\s*option\s*#?\s*\d*\s*$", re.I)


# ---- approval status ---------------------------------------------------------------

def approval_kind(status: str | None) -> str:
    """Normalize an option's approval_status to "approved", "declined" or "".

    The spec leaves the field an untyped string. Seen/likely values include
    "approved", "pro approved", "declined", "pro declined" — the customer approving
    through the link and the tech marking it in the app both count.
    """
    s = (status or "").lower()
    if "declin" in s:
        return "declined"
    if "approv" in s:
        return "approved"
    return ""


@dataclass
class ApprovedOption:
    estimate: dict
    option: dict
    approved_at: datetime | None


def approved_options(estimates: list[dict], since: datetime) -> list[ApprovedOption]:
    """Options approved at or after `since`, oldest first (so Slack reads in order)."""
    found = []
    for est in estimates:
        for opt in est.get("options") or []:
            if approval_kind(opt.get("approval_status")) != "approved":
                continue
            at = parse_ts(opt.get("approval_status_updated_at")) or parse_ts(opt.get("updated_at"))
            if at is None or at < since:
                continue
            found.append(ApprovedOption(est, opt, at))
    found.sort(key=lambda a: a.approved_at)
    return found


def estimate_description(est: dict, opt: dict, line_items: list[dict]) -> str:
    """"Washer repair": job type, else a non-generic option name, else the first
    labor line item. Monterey leaves job type empty and options named "Option #1",
    so in practice it is the labor line (materials and discounts are skipped).
    """
    job_type = ((est.get("estimate_fields") or {}).get("job_type") or {}).get("name")
    if job_type:
        return job_type
    name = (opt.get("name") or "").strip()
    if name and not _GENERIC_OPTION.match(name):
        return name
    labor = [i for i in line_items if (i.get("kind") or "").lower() == "labor" and i.get("name")]
    return labor[0]["name"] if labor else ""


def money(cents: int | float | None) -> str:
    if cents is None:
        return "—"
    dollars = cents / 100
    return f"${dollars:,.0f}" if dollars == int(dollars) else f"${dollars:,.2f}"


# ---- leads -------------------------------------------------------------------------

@dataclass
class LeadSource:
    label: str
    emoji: str


def classify_lead_source(raw: str | None, sources: list[dict], default: dict) -> LeadSource:
    """First entry whose `match` substring appears in the HCP lead_source wins."""
    text = (raw or "").lower()
    for src in sources:
        if any(m.lower() in text for m in src.get("match", [])):
            return LeadSource(src["label"], src.get("emoji", ""))
    return LeadSource(default["label"], default.get("emoji", ""))


def lead_number(lead: dict) -> int | None:
    try:
        return int(lead.get("number"))
    except (TypeError, ValueError):
        return None


def new_leads(
    leads: list[dict],
    since: datetime,
    posted_ids: set[str],
    last_posted_number: int | None,
) -> list[dict]:
    """Leads not yet posted, oldest first.

    With created_at: anything created inside the window. Without it (the spec's Lead
    object doesn't list the field): anything numbered above the last lead we posted.
    On the very first run there is no such number, so only the newest lead goes out —
    it becomes the baseline instead of flooding the channel with the backlog.
    """
    fresh = []
    for lead in leads:
        if str(lead.get("id")) in posted_ids:
            continue
        created = parse_ts(lead.get("created_at"))
        if created is not None:
            if created >= since:
                fresh.append(lead)
            continue
        number = lead_number(lead)
        if number is None:
            continue
        if last_posted_number is None or number > last_posted_number:
            fresh.append(lead)

    if last_posted_number is None and fresh and all(not l.get("created_at") for l in fresh):
        fresh = [max(fresh, key=lambda l: lead_number(l) or 0)]

    def order(lead: dict):
        return (parse_ts(lead.get("created_at")) or datetime.min.replace(tzinfo=since.tzinfo),
                lead_number(lead) or 0)

    return sorted(fresh, key=order)


def customer_name(customer: dict | None) -> str:
    c = customer or {}
    name = " ".join(p for p in (c.get("first_name"), c.get("last_name")) if p).strip()
    return name or c.get("company") or "—"


def customer_phone(customer: dict | None) -> str:
    c = customer or {}
    return c.get("mobile_number") or c.get("home_number") or c.get("work_number") or ""


# ---- daily technician report -------------------------------------------------------

def is_canceled(job: dict) -> bool:
    return bool(job.get("canceled_at") or job.get("deleted_at")
                or (job.get("work_status") or "").lower() in _CANCELED)


def is_complete(job: dict) -> bool:
    return ((job.get("work_status") or "").lower() in _COMPLETE
            or bool((job.get("work_timestamps") or {}).get("completed_at")))


def has_photos(job: dict) -> bool:
    for att in job.get("attachments") or []:
        kind = (att.get("file_type") or "").lower()
        name = (att.get("file_name") or att.get("url") or "").lower().split("?")[0]
        if kind.startswith("image") or name.endswith(_IMAGE_EXT):
            return True
    return False


def is_paid(job: dict) -> bool:
    """Nothing left to collect. A $0 job (free estimate visit) counts as paid,
    as in the manual reports."""
    balance = job.get("outstanding_balance")
    return balance is not None and balance <= 0


def paid_by_check(invoices: list[dict]) -> bool:
    """HCP records a check as payment_method "external" with category "check";
    the manual report flags those ("Paid ✅ check") so someone collects the paper."""
    return any((p.get("category") or "").lower() == "check"
               and (p.get("status") or "succeeded") == "succeeded"
               for inv in invoices if (inv.get("status") or "") != "voided"
               for p in inv.get("payments") or [])


def local_date(value: str | None, tz: ZoneInfo) -> date | None:
    ts = parse_ts(value)
    return ts.astimezone(tz).date() if ts else None


def estimates_from_visit(job: dict, customer_estimates: list[dict],
                         day: date, tz: ZoneInfo) -> list[dict]:
    """Estimates the tech wrote for this visit.

    The API has no job → estimate link for estimates created *from* a job, so this
    matches the same customer, created on the visit day or later — techs often
    write it up that evening or the next day (#5830, #5834 in the Sep 21–23
    manual reports). The estimate the job was copied from is excluded.
    """
    origin = {job.get("original_estimate_id"), *(job.get("original_estimate_uuids") or [])}
    return [e for e in customer_estimates
            if e.get("id") not in origin
            and (local_date(e.get("created_at"), tz) or date.min) >= day]


def estimate_outcome(estimates: list[dict]) -> str:
    """"Approved", "Declined", or "" (sent, no answer yet)."""
    kinds = [approval_kind(o.get("approval_status"))
             for e in estimates for o in (e.get("options") or [])]
    if "approved" in kinds:
        return "Approved"
    if kinds and all(k == "declined" for k in kinds):
        return "Declined"
    return ""


# Line items that are the visit itself rather than work done: the diagnostic /
# service-call fee, a free estimate visit, a $0 follow-up placeholder.
_VISIT_LINE = re.compile(r"diagnos|estimate|service call|additional (visit|work)|trip charge", re.I)
_DIAGNOSTIC_LINE = re.compile(r"diagnos", re.I)


def work_lines(line_items: list[dict]) -> list[dict]:
    """Billable repair / install lines: materials, or labor that isn't the visit fee."""
    found = []
    for item in line_items:
        kind = (item.get("kind") or "").lower()
        if "discount" in kind:
            continue
        if kind == "materials" or not _VISIT_LINE.search(item.get("name") or ""):
            found.append(item)
    return found


@dataclass
class JobLine:
    job: dict
    first_label: str          # "Repair done" / "Estimate" / "Diagnostic report"
    first_ok: bool
    first_note: str           # "Approved" / "Declined" / ""
    photos: bool
    paid: bool
    paid_note: str = ""       # "check"

    @property
    def number(self) -> str:
        return str(self.job.get("invoice_number") or "")

    @property
    def customer(self) -> str:
        return customer_name(self.job.get("customer"))


def job_line(job: dict, customer_estimates: list[dict], line_items: list[dict],
             invoices: list[dict], day: date, tz: ZoneInfo) -> JobLine:
    """First line of a job block, matched against the manual Sep 21–23 reports:

    - the job has repair/install work on it → "Repair done", ✅ once completed
      (a return visit that isn't finished yet is "Repair done ❌")
    - otherwise an estimate was written → "Estimate ✅ Approved / Declined / (sent)"
    - otherwise it was a diagnostic-only visit → "Diagnostic report", ✅ once completed
    - otherwise → "Estimate ❌": the visit ended with nothing for the customer
    """
    made = estimates_from_visit(job, customer_estimates, day, tz)
    if work_lines(line_items):
        first = ("Repair done", is_complete(job), "")
    elif made:
        first = ("Estimate", True, estimate_outcome(made))
    elif any(_DIAGNOSTIC_LINE.search(i.get("name") or "") for i in line_items):
        first = ("Diagnostic report", is_complete(job), "")
    else:
        first = ("Estimate", False, "")
    paid = is_paid(job)
    return JobLine(job, *first, photos=has_photos(job), paid=paid,
                   paid_note="check" if paid and paid_by_check(invoices) else "")


@dataclass
class TechReport:
    employee_id: str
    name: str
    employee: dict
    jobs: list[dict] = field(default_factory=list)


UNASSIGNED = "unassigned"


def group_by_technician(jobs: list[dict]) -> list[TechReport]:
    """One report per assigned employee; a job with two techs appears under both.
    Jobs with nobody assigned go to a trailing "Unassigned" report so none vanish.
    """
    reports: dict[str, TechReport] = {}
    for job in jobs:
        for emp in job.get("assigned_employees") or [{"id": UNASSIGNED}]:
            emp_id = str(emp.get("id") or UNASSIGNED)
            name = "Unassigned" if emp_id == UNASSIGNED else customer_name(emp)
            reports.setdefault(emp_id, TechReport(emp_id, name, emp)).jobs.append(job)
    return sorted(reports.values(), key=lambda r: (r.employee_id == UNASSIGNED, r.name.lower()))
