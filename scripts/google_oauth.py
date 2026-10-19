"""One-time: get a Google Ads API refresh token for the GLS integration.

Needs GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET in .env (OAuth client of
type "Desktop app"). Opens the browser; sign in with the Google account that can
see Monterey's Local Services account, allow access, and the refresh token is
written into .env as GOOGLE_ADS_REFRESH_TOKEN (never printed).

    python scripts/google_oauth.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from config import ENV_FILE, load_env_file  # noqa: E402

SCOPE = "https://www.googleapis.com/auth/adwords"


def main() -> int:
    load_env_file()
    client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        print("Set GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET in .env first.")
        return 1
    flow = InstalledAppFlow.from_client_config(
        {"installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }},
        scopes=[SCOPE],
    )
    # prompt=consent forces Google to return a refresh token even on a repeat run.
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        print("Google returned no refresh token. Remove the app's access at "
              "myaccount.google.com/permissions and run this again.")
        return 1
    save_to_env("GOOGLE_ADS_REFRESH_TOKEN", creds.refresh_token)
    print(f"\nSaved GOOGLE_ADS_REFRESH_TOKEN to .env "
          f"({len(creds.refresh_token)} chars, ends …{creds.refresh_token[-4:]}).")
    return 0


def save_to_env(key: str, value: str) -> None:
    """Write the token into .env instead of printing it, so it never ends up in
    a terminal log or chat."""
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [l for l in lines if not l.strip().startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
