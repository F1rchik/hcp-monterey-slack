"""Housecall Pro public API client (read-only).

Base URL and auth per the official spec (docs.housecallpro.com, "Housecall v1 API"):
`Authorization: Token <company API key>`. The API is only available on the MAX plan;
the key is generated in HCP → App Store → API.

List endpoints are Rails-style: array params go as `expand[]=attachments`, and every
list response carries `page`, `total_pages` and the items under a resource key.

Our key is for the franchise *parent* account; the Monterey location is selected
with the `X-Company-Id` header (location ids are listed by GET /company).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone

import requests

BASE_URL = "https://api.housecallpro.com"
PAGE_SIZE = 100
MAX_PAGES = 50  # guard against an unexpectedly huge account or a runaway loop

log = logging.getLogger(__name__)


def parse_ts(value: str | None) -> datetime | None:
    """HCP timestamps come as `2026-09-17T15:30:00Z`, sometimes without the zone.

    The spec documents them as UTC, so a naive value is read as UTC.
    """
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def iso_utc(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HCPClient:
    def __init__(self, api_key: str, company_id: str = "",
                 session: requests.Session | None = None):
        self._session = session or requests.Session()
        self._session.headers.update({
            "Authorization": f"Token {api_key}",
            "Accept": "application/json",
        })
        # The key belongs to the Fuse parent account; without this header every call
        # answers for the parent, which has no jobs or estimates of its own.
        if company_id:
            self._session.headers["X-Company-Id"] = company_id

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET with retries on rate limiting and transient server errors."""
        url = BASE_URL + path
        for attempt in range(5):
            resp = self._session.get(url, params=params, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = float(resp.headers.get("Retry-After") or 2 ** attempt)
                log.warning("HCP %s %s → %s, retrying in %.0fs", "GET", path, resp.status_code, wait)
                time.sleep(min(wait, 30))
                continue
            if resp.status_code == 401:
                raise RuntimeError(
                    "HCP rejected the API key (401). Check HCP_API_KEY — it is the company "
                    "key from HCP → App Store → API, and the account must be on the MAX plan."
                )
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        raise RuntimeError(f"HCP GET {path} kept failing with {resp.status_code}")

    def paginate(
        self,
        path: str,
        key: str,
        params: dict | None = None,
        stop: Callable[[dict], bool] | None = None,
    ) -> Iterator[dict]:
        """Yield items across pages. `stop(item)` ends the scan early, which is how
        callers walk a list sorted newest-first until they pass their time window.
        """
        params = dict(params or {})
        params.setdefault("page_size", PAGE_SIZE)
        for page in range(1, MAX_PAGES + 1):
            data = self.get(path, {**params, "page": page})
            items = data.get(key) or []
            for item in items:
                if stop and stop(item):
                    return
                yield item
            if not items or page >= int(data.get("total_pages") or 1):
                return
        log.warning("HCP %s: stopped after %d pages", path, MAX_PAGES)

    # ---- resources -----------------------------------------------------------------

    def jobs_scheduled_between(self, start: datetime, end: datetime) -> list[dict]:
        """Job segments whose scheduled start falls in [start, end), with attachments
        expanded so photo uploads can be checked without a call per job.
        """
        params = {
            "scheduled_start_min": iso_utc(start),
            "scheduled_start_max": iso_utc(end),
            "expand[]": "attachments",
            "sort_by": "invoice_number",
            "sort_direction": "asc",
        }
        jobs = []
        for job in self.paginate("/jobs", "jobs", params):
            # scheduled_start_max is inclusive; keep the window half-open.
            start_ts = parse_ts((job.get("schedule") or {}).get("scheduled_start"))
            if start_ts is None or start <= start_ts < end:
                jobs.append(job)
        return jobs

    def recent_estimates(self, created_since: datetime) -> list[dict]:
        """Estimates created since `created_since`, newest first.

        An option's approval can come weeks after the estimate was written, and the
        spec doesn't promise the estimate's updated_at moves when it does — so we
        scan by creation date over a generous window and filter on the option's own
        approval_status_updated_at afterwards.
        """
        params = {"sort_by": "created_at", "sort_direction": "desc"}
        return list(self.paginate(
            "/estimates", "estimates", params,
            stop=lambda e: _older_than(e.get("created_at"), created_since),
        ))

    def estimates_for_customer(self, customer_id: str) -> list[dict]:
        params = {"customer_id": customer_id, "sort_by": "created_at",
                  "sort_direction": "desc", "page_size": 25}
        return self.get("/estimates", params).get("estimates") or []

    def jobs_for_customer(self, customer_id: str) -> list[dict]:
        params = {"customer_id": customer_id, "sort_by": "created_at",
                  "sort_direction": "desc", "page_size": 50}
        return self.get("/jobs", params).get("jobs") or []

    def job_line_items(self, job_id: str) -> list[dict]:
        return self.get(f"/jobs/{job_id}/line_items").get("data") or []

    def job_invoices(self, job_id: str) -> list[dict]:
        data = self.get(f"/jobs/{job_id}/invoices")
        return (data.get("invoices") if isinstance(data, dict) else data) or []

    def option_line_items(self, estimate_id: str, option_id: str) -> list[dict]:
        path = f"/estimates/{estimate_id}/options/{option_id}/line_items"
        return self.get(path).get("line_items") or []

    def recent_leads(self, created_since: datetime) -> list[dict]:
        """Newest leads first.

        The spec's Lead object has no created_at (it may still come back in practice).
        Without it there is no way to know where the window ends, so only the first
        page is read and rules.new_leads falls back to the lead-number watermark.
        """
        params = {"sort_by": "created_at", "sort_direction": "desc"}
        leads: list[dict] = []
        for lead in self.paginate("/leads", "leads", params,
                                  stop=lambda l: _older_than(l.get("created_at"), created_since)):
            leads.append(lead)
            if not lead.get("created_at") and len(leads) >= PAGE_SIZE:
                break
        return leads

    def lead_line_items(self, lead_id: str) -> list[dict]:
        return self.get(f"/leads/{lead_id}/line_items").get("line_items") or []

    def job_types(self) -> dict[str, str]:
        """job type id → name (e.g. "HVAC Repair")."""
        return {t["id"]: t.get("name", "") for t in
                self.paginate("/job_fields/job_types", "job_types") if t.get("id")}

    def employees(self) -> list[dict]:
        return list(self.paginate("/employees", "employees"))


def _older_than(value: str | None, cutoff: datetime) -> bool:
    ts = parse_ts(value)
    return ts is not None and ts < cutoff
