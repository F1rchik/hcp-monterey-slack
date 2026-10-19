"""Check Google Local Services (GLS) API access and dump recent leads to data/.

Read-only: nothing is created in HCP or posted to Slack. Run after .env has:

    GOOGLE_ADS_DEVELOPER_TOKEN   API Center of the manager (MCC) account
    GOOGLE_ADS_CLIENT_ID         OAuth client, type "Desktop app"
    GOOGLE_ADS_CLIENT_SECRET
    GOOGLE_ADS_REFRESH_TOKEN     from scripts/google_oauth.py
    GOOGLE_ADS_LOGIN_CUSTOMER_ID manager account id, digits only (blank if none)
    GLS_CUSTOMER_ID              Monterey's Local Services account id, digits only

    python scripts/inspect_gls.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import ROOT, load_env_file  # noqa: E402

API_VERSION = "v25"  # newest listed by Google in Oct 2026; bump when it's sunset
API = f"https://googleads.googleapis.com/{API_VERSION}"

LEADS_QUERY = """
SELECT
  local_services_lead.id,
  local_services_lead.lead_type,
  local_services_lead.lead_status,
  local_services_lead.category_id,
  local_services_lead.service_id,
  local_services_lead.contact_details,
  local_services_lead.creation_date_time,
  local_services_lead.locale,
  local_services_lead.lead_charged
FROM local_services_lead
ORDER BY local_services_lead.creation_date_time DESC
LIMIT 50
"""


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip().replace("-", "") if "CUSTOMER_ID" in name \
        else os.environ.get(name, "").strip()
    if required and not value:
        sys.exit(f"Missing {name} in .env")
    return value


def access_token() -> str:
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": env("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": env("GOOGLE_ADS_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    }, timeout=30)
    if resp.status_code != 200:
        sys.exit(f"OAuth failed ({resp.status_code}): {resp.text[:300]}\n"
                 "Re-run scripts/google_oauth.py; in 'Testing' mode tokens expire after 7 days.")
    return resp.json()["access_token"]


def search(customer_id: str, query: str, headers: dict) -> list[dict]:
    rows, page_token = [], None
    while True:
        body = {"query": query}
        if page_token:
            body["pageToken"] = page_token
        resp = requests.post(f"{API}/customers/{customer_id}/googleAds:search",
                             headers=headers, json=body, timeout=60)
        if resp.status_code != 200:
            explain(resp)
        data = resp.json()
        rows += data.get("results", [])
        page_token = data.get("nextPageToken")
        if not page_token:
            return rows


def explain(resp: requests.Response) -> None:
    text = resp.text
    hints = {
        "DEVELOPER_TOKEN_NOT_APPROVED": "The developer token only has test access. "
            "Apply for Basic access in the MCC: Admin → API Center.",
        "DEVELOPER_TOKEN_PROHIBITED": "Developer token is not valid for this Google Cloud project.",
        "USER_PERMISSION_DENIED": "The signed-in Google account can't access GLS_CUSTOMER_ID, "
            "or GOOGLE_ADS_LOGIN_CUSTOMER_ID is wrong/missing.",
        "CUSTOMER_NOT_FOUND": "GLS_CUSTOMER_ID is wrong.",
        "NOT_ADS_USER": "The signed-in Google account has no Google Ads access.",
        "SERVICE_DISABLED": "Enable 'Google Ads API' in the Google Cloud project.",
    }
    hint = next((h for code, h in hints.items() if code in text), "")
    sys.exit(f"Google Ads API error {resp.status_code}:\n{text[:1500]}\n\n{hint}")


def main() -> int:
    load_env_file()
    customer = env("GLS_CUSTOMER_ID")
    headers = {
        "Authorization": f"Bearer {access_token()}",
        "developer-token": env("GOOGLE_ADS_DEVELOPER_TOKEN"),
    }
    login = env("GOOGLE_ADS_LOGIN_CUSTOMER_ID", required=False)
    if login:
        headers["login-customer-id"] = login

    print("OAuth: OK")
    campaigns = search(customer, "SELECT campaign.id, campaign.name, campaign.status "
                       "FROM campaign WHERE campaign.advertising_channel_type = 'LOCAL_SERVICES'",
                       headers)
    print(f"Local Services campaigns in {customer}: "
          f"{[(c['campaign']['name'], c['campaign']['status']) for c in campaigns] or 'NONE'}")

    rows = search(customer, LEADS_QUERY, headers)
    leads = [r["localServicesLead"] for r in rows]
    out = ROOT / "data"
    out.mkdir(exist_ok=True)
    (out / "gls_leads.json").write_text(json.dumps(leads, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Leads: {len(leads)} newest written to data/gls_leads.json")
    print(f"  types:    {dict(Counter(l.get('leadType') for l in leads))}")
    print(f"  statuses: {dict(Counter(l.get('leadStatus') for l in leads))}")
    for lead in leads[:5]:
        c = lead.get("contactDetails") or {}
        print(f"  {lead.get('creationDateTime')}  {lead.get('leadType'):8}  "
              f"{c.get('consumerName', '—')}  {c.get('phoneNumber', '—')}  service={lead.get('serviceId')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
