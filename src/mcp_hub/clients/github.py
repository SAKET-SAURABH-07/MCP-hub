from __future__ import annotations

from typing import Any

from ..config import Settings
from .http import UpstreamClient


class GitHubClient:
    def __init__(self, http: UpstreamClient) -> None:
        self.http = http

    @classmethod
    def from_settings(cls, s: Settings) -> GitHubClient:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mcp-hub",
        }
        if s.github_token:
            headers["Authorization"] = f"Bearer {s.github_token}"
        return cls(
            UpstreamClient(s.github_api, headers, timeout=s.http_timeout, retries=s.http_retries)
        )

    async def list_issues(self, repo: str, state: str, limit: int) -> list[dict[str, Any]]:
        data = await self.http.request(
            "GET", f"/repos/{repo}/issues", params={"state": state, "per_page": min(limit * 2, 100)}
        )
        issues = [i for i in data if "pull_request" not in i]  # the API mixes PRs in
        return [
            {
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "url": i["html_url"],
                "labels": [lab["name"] for lab in i.get("labels", [])],
                "author": (i.get("user") or {}).get("login"),
            }
            for i in issues[:limit]
        ]

    async def create_issue(
        self, repo: str, title: str, body: str, labels: list[str]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        i = await self.http.request("POST", f"/repos/{repo}/issues", json=payload)
        return {"number": i["number"], "url": i["html_url"], "title": i["title"]}

    async def get_pull_request(self, repo: str, number: int) -> dict[str, Any]:
        p = await self.http.request("GET", f"/repos/{repo}/pulls/{number}")
        return {
            "number": p["number"],
            "title": p["title"],
            "state": p["state"],
            "merged": p.get("merged", False),
            "draft": p.get("draft", False),
            "author": (p.get("user") or {}).get("login"),
            "base": p["base"]["ref"],
            "head": p["head"]["ref"],
            "additions": p.get("additions"),
            "deletions": p.get("deletions"),
            "changed_files": p.get("changed_files"),
            "url": p["html_url"],
        }
