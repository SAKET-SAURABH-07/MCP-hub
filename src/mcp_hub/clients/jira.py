from __future__ import annotations

from typing import Any

from ..config import Settings
from ..errors import ErrorCode, ToolError
from .http import UpstreamClient


def _jql_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_jql(project: str, text: str, status: str) -> str:
    parts = [f"project = {_jql_quote(project)}"]
    if text:
        parts.append(f"text ~ {_jql_quote(text)}")
    if status:
        parts.append(f"status = {_jql_quote(status)}")
    return " AND ".join(parts) + " ORDER BY created DESC"


def adf(text: str) -> dict[str, Any]:
    """Atlassian Document Format: Jira Cloud v3 requires it for rich text fields."""
    paras = [p for p in text.split("\n\n") if p.strip()] or [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}] if p else []}
            for p in paras
        ],
    }


class JiraClient:
    def __init__(self, http: UpstreamClient, base_url: str) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_settings(cls, s: Settings) -> JiraClient:
        auth = (s.jira_email, s.jira_api_token) if s.jira_email else None
        http = UpstreamClient(
            s.jira_base_url or "https://invalid.example",
            {"Accept": "application/json"},
            timeout=s.http_timeout,
            retries=s.http_retries,
            auth=auth,
        )
        return cls(http, s.jira_base_url)

    async def search(
        self, project: str, text: str, status: str, limit: int
    ) -> list[dict[str, Any]]:
        # /rest/api/3/search was removed; /search/jql is token-paginated and needs bounded JQL
        data = await self.http.request(
            "GET",
            "/rest/api/3/search/jql",
            params={
                "jql": build_jql(project, text, status),
                "maxResults": limit,
                "fields": "summary,status,assignee,issuetype,created",
            },
        )
        return [
            {
                "key": i["key"],
                "summary": i["fields"].get("summary"),
                "status": (i["fields"].get("status") or {}).get("name"),
                "type": (i["fields"].get("issuetype") or {}).get("name"),
                "assignee": (i["fields"].get("assignee") or {}).get("displayName"),
                "url": f"{self.base_url}/browse/{i['key']}",
            }
            for i in data.get("issues", [])[:limit]
        ]

    async def create_issue(
        self, project: str, summary: str, description: str, issue_type: str
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "project": {"key": project},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = adf(description)
        i = await self.http.request("POST", "/rest/api/3/issue", json={"fields": fields})
        return {"key": i["key"], "url": f"{self.base_url}/browse/{i['key']}"}

    async def transition(self, issue_key: str, transition: str) -> dict[str, Any]:
        data = await self.http.request("GET", f"/rest/api/3/issue/{issue_key}/transitions")
        match = next(
            (
                t
                for t in data.get("transitions", [])
                if t["name"].lower() == transition.lower() or t["id"] == transition
            ),
            None,
        )
        if match is None:
            names = ", ".join(t["name"] for t in data.get("transitions", []))
            raise ToolError(
                ErrorCode.INVALID_ARGUMENTS,
                f"transition {transition!r} not available; available: {names or 'none'}",
            )
        await self.http.request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/transitions",
            json={"transition": {"id": match["id"]}},
        )
        return {"key": issue_key, "transitioned_to": match["name"]}
