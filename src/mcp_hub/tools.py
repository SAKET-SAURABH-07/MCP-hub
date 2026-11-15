"""Tool registry: name -> (arg model, handler, write?). The gateway is the only caller."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from . import schemas as s
from .auth import Claims
from .bulk import parse_csv
from .errors import ErrorCode, ToolError
from .idempotency import make_key
from .services import Services

Handler = Callable[[Services, Claims, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    model: type[BaseModel]
    handler: Handler
    write: bool = False
    idempotent: bool = False  # gateway-level idempotency (bulk does it per row instead)


async def github_list_issues(svc: Services, _c: Claims, a: s.GithubListIssues) -> dict[str, Any]:
    svc.scope.check_repo(a.repo)
    return {"repo": a.repo, "issues": await svc.github.list_issues(a.repo, a.state, a.limit)}


async def github_create_issue(svc: Services, _c: Claims, a: s.GithubCreateIssue) -> dict[str, Any]:
    svc.scope.check_repo(a.repo)
    return await svc.github.create_issue(a.repo, a.title, a.body, a.labels)


async def github_get_pull_request(
    svc: Services, _c: Claims, a: s.GithubGetPullRequest
) -> dict[str, Any]:
    svc.scope.check_repo(a.repo)
    return await svc.github.get_pull_request(a.repo, a.number)


async def jira_search_issues(svc: Services, _c: Claims, a: s.JiraSearchIssues) -> dict[str, Any]:
    svc.scope.check_project(a.project)
    return {"issues": await svc.jira.search(a.project, a.text, a.status, a.limit)}


async def jira_create_issue(svc: Services, _c: Claims, a: s.JiraCreateIssue) -> dict[str, Any]:
    svc.scope.check_project(a.project)
    return await svc.jira.create_issue(a.project, a.summary, a.description, a.issue_type)


async def jira_transition_issue(
    svc: Services, _c: Claims, a: s.JiraTransitionIssue
) -> dict[str, Any]:
    svc.scope.check_project(a.issue_key.rsplit("-", 1)[0])
    return await svc.jira.transition(a.issue_key, a.transition)


async def slack_list_channels(svc: Services, _c: Claims, a: s.SlackListChannels) -> dict[str, Any]:
    channels = await svc.slack.list_channels(a.limit)
    # only reveal channels the policy allows
    return {"channels": [c for c in channels if c["id"] in svc.scope.slack_channels]}


async def slack_post_message(svc: Services, _c: Claims, a: s.SlackPostMessage) -> dict[str, Any]:
    svc.scope.check_channel(a.channel)
    return await svc.slack.post_message(a.channel, a.text)


async def bulk_create_issues_from_csv(
    svc: Services, claims: Claims, a: s.BulkCreateIssuesFromCsv
) -> dict[str, Any]:
    if a.target == "github":
        svc.scope.check_repo(a.destination)
    else:
        svc.scope.check_project(a.destination)

    cfg = svc.settings
    parsed = parse_csv(a.csv_text, a.target, a.destination, cfg.bulk_max_rows, cfg.bulk_max_bytes)
    if parsed.errors:  # all-or-nothing validation: write nothing if any row is bad
        return {"dry_run": a.dry_run, "created": [], "validation_errors": parsed.errors}
    if a.dry_run:
        return {"dry_run": True, "would_create": len(parsed.rows), "created": [], "failed": []}

    # The gateway charged one token for this call, but a real run makes one upstream write
    # per row. Charge the remainder up front, or a single admin call would walk straight
    # past the rate limit it is supposed to be bound by.
    if len(parsed.rows) > 1:
        ok, retry_after = svc.limiter.try_acquire(claims.subject, cost=float(len(parsed.rows) - 1))
        if not ok:
            raise ToolError(
                ErrorCode.RATE_LIMITED,
                f"{len(parsed.rows)} rows exceeds the remaining quota;"
                f" retry in {retry_after:.0f}s or split the file",
            )

    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for i, row in enumerate(parsed.rows, start=1):
        key = make_key(claims.subject, f"bulk:{a.target}:{a.destination}", row, None)
        outcome, prior = svc.idempotency.begin(key)
        if outcome == "done" and prior is not None:
            created.append({**prior, "row": i, "replayed": True})
            continue
        if outcome == "pending":
            failed.append({"row": i, "error": ErrorCode.DUPLICATE_IN_PROGRESS.value})
            continue
        try:
            if a.target == "github":
                res = await svc.github.create_issue(
                    a.destination, row["title"], row["body"], row["labels"]
                )
            else:
                res = await svc.jira.create_issue(
                    a.destination, row["summary"], row["description"], row["issue_type"]
                )
        except ToolError as exc:
            svc.idempotency.abort(key)
            failed.append({"row": i, "error": exc.code.value})
            continue
        svc.idempotency.complete(key, res)
        created.append({**res, "row": i})
    return {"dry_run": False, "created": created, "failed": failed}


TOOLS: dict[str, ToolSpec] = {
    t.name: t
    for t in [
        ToolSpec(
            "github_list_issues",
            "List issues in an allowlisted GitHub repository.",
            s.GithubListIssues,
            github_list_issues,
        ),
        ToolSpec(
            "github_create_issue",
            "Create an issue in an allowlisted GitHub repository.",
            s.GithubCreateIssue,
            github_create_issue,
            write=True,
            idempotent=True,
        ),
        ToolSpec(
            "github_get_pull_request",
            "Get summary details of a pull request.",
            s.GithubGetPullRequest,
            github_get_pull_request,
        ),
        ToolSpec(
            "jira_search_issues",
            "Search issues in an allowlisted Jira project.",
            s.JiraSearchIssues,
            jira_search_issues,
        ),
        ToolSpec(
            "jira_create_issue",
            "Create an issue in an allowlisted Jira project.",
            s.JiraCreateIssue,
            jira_create_issue,
            write=True,
            idempotent=True,
        ),
        ToolSpec(
            "jira_transition_issue",
            "Move a Jira issue to another workflow status.",
            s.JiraTransitionIssue,
            jira_transition_issue,
            write=True,
            idempotent=True,
        ),
        ToolSpec(
            "slack_list_channels",
            "List allowlisted Slack channels.",
            s.SlackListChannels,
            slack_list_channels,
        ),
        ToolSpec(
            "slack_post_message",
            "Post a message to an allowlisted Slack channel.",
            s.SlackPostMessage,
            slack_post_message,
            write=True,
            idempotent=True,
        ),
        ToolSpec(
            "bulk_create_issues_from_csv",
            "Admin only. Create many GitHub or Jira issues from CSV text (dry run by default).",
            s.BulkCreateIssuesFromCsv,
            bulk_create_issues_from_csv,
            write=True,
        ),
    ]
}
