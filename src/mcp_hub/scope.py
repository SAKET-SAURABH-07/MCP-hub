"""Scope allowlists. Independent of roles: even an admin can only touch allowlisted
repos, Jira projects and Slack channels. Empty allowlist = nothing allowed."""

from __future__ import annotations

from collections.abc import Iterable

from .errors import ErrorCode, ToolError


class ScopePolicy:
    def __init__(
        self,
        repos: Iterable[str] = (),
        jira_projects: Iterable[str] = (),
        slack_channels: Iterable[str] = (),
    ) -> None:
        self.repos = {r.lower() for r in repos}
        self.jira_projects = {p.upper() for p in jira_projects}
        self.slack_channels = set(slack_channels)

    def check_repo(self, repo: str) -> None:
        if repo.lower() not in self.repos:
            raise ToolError(ErrorCode.OUT_OF_SCOPE, f"repository {repo!r} is not allowlisted")

    def check_project(self, project: str) -> None:
        if project.upper() not in self.jira_projects:
            raise ToolError(ErrorCode.OUT_OF_SCOPE, f"Jira project {project!r} is not allowlisted")

    def check_channel(self, channel: str) -> None:
        if channel not in self.slack_channels:
            raise ToolError(ErrorCode.OUT_OF_SCOPE, f"Slack channel {channel!r} is not allowlisted")
