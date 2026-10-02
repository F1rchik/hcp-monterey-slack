"""Settings: secrets from the environment (.env locally, GitHub Secrets in CI),
everything else from config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
CONFIG_FILE = ROOT / "config.yaml"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Minimal .env loader. The real environment always wins over the file."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _env(name: str) -> str:
    # .strip() guards against stray spaces/newlines pasted into GitHub secrets.
    return os.environ.get(name, "").strip()


def _flag(name: str) -> bool:
    return _env(name).lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    hcp_api_key: str
    hcp_company_id: str
    slack_bot_token: str
    dry_run: bool
    # Test mode: every message goes here instead of the real channel.
    test_channel: str
    tz: ZoneInfo
    channels: dict[str, str]
    links: dict[str, str]
    lookback_hours: float
    estimate_max_age_days: int
    post_after: time
    lead_sources: list[dict]
    default_lead_source: dict
    slack_ids: dict[str, str]   # hcp employee id → slack member id

    def channel(self, kind: str) -> str:
        if self.test_channel:
            return self.test_channel
        channel = self.channels.get(kind, "")
        if not channel and not self.dry_run:
            raise RuntimeError(f"channels.{kind} is empty in config.yaml.")
        return channel


def load_settings() -> Settings:
    load_env_file()
    cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}

    api_key = _env("HCP_API_KEY")
    if not api_key:
        raise RuntimeError("HCP_API_KEY is not set. Locally put it in .env, in CI in GitHub Secrets.")

    dry_run = _flag("DRY_RUN")
    token = _env("SLACK_BOT_TOKEN")
    if not token and not dry_run:
        raise RuntimeError("SLACK_BOT_TOKEN is not set (or run with DRY_RUN=1).")

    live = cfg.get("live") or {}
    daily = cfg.get("daily") or {}
    return Settings(
        hcp_api_key=api_key,
        hcp_company_id=str(cfg.get("hcp_company_id") or "").strip(),
        slack_bot_token=token,
        dry_run=dry_run,
        test_channel=_env("SLACK_TEST_CHANNEL"),
        tz=ZoneInfo(cfg.get("timezone") or "America/Los_Angeles"),
        channels={k: str(v or "").strip() for k, v in (cfg.get("channels") or {}).items()},
        links=cfg.get("links") or {},
        lookback_hours=float(live.get("lookback_hours", 6)),
        estimate_max_age_days=int(live.get("estimate_max_age_days", 60)),
        post_after=time.fromisoformat(str(daily.get("post_after", "07:00"))),
        lead_sources=cfg.get("lead_sources") or [],
        default_lead_source=cfg.get("default_lead_source") or {"label": "HCP", "emoji": ""},
        slack_ids={str(t["hcp_employee_id"]): str(t["slack_user_id"])
                   for t in cfg.get("technicians") or []
                   if t.get("hcp_employee_id") and t.get("slack_user_id")},
    )
