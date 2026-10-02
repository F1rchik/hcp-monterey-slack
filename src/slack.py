"""Posting, and remembering what was posted.

There is no database: every bot message carries Slack message metadata
(`event_type` + the HCP id), and each run reads the channel history back to see
what has already gone out. That keeps repeats impossible even when two GitHub
runs overlap a time window, with nothing extra to host.

Bot scopes: chat:write, groups:history (the three channels are private),
channels:history (in case one is made public), users:read (suggest_slack_ids.py).
"""

from __future__ import annotations

import logging
from datetime import datetime

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

log = logging.getLogger(__name__)

APPROVED_ESTIMATE = "hcp_estimate_approved"
NEW_LEAD = "hcp_new_lead"
DAILY_JOBS = "hcp_daily_jobs"


class Slack:
    def __init__(self, token: str):
        self._client = WebClient(token=token)

    def posted(self, channel: str, event_type: str, since: datetime) -> list[dict]:
        """Metadata payloads of our own messages of `event_type` since `since`."""
        payloads, cursor = [], None
        while True:
            try:
                resp = self._client.conversations_history(
                    channel=channel, oldest=f"{since.timestamp():.6f}", limit=200,
                    include_all_metadata=True, cursor=cursor,
                )
            except SlackApiError as e:
                raise RuntimeError(_explain(e, channel)) from e
            for msg in resp.get("messages", []):
                meta = msg.get("metadata") or {}
                if meta.get("event_type") == event_type:
                    payloads.append(meta.get("event_payload") or {})
            cursor = (resp.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return payloads

    def post(self, channel: str, text: str, blocks: list[dict],
             event_type: str, payload: dict[str, str]) -> str:
        try:
            resp = self._client.chat_postMessage(
                channel=channel, text=text, blocks=blocks,
                metadata={"event_type": event_type, "event_payload": payload},
                unfurl_links=False, unfurl_media=False,
            )
        except SlackApiError as e:
            raise RuntimeError(_explain(e, channel)) from e
        return resp["ts"]


def _explain(e: SlackApiError, channel: str) -> str:
    err = e.response.get("error")
    hint = {
        "channel_not_found": " — the bot is not in this private channel (/invite it) "
                             "or the channel id is wrong.",
        "not_in_channel": " — invite the bot into the channel.",
        "missing_scope": f" — the token lacks a scope (needed: {e.response.get('needed')}); "
                         "add it and reinstall the app.",
    }.get(err, "")
    return f"Slack API error in {channel}: {err}{hint}"
