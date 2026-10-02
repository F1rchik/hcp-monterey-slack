"""Dump real HCP payloads to data/ (gitignored: customer names and phones).

Run once the API key is in .env, before trusting the rules in src/rules.py:

    python scripts/inspect_hcp.py

and check in the printed summary:
- which approval_status strings estimate options really use;
- whether leads come back with created_at, and what lead_source looks like
  for Yelp / Thumbtack / Google / eLocal leads;
- whether yesterday's jobs carry attachments, and their file_type values.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import ROOT, load_settings  # noqa: E402
from hcp import HCPClient  # noqa: E402

OUT = ROOT / "data"


def dump(name: str, data) -> None:
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}")


def main() -> int:
    s = load_settings()
    hcp = HCPClient(s.hcp_api_key, s.hcp_company_id)

    print("Company:")
    company = hcp.get("/company")
    print(f"  {company.get('name')} ({company.get('id')})")

    print("Estimates (newest 50):")
    est = hcp.get("/estimates", {"sort_by": "created_at", "sort_direction": "desc", "page_size": 50})
    dump("estimates", est)
    statuses = Counter(o.get("approval_status") for e in est.get("estimates", []) for o in e.get("options", []))
    print(f"  option approval_status values: {dict(statuses)}")

    print("Leads (newest 50):")
    leads = hcp.get("/leads", {"sort_by": "created_at", "sort_direction": "desc", "page_size": 50})
    dump("leads", leads)
    items = leads.get("leads", [])
    print(f"  have created_at: {sum(bool(l.get('created_at')) for l in items)}/{len(items)}")
    print(f"  lead_source values: {dict(Counter(l.get('lead_source') for l in items))}")

    print("Yesterday's jobs:")
    day = datetime.now(s.tz).date() - timedelta(days=1)
    start = datetime.combine(day, time.min, tzinfo=s.tz)
    jobs = hcp.jobs_scheduled_between(start, start + timedelta(days=1))
    dump("jobs_yesterday", jobs)
    print(f"  {len(jobs)} jobs on {day}")
    print(f"  work_status: {dict(Counter(j.get('work_status') for j in jobs))}")
    print(f"  attachment file_type: {dict(Counter(a.get('file_type') for j in jobs for a in j.get('attachments') or []))}")
    print(f"  with original_estimate_id: {sum(bool(j.get('original_estimate_id')) for j in jobs)}")

    print("Employees:")
    dump("employees", hcp.employees())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
