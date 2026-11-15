"""Exercise all 9 tools against YOUR REAL accounts through the gateway (RBAC, audit and
idempotency included). It CREATES real issues and posts a real Slack message in your
allowlisted sandbox, so it asks for --yes.

    python scripts/smoke_live.py --yes --repo you/sandbox --project SBX --channel C0123456789
"""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import Any

from mcp_hub.auth import Claims
from mcp_hub.config import Settings
from mcp_hub.errors import ToolError
from mcp_hub.gateway import invoke
from mcp_hub.services import Services


async def step(
    svc: Services, claims: Claims, tool: str, args: dict[str, Any]
) -> dict[str, Any] | None:
    t0 = time.perf_counter()
    try:
        out = await invoke(svc, claims, tool, args)
        print(f"PASS {tool:30s} {1000 * (time.perf_counter() - t0):7.0f} ms")
        return out
    except ToolError as exc:
        print(f"FAIL {tool:30s} {exc}")
        return None


async def run(ns: argparse.Namespace) -> int:
    settings = Settings(
        allowed_repos=[ns.repo],
        allowed_jira_projects=[ns.project],
        allowed_slack_channels=[ns.channel],
    )
    svc = Services.from_settings(settings)
    admin = Claims("smoke-admin", "smoke-admin", frozenset({"admin"}), 0)
    viewer = Claims("smoke-viewer", "smoke-viewer", frozenset({"viewer"}), 0)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    results: list[bool] = []
    try:
        r = await step(svc, admin, "github_list_issues", {"repo": ns.repo, "limit": 3})
        results.append(r is not None)
        r = await step(
            svc,
            admin,
            "github_create_issue",
            {
                "repo": ns.repo,
                "title": f"[mcp-hub smoke] {stamp}",
                "body": "created by scripts/smoke_live.py",
            },
        )
        results.append(r is not None)
        r = await step(svc, admin, "github_get_pull_request", {"repo": ns.repo, "number": ns.pr})
        results.append(r is not None)
        r = await step(svc, admin, "jira_search_issues", {"project": ns.project, "limit": 3})
        results.append(r is not None)
        j = await step(
            svc,
            admin,
            "jira_create_issue",
            {"project": ns.project, "summary": f"[mcp-hub smoke] {stamp}"},
        )
        results.append(j is not None)
        if j:
            r = await step(
                svc,
                admin,
                "jira_transition_issue",
                {"issue_key": j["key"], "transition": ns.transition},
            )
            results.append(r is not None)
        else:
            results.append(False)
        r = await step(svc, admin, "slack_list_channels", {"limit": 20})
        results.append(r is not None)
        r = await step(
            svc,
            admin,
            "slack_post_message",
            {"channel": ns.channel, "text": f"mcp-hub smoke test {stamp}"},
        )
        results.append(r is not None)
        r = await step(
            svc,
            admin,
            "bulk_create_issues_from_csv",
            {
                "target": "github",
                "destination": ns.repo,
                "csv_text": "title\nbulk dry run check",
                "dry_run": True,
            },
        )
        results.append(r is not None)
        # RBAC must hold on the real server too
        try:
            await invoke(
                svc, viewer, "github_create_issue", {"repo": ns.repo, "title": "must be denied"}
            )
            print("FAIL viewer was able to create an issue")
            results.append(False)
        except ToolError as exc:
            print(f"PASS viewer denied ({exc.code.value})")
            results.append(True)
    finally:
        await svc.aclose()
    print(f"\n{sum(results)}/{len(results)} checks passed. Audit DB: {settings.audit_db}")
    return 0 if all(results) else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="confirm you accept real writes")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--pr", type=int, default=1, help="an existing PR number in --repo")
    ap.add_argument("--transition", default="In Progress")
    ns = ap.parse_args()
    if not ns.yes:
        raise SystemExit("refusing to write to real accounts without --yes")
    raise SystemExit(asyncio.run(run(ns)))


if __name__ == "__main__":
    main()
