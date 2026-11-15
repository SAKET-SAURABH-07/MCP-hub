"""Dependency container shared by the gateway and tool handlers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog, connect
from .clients.github import GitHubClient
from .clients.jira import JiraClient
from .clients.slack import SlackClient
from .config import Settings
from .idempotency import IdempotencyStore
from .ratelimit import TokenBucketLimiter
from .scope import ScopePolicy


@dataclass
class Services:
    settings: Settings
    github: GitHubClient
    jira: JiraClient
    slack: SlackClient
    audit: AuditLog
    idempotency: IdempotencyStore
    limiter: TokenBucketLimiter
    scope: ScopePolicy

    @classmethod
    def from_settings(cls, s: Settings) -> Services:
        conn = connect(s.audit_db if str(s.audit_db) == ":memory:" else Path(s.audit_db))
        return cls(
            settings=s,
            github=GitHubClient.from_settings(s),
            jira=JiraClient.from_settings(s),
            slack=SlackClient.from_settings(s),
            audit=AuditLog(conn),
            idempotency=IdempotencyStore(conn, ttl=s.idempotency_ttl_seconds),
            limiter=TokenBucketLimiter(s.rate_capacity, s.rate_refill_per_sec),
            scope=ScopePolicy(s.allowed_repos, s.allowed_jira_projects, s.allowed_slack_channels),
        )

    async def aclose(self) -> None:
        await self.github.http.aclose()
        await self.jira.http.aclose()
        await self.slack.http.aclose()
