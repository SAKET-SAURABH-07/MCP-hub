"""Offline eval: runs a fixed set of security/behaviour cases through the real gateway with
mocked upstream APIs and reports a pass rate per category.

    python evals/run_evals.py            # prints a report, writes evals/results.json
Exit code is non-zero if any case fails.

Cases = every (role x tool) authorization cell (generated) + evals/cases.json (hand-written
scope, validation, injection and redaction cases).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import respx

from mcp_hub.auth import Claims
from mcp_hub.config import Settings
from mcp_hub.errors import ToolError
from mcp_hub.gateway import invoke
from mcp_hub.rbac import ROLE_TOOLS
from mcp_hub.services import Services

HERE = Path(__file__).parent
REPO, PROJECT, CHANNEL = "acme/app", "SBX", "C0123456789"

SAMPLE_ARGS: dict[str, dict[str, Any]] = {
    "github_list_issues": {"repo": REPO},
    "github_create_issue": {"repo": REPO, "title": "t"},
    "github_get_pull_request": {"repo": REPO, "number": 1},
    "jira_search_issues": {"project": PROJECT},
    "jira_create_issue": {"project": PROJECT, "summary": "s"},
    "jira_transition_issue": {"issue_key": "SBX-1", "transition": "Done"},
    "slack_list_channels": {},
    "slack_post_message": {"channel": CHANNEL, "text": "hi"},
    "bulk_create_issues_from_csv": {
        "target": "github",
        "destination": REPO,
        "csv_text": "title\nx",
    },
}


@dataclass
class Case:
    id: str
    category: str
    role: str
    tool: str
    args: dict[str, Any]
    expect: str
    forbidden_in_audit: list[str] = field(default_factory=list)
    request_must_not_contain: list[str] = field(default_factory=list)


def load_cases() -> list[Case]:
    cases = [
        Case(
            id=f"authz-{role}-{tool}",
            category="authz",
            role=role,
            tool=tool,
            args=SAMPLE_ARGS[tool],
            expect="ok" if tool in ROLE_TOOLS[role] else "FORBIDDEN",
        )
        for role in ROLE_TOOLS
        for tool in sorted(SAMPLE_ARGS)
    ]
    cases.append(
        Case(
            "authz-norole-read",
            "authz",
            "none",
            "github_list_issues",
            SAMPLE_ARGS["github_list_issues"],
            "FORBIDDEN",
        )
    )
    for raw in json.loads((HERE / "cases.json").read_text()):
        args = {k: ("a" * 10001 if v == "@@HUGE@@" else v) for k, v in raw["args"].items()}
        cases.append(Case(**{**raw, "args": args}))
    return cases


def mock_upstreams(router: respx.MockRouter) -> None:
    router.get(f"https://api.github.com/repos/{REPO}/issues").mock(
        return_value=httpx.Response(200, json=[])
    )
    router.post(f"https://api.github.com/repos/{REPO}/issues").mock(
        return_value=httpx.Response(201, json={"number": 1, "html_url": "u", "title": "t"})
    )
    router.get(f"https://api.github.com/repos/{REPO}/pulls/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 1,
                "title": "p",
                "state": "open",
                "html_url": "u",
                "base": {"ref": "m"},
                "head": {"ref": "h"},
            },
        )
    )
    router.get("https://jira.test/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": []})
    )
    router.post("https://jira.test/rest/api/3/issue").mock(
        return_value=httpx.Response(201, json={"key": "SBX-1"})
    )
    router.get("https://jira.test/rest/api/3/issue/SBX-1/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": "Done"}]})
    )
    router.post("https://jira.test/rest/api/3/issue/SBX-1/transitions").mock(
        return_value=httpx.Response(204)
    )
    router.get("https://slack.com/api/conversations.list").mock(
        return_value=httpx.Response(200, json={"ok": True, "channels": []})
    )
    router.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "channel": CHANNEL, "ts": "1"})
    )


async def run_case(svc: Services, router: respx.MockRouter, case: Case) -> tuple[bool, str]:
    roles = frozenset() if case.role == "none" else frozenset({case.role})
    claims = Claims(f"eval-{case.id}", f"eval-{case.id}", roles, 0)
    router.calls.reset()
    got = "ok"
    try:
        await invoke(svc, claims, case.tool, case.args)
    except ToolError as exc:
        got = exc.code.value
    problems: list[str] = []
    if got != case.expect:
        problems.append(f"expected {case.expect}, got {got}")
    if case.forbidden_in_audit:
        dump = json.dumps(svc.audit.rows(5))
        problems += [
            f"secret leaked to audit: {s[:12]}…" for s in case.forbidden_in_audit if s in dump
        ]
    for needle in case.request_must_not_contain:
        sent = " ".join(str(c.request.url) for c in router.calls)
        if needle in sent:
            problems.append(f"upstream request contained {needle!r}")
    return (not problems), "; ".join(problems)


async def run_all() -> dict[str, Any]:
    settings = Settings(
        _env_file=None,
        audit_db=":memory:",
        jira_base_url="https://jira.test",
        jira_email="e@x.test",
        jira_api_token="t",
        github_token="g",
        slack_bot_token="s",
        allowed_repos=[REPO],
        allowed_jira_projects=[PROJECT],
        allowed_slack_channels=[CHANNEL],
        http_retries=0,
        rate_capacity=10_000,
        rate_refill_per_sec=10_000,
    )
    svc = Services.from_settings(settings)
    results: list[dict[str, Any]] = []
    try:
        with respx.mock(assert_all_called=False) as router:
            mock_upstreams(router)
            for case in load_cases():
                ok, why = await run_case(svc, router, case)
                results.append({"id": case.id, "category": case.category, "passed": ok, "why": why})
    finally:
        await svc.aclose()
    cats: dict[str, dict[str, int]] = {}
    for r in results:
        c = cats.setdefault(r["category"], {"total": 0, "passed": 0})
        c["total"] += 1
        c["passed"] += r["passed"]
    return {
        "total": len(results),
        "passed": sum(r["passed"] for r in results),
        "by_category": cats,
        "failures": [r for r in results if not r["passed"]],
    }


def main() -> None:
    import asyncio

    report = asyncio.run(run_all())
    (HERE / "results.json").write_text(json.dumps(report, indent=2))
    print(f"eval: {report['passed']}/{report['total']} passed")
    for cat, v in sorted(report["by_category"].items()):
        print(f"  {cat:11s} {v['passed']}/{v['total']}")
    for f in report["failures"]:
        print(f"  FAIL {f['id']}: {f['why']}")
    sys.exit(0 if not report["failures"] else 1)


if __name__ == "__main__":
    main()
