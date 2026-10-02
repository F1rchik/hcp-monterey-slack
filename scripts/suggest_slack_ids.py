"""Print a `technicians:` block for config.yaml: every HCP field tech with the best
Slack match by name. Suggestions only — check each one before pasting.

    python scripts/suggest_slack_ids.py

Monterey techs are named like "Raymond Technician Monterey" in Slack, so the first
name plus "monterey" is usually decisive; "?" marks a guess, blank means no match.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slack_sdk import WebClient  # noqa: E402

from config import load_settings  # noqa: E402
from hcp import HCPClient  # noqa: E402


def slack_people(client: WebClient) -> list[tuple[str, str]]:
    people, cursor = [], None
    while True:
        resp = client.users_list(limit=200, cursor=cursor)
        for u in resp["members"]:
            if u.get("deleted") or u.get("is_bot"):
                continue
            p = u.get("profile") or {}
            label = p.get("display_name") or p.get("real_name") or u.get("name", "")
            people.append((u["id"], label))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            return people


def best_match(first: str, last: str, people: list[tuple[str, str]]) -> tuple[str, str, bool]:
    first, last = first.lower(), last.lower()
    scored = []
    for uid, label in people:
        words = label.lower().split()
        if not first or first not in words:
            continue
        score = 1 + ("monterey" in words) * 2 + (bool(last) and last in words) * 2
        scored.append((score, uid, label))
    if not scored:
        return "", "", False
    scored.sort(reverse=True)
    top = scored[0]
    sure = top[0] >= 3 and (len(scored) == 1 or scored[1][0] < top[0])
    return top[1], top[2], sure


def main() -> int:
    s = load_settings()
    people = slack_people(WebClient(token=s.slack_bot_token))
    print("technicians:")
    for emp in HCPClient(s.hcp_api_key, s.hcp_company_id).employees():
        first, last = emp.get("first_name") or "", emp.get("last_name") or ""
        uid, label, sure = best_match(first, last, people)
        mark = "" if sure else "   # ? check" if uid else "   # no match"
        print(f'  - hcp_employee_id: "{emp["id"]}"')
        name = f"{first} {last}".strip()
        print(f'    name: "{name}"   # role: {emp.get("role")}')
        print(f'    slack_user_id: "{uid}"{mark}{"  (" + label + ")" if uid else ""}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
