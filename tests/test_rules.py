from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import rules

TZ = ZoneInfo("America/Los_Angeles")
SINCE = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
DAY = date(2026, 9, 17)


def test_approval_kind_normalizes_customer_and_pro_variants():
    assert rules.approval_kind("approved") == "approved"
    assert rules.approval_kind("pro approved") == "approved"
    assert rules.approval_kind("customer_approved") == "approved"
    assert rules.approval_kind("pro declined") == "declined"
    assert rules.approval_kind("declined") == "declined"
    assert rules.approval_kind(None) == ""
    assert rules.approval_kind("pending") == ""


def test_approved_options_only_inside_window_and_sorted():
    est = {"id": "e1", "options": [
        {"id": "o1", "approval_status": "approved", "approval_status_updated_at": "2026-09-24T13:00:00Z"},
        {"id": "o2", "approval_status": "approved", "approval_status_updated_at": "2026-09-24T11:59:00Z"},
        {"id": "o3", "approval_status": "declined", "approval_status_updated_at": "2026-09-24T14:00:00Z"},
    ]}
    est2 = {"id": "e2", "options": [
        {"id": "o4", "approval_status": "pro approved", "approval_status_updated_at": "2026-09-24T12:30:00"},
    ]}
    found = rules.approved_options([est, est2], SINCE)
    assert [a.option["id"] for a in found] == ["o4", "o1"]


def test_estimate_description_job_type_then_option_name_then_labor_line():
    opt = {"name": "Option #1"}
    items = [{"name": "Discount", "kind": "fixed discount"}, {"name": "Main motor", "kind": "materials"},
             {"name": "Washer repair", "kind": "labor"}]
    assert rules.estimate_description({"estimate_fields": {"job_type": {"name": "HVAC"}}}, opt, items) == "HVAC"
    assert rules.estimate_description({}, {"name": "Replace compressor"}, items) == "Replace compressor"
    assert rules.estimate_description({"estimate_fields": {"job_type": None}}, opt, items) == "Washer repair"
    assert rules.estimate_description({}, opt, []) == ""


def test_money():
    assert rules.money(46900) == "$469"
    assert rules.money(46950) == "$469.50"
    assert rules.money(123456700) == "$1,234,567"


SOURCES = [
    {"label": "Yelp", "emoji": ":yelp:", "match": ["yelp"]},
    {"label": "GLS", "emoji": ":googleads:", "match": ["google", "lsa"]},
]
DEFAULT = {"label": "HCP", "emoji": ":fuse:"}


def test_classify_lead_source():
    assert rules.classify_lead_source("Yelp Leads", SOURCES, DEFAULT).label == "Yelp"
    assert rules.classify_lead_source("Google Local Services", SOURCES, DEFAULT).label == "GLS"
    assert rules.classify_lead_source("Online booking", SOURCES, DEFAULT).label == "HCP"
    assert rules.classify_lead_source(None, SOURCES, DEFAULT).emoji == ":fuse:"


def test_new_leads_with_created_at_uses_window_and_posted_ids():
    leads = [
        {"id": "l3", "number": 3, "created_at": "2026-09-24T14:00:00Z"},
        {"id": "l2", "number": 2, "created_at": "2026-09-24T13:00:00Z"},
        {"id": "l1", "number": 1, "created_at": "2026-09-24T10:00:00Z"},
    ]
    fresh = rules.new_leads(leads, SINCE, {"l3"}, None)
    assert [l["id"] for l in fresh] == ["l2"]


def test_new_leads_without_created_at_use_number_watermark():
    leads = [{"id": f"l{n}", "number": n} for n in (12, 11, 10, 9)]
    assert [l["id"] for l in rules.new_leads(leads, SINCE, set(), 10)] == ["l11", "l12"]


def test_new_leads_first_run_without_created_at_posts_only_newest():
    leads = [{"id": f"l{n}", "number": n} for n in (12, 11, 10)]
    assert [l["id"] for l in rules.new_leads(leads, SINCE, set(), None)] == ["l12"]


def test_has_photos_by_type_or_extension():
    assert rules.has_photos({"attachments": [{"file_type": "image/jpeg"}]})
    assert rules.has_photos({"attachments": [{"file_name": "IMG_01.HEIC"}]})
    assert not rules.has_photos({"attachments": [{"file_type": "application/pdf", "file_name": "invoice.pdf"}]})
    assert not rules.has_photos({})


def test_is_paid_zero_balance_including_free_visits():
    assert rules.is_paid({"total_amount": 46900, "outstanding_balance": 0})
    assert not rules.is_paid({"total_amount": 46900, "outstanding_balance": 100})
    assert rules.is_paid({"total_amount": 0, "outstanding_balance": 0})  # FREE estimate visit
    assert not rules.is_paid({"total_amount": 100})


def test_paid_by_check_ignores_voided_invoices():
    check = {"status": "succeeded", "payment_method": "external", "category": "check"}
    assert rules.paid_by_check([{"status": "paid", "payments": [check]}])
    assert not rules.paid_by_check([{"status": "voided", "payments": [check]}])
    assert not rules.paid_by_check([{"status": "paid", "payments": [{"category": None, "payment_method": "credit_card"}]}])


def test_is_canceled():
    assert rules.is_canceled({"work_status": "pro canceled"})
    assert rules.is_canceled({"work_status": "complete rated", "canceled_at": "2026-09-17T10:00:00Z"})
    assert not rules.is_canceled({"work_status": "scheduled"})


def _job(**kw):
    base = {"id": "j1", "invoice_number": "5688", "customer": {"id": "c1", "first_name": "Liz", "last_name": "Patania"},
            "work_status": "complete unrated", "attachments": [], "total_amount": 0, "outstanding_balance": 0}
    base.update(kw)
    return base


def _est(eid, created, *statuses):
    return {"id": eid, "created_at": created, "options": [{"approval_status": s} for s in statuses]}


DIAG = [{"kind": "labor", "name": "Service Call - Appliance Diagnostics", "unit_price": 9900}]
FREE_EST = [{"kind": "labor", "name": "Service Call - Electrical Installation Estimate - FREE", "unit_price": 0}]
REPAIR = [{"kind": "labor", "name": "Range Repair", "unit_price": 29900},
          {"kind": "materials", "name": "Ignitor", "unit_price": 6900},
          {"kind": "fixed discount", "name": "Discount", "unit_price": 9900}]


def line(job=None, ests=(), items=DIAG, invoices=()):
    return rules.job_line(job or _job(), list(ests), list(items), list(invoices), DAY, TZ)


def test_estimate_written_on_visit_approved():
    # 2026-09-17 20:00 PDT is 2026-09-18 03:00 UTC — still the 17th locally.
    got = line(ests=[_est("e1", "2026-09-18T03:00:00Z", "approved", None)])
    assert (got.first_label, got.first_ok, got.first_note) == ("Estimate", True, "Approved")


def test_estimate_written_next_day_still_counts_but_not_one_from_before():
    assert line(ests=[_est("e1", "2026-09-18T20:00:00Z", None)]).first_label == "Estimate"
    before = line(ests=[_est("e9", "2026-09-10T18:00:00Z", "approved")])
    assert (before.first_label, before.first_ok) == ("Diagnostic report", True)


def test_estimate_declined_and_sent():
    assert line(ests=[_est("e1", "2026-09-17T18:00:00Z", "declined", "pro declined")]).first_note == "Declined"
    sent = line(ests=[_est("e1", "2026-09-17T18:00:00Z", None)])
    assert (sent.first_ok, sent.first_note) == (True, "")


def test_repair_work_wins_over_estimate_and_needs_completion():
    # #5833: "Fix two breakers" done and paid, an estimate written too → Repair done.
    done = line(ests=[_est("e1", "2026-09-17T18:00:00Z", None)], items=REPAIR)
    assert (done.first_label, done.first_ok) == ("Repair done", True)
    # #5700-2: return visit for a repair that hasn't happened yet.
    pending = line(job=_job(work_status="scheduled"), items=REPAIR)
    assert (pending.first_label, pending.first_ok) == ("Repair done", False)


def test_origin_estimate_is_not_a_new_estimate():
    got = line(job=_job(original_estimate_id="e0"), ests=[_est("e0", "2026-09-17T18:00:00Z", "approved")],
               items=FREE_EST)
    assert (got.first_label, got.first_ok) == ("Estimate", False)


def test_diagnostic_only_visit():
    assert (line().first_label, line().first_ok) == ("Diagnostic report", True)
    assert line(job=_job(work_status="in progress")).first_ok is False


def test_free_estimate_visit_without_estimate_is_cross_but_paid():
    got = line(items=FREE_EST)
    assert (got.first_label, got.first_ok, got.paid) == ("Estimate", False, True)


def test_check_note_only_when_paid():
    inv = [{"status": "paid", "payments": [{"status": "succeeded", "category": "check"}]}]
    assert line(invoices=inv).paid_note == "check"
    assert line(job=_job(outstanding_balance=500), invoices=inv).paid_note == ""


def test_group_by_technician_shared_and_unassigned_jobs():
    ray = {"id": "p1", "first_name": "Raymond", "last_name": "T"}
    alan = {"id": "p2", "first_name": "Alan", "last_name": "B"}
    jobs = [_job(id="a", assigned_employees=[ray, alan]), _job(id="b", assigned_employees=[ray]),
            _job(id="c", assigned_employees=[])]
    reports = rules.group_by_technician(jobs)
    assert [(r.name, [j["id"] for j in r.jobs]) for r in reports] == [
        ("Alan B", ["a"]), ("Raymond T", ["a", "b"]), ("Unassigned", ["c"])]


def _cjob(num, start, status="complete rated"):
    return {"id": f"job_{num}", "invoice_number": num, "work_status": status,
            "schedule": {"scheduled_start": start}}


def test_job_for_estimate_takes_latest_visit_on_or_before_estimate_day():
    # #3093 Lauerman: old job 08-18, visit 09-28, follow-up 10-02; estimate 09-28 evening.
    est = {"created_at": "2026-09-29T02:19:00Z"}  # 09-28 19:19 PDT
    jobs = [_cjob("5546", "2026-08-18T17:00:00Z"), _cjob("5854-1", "2026-09-28T17:00:00Z"),
            _cjob("5854-2", "2026-10-02T17:00:00Z", "scheduled")]
    assert rules.job_for_estimate(est, jobs, TZ)["invoice_number"] == "5854-1"


def test_job_for_estimate_office_estimate_gets_next_job_and_skips_canceled():
    est = {"created_at": "2026-09-30T18:48:00Z"}
    jobs = [_cjob("5890", "2026-10-10T17:00:00Z", "pro canceled"),
            _cjob("5896-2", "2026-10-16T17:00:00Z", "scheduled"),
            _cjob("5896-1", "2026-10-15T17:00:00Z", "scheduled"),
            {"id": "job_x", "invoice_number": "5878", "schedule": {}}]
    assert rules.job_for_estimate(est, jobs, TZ)["invoice_number"] == "5896-1"
    assert rules.job_for_estimate(est, [], TZ) is None
