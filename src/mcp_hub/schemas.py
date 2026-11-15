"""Argument models. Patterns are strict on purpose: they are the first defence against
path traversal (`../`), JQL injection and malformed identifiers."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

Repo = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"),
]
ProjectKey = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,9}$")]
IssueKey = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,9}-[0-9]{1,7}$")]
ChannelId = Annotated[str, StringConstraints(pattern=r"^[CG][A-Z0-9]{8,12}$")]
IdemKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")]
Title = Annotated[str, StringConstraints(min_length=1, max_length=256, strip_whitespace=True)]
Body = Annotated[str, StringConstraints(max_length=10000)]
Label = Annotated[str, StringConstraints(min_length=1, max_length=50, pattern=r"^[^,;\n]+$")]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GithubListIssues(_Args):
    repo: Repo
    state: Literal["open", "closed", "all"] = "open"
    limit: int = Field(default=20, ge=1, le=50)


class GithubCreateIssue(_Args):
    repo: Repo
    title: Title
    body: Body = ""
    labels: list[Label] = Field(default_factory=list, max_length=10)
    idempotency_key: IdemKey | None = None


class GithubGetPullRequest(_Args):
    repo: Repo
    number: int = Field(ge=1, le=10_000_000)


class JiraSearchIssues(_Args):
    """Structured search (not raw JQL) so the server controls the query and its scope."""

    project: ProjectKey
    text: Annotated[str, StringConstraints(max_length=200)] = ""
    status: Annotated[str, StringConstraints(max_length=50, pattern=r"^[A-Za-z0-9 _-]*$")] = ""
    limit: int = Field(default=20, ge=1, le=50)


class JiraCreateIssue(_Args):
    project: ProjectKey
    summary: Title
    description: Body = ""
    issue_type: Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z -]{0,29}$")] = "Task"
    idempotency_key: IdemKey | None = None


class JiraTransitionIssue(_Args):
    issue_key: IssueKey
    transition: Annotated[str, StringConstraints(min_length=1, max_length=50)]
    idempotency_key: IdemKey | None = None


class SlackListChannels(_Args):
    limit: int = Field(default=50, ge=1, le=200)


class SlackPostMessage(_Args):
    channel: ChannelId
    text: Annotated[str, StringConstraints(min_length=1, max_length=3000)]
    idempotency_key: IdemKey | None = None


class BulkCreateIssuesFromCsv(_Args):
    target: Literal["github", "jira"]
    destination: Annotated[str, StringConstraints(min_length=2, max_length=200)]
    csv_text: Annotated[str, StringConstraints(min_length=1)]
    dry_run: bool = True

    @model_validator(mode="after")
    def _destination_matches_target(self) -> BulkCreateIssuesFromCsv:
        # reuse the strict single-item patterns so "../x" or "a b" never reach a handler
        TypeAdapter(Repo if self.target == "github" else ProjectKey).validate_python(
            self.destination
        )
        return self
