"""Bulk issue creation from CSV text.

Safety rules: size and row caps, strict per-row validation, validate ALL rows before
writing ANY, dry-run by default, per-row idempotency so a re-run never duplicates.
Columns: github -> title, body, labels (';'-separated); jira -> summary, description, issue_type.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .errors import ErrorCode, ToolError
from .schemas import GithubCreateIssue, JiraCreateIssue

GITHUB_COLUMNS = {"title", "body", "labels"}
JIRA_COLUMNS = {"summary", "description", "issue_type"}


@dataclass
class ParsedRows:
    rows: list[dict[str, Any]]
    errors: list[str]


def parse_csv(
    text: str, target: str, destination: str, max_rows: int, max_bytes: int
) -> ParsedRows:
    if len(text.encode()) > max_bytes:
        raise ToolError(ErrorCode.INVALID_ARGUMENTS, f"CSV larger than {max_bytes} bytes")
    required = {"title"} if target == "github" else {"summary"}
    allowed = GITHUB_COLUMNS if target == "github" else JIRA_COLUMNS
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    if not required <= fields:
        raise ToolError(ErrorCode.INVALID_ARGUMENTS, f"CSV must have column(s): {sorted(required)}")
    unknown = fields - allowed
    if unknown:
        raise ToolError(ErrorCode.INVALID_ARGUMENTS, f"unknown CSV column(s): {sorted(unknown)}")

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for n, raw in enumerate(reader, start=2):  # line 1 is the header
        if len(rows) + len(errors) >= max_rows:
            raise ToolError(ErrorCode.INVALID_ARGUMENTS, f"more than {max_rows} rows")
        try:
            if target == "github":
                labels = [x.strip() for x in (raw.get("labels") or "").split(";") if x.strip()]
                m = GithubCreateIssue(
                    repo=destination,
                    title=raw.get("title") or "",
                    body=raw.get("body") or "",
                    labels=labels,
                )
                rows.append({"title": m.title, "body": m.body, "labels": m.labels})
            else:
                j = JiraCreateIssue(
                    project=destination,
                    summary=raw.get("summary") or "",
                    description=raw.get("description") or "",
                    issue_type=raw.get("issue_type") or "Task",
                )
                rows.append(
                    {"summary": j.summary, "description": j.description, "issue_type": j.issue_type}
                )
        except ValidationError as exc:
            first = exc.errors()[0]
            errors.append(f"line {n}: {first['loc'][-1]}: {first['msg']}")
    if not rows and not errors:
        raise ToolError(ErrorCode.INVALID_ARGUMENTS, "CSV has no data rows")
    return ParsedRows(rows, errors)
