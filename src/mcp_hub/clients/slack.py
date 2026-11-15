from __future__ import annotations

from typing import Any

from ..config import Settings
from ..errors import ErrorCode, ToolError
from .http import UpstreamClient

_AUTH_ERRORS = {"invalid_auth", "not_authed", "token_revoked", "account_inactive", "missing_scope"}


def _check(data: dict[str, Any]) -> dict[str, Any]:
    """Slack returns HTTP 200 with ok=false on errors."""
    if data.get("ok"):
        return data
    err = str(data.get("error", "unknown_error"))
    if err in _AUTH_ERRORS:
        raise ToolError(ErrorCode.UPSTREAM_AUTH, f"slack: {err}")
    if err in {"channel_not_found", "not_in_channel"}:
        raise ToolError(ErrorCode.UPSTREAM_NOT_FOUND, f"slack: {err}")
    if err == "ratelimited":
        raise ToolError(ErrorCode.UPSTREAM_RATE_LIMITED, "slack: ratelimited")
    raise ToolError(ErrorCode.UPSTREAM_ERROR, f"slack: {err}")


class SlackClient:
    def __init__(self, http: UpstreamClient) -> None:
        self.http = http

    @classmethod
    def from_settings(cls, s: Settings) -> SlackClient:
        headers = {"Authorization": f"Bearer {s.slack_bot_token}"} if s.slack_bot_token else {}
        return cls(
            UpstreamClient(s.slack_api, headers, timeout=s.http_timeout, retries=s.http_retries)
        )

    async def list_channels(self, limit: int) -> list[dict[str, Any]]:
        data = _check(
            await self.http.request(
                "GET",
                "/conversations.list",
                params={"limit": limit, "exclude_archived": "true", "types": "public_channel"},
            )
        )
        return [
            {
                "id": c["id"],
                "name": c["name"],
                "members": c.get("num_members"),
                "topic": (c.get("topic") or {}).get("value", ""),
            }
            for c in data.get("channels", [])
        ]

    async def post_message(self, channel: str, text: str) -> dict[str, Any]:
        data = _check(
            await self.http.request(
                "POST", "/chat.postMessage", json={"channel": channel, "text": text}
            )
        )
        return {"channel": data.get("channel", channel), "ts": data.get("ts")}
