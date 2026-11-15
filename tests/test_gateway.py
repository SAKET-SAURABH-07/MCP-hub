from __future__ import annotations

import json

import httpx
import pytest
import respx

from mcp_hub.errors import ErrorCode, ToolError
from mcp_hub.gateway import invoke
from mcp_hub.services import Services

from .conftest import CHANNEL, PROJECT, REPO, claims_for

GH = "https://api.github.com"
ISSUES = f"{GH}/repos/{REPO}/issues"


async def err(svc: Services, claims, tool: str, args: dict) -> ErrorCode:  # type: ignore[no-untyped-def]
    with pytest.raises(ToolError) as e:
        await invoke(svc, claims, tool, args)
    return e.value.code


def last_audit(svc: Services) -> dict:  # type: ignore[type-arg]
    return svc.audit.rows(1)[0]


async def test_unauthenticated(svc: Services) -> None:
    assert await err(svc, None, "github_list_issues", {"repo": REPO}) is ErrorCode.UNAUTHENTICATED
    row = last_audit(svc)
    assert row["status"] == "denied" and row["subject"] == "anonymous"


async def test_unknown_tool(svc: Services) -> None:
    assert await err(svc, claims_for("admin"), "rm_rf", {}) is ErrorCode.UNKNOWN_TOOL


@pytest.mark.parametrize(
    ("role", "tool", "args"),
    [
        ("viewer", "github_create_issue", {"repo": REPO, "title": "x"}),
        ("viewer", "slack_post_message", {"channel": CHANNEL, "text": "x"}),
        ("viewer", "jira_transition_issue", {"issue_key": "SBX-1", "transition": "Done"}),
        (
            "developer",
            "bulk_create_issues_from_csv",
            {"target": "github", "destination": REPO, "csv_text": "title\nx"},
        ),
    ],
)
async def test_forbidden_and_audited_as_denied(
    svc: Services, role: str, tool: str, args: dict
) -> None:  # type: ignore[type-arg]
    assert await err(svc, claims_for(role), tool, args) is ErrorCode.FORBIDDEN
    assert last_audit(svc)["status"] == "denied"


async def test_no_role_is_forbidden_everywhere(svc: Services) -> None:
    assert await err(svc, claims_for(), "github_list_issues", {"repo": REPO}) is ErrorCode.FORBIDDEN


async def test_forbidden_precedes_validation(svc: Services) -> None:
    # an unauthorised caller must not learn the schema from validation errors
    assert (
        await err(svc, claims_for("viewer"), "github_create_issue", {"junk": 1})
        is ErrorCode.FORBIDDEN
    )


@pytest.mark.parametrize(
    "args",
    [
        {"repo": "../../etc/passwd"},
        {"repo": "acme/app", "extra": 1},
        {"repo": "acme/app", "limit": 0},
        {"repo": "acme/app", "state": "bogus"},
        {},
    ],
)
async def test_invalid_arguments(svc: Services, args: dict) -> None:  # type: ignore[type-arg]
    assert (
        await err(svc, claims_for("viewer"), "github_list_issues", args)
        is ErrorCode.INVALID_ARGUMENTS
    )


async def test_out_of_scope_even_for_admin(svc: Services) -> None:
    assert (
        await err(svc, claims_for("admin"), "github_list_issues", {"repo": "other/repo"})
        is ErrorCode.OUT_OF_SCOPE
    )
    assert (
        await err(svc, claims_for("admin"), "jira_search_issues", {"project": "NOPE"})
        is ErrorCode.OUT_OF_SCOPE
    )
    assert (
        await err(
            svc, claims_for("admin"), "slack_post_message", {"channel": "C9999999999", "text": "x"}
        )
        is ErrorCode.OUT_OF_SCOPE
    )
    assert last_audit(svc)["status"] == "denied"


@respx.mock
async def test_read_happy_path_is_audited(svc: Services) -> None:
    respx.get(ISSUES).mock(
        return_value=httpx.Response(
            200, json=[{"number": 1, "title": "t", "state": "open", "html_url": "u"}]
        )
    )
    out = await invoke(svc, claims_for("viewer"), "github_list_issues", {"repo": REPO})
    assert out["issues"][0]["number"] == 1
    row = last_audit(svc)
    assert (row["status"], row["tool"], row["roles"]) == ("ok", "github_list_issues", "viewer")
    assert row["latency_ms"] >= 0 and len(row["request_id"]) == 32


async def test_rate_limit(svc: Services) -> None:
    svc.limiter.capacity = 2
    svc.limiter.refill = 0.0001
    c = claims_for("viewer", sub="spammer")
    for _ in range(2):
        assert (
            await err(svc, c, "github_list_issues", {"repo": "../x"}) is ErrorCode.INVALID_ARGUMENTS
        )
    assert await err(svc, c, "github_list_issues", {"repo": REPO}) is ErrorCode.RATE_LIMITED
    # another user is unaffected
    assert (
        await err(svc, claims_for("viewer", sub="other"), "github_list_issues", {"repo": "../x"})
        is ErrorCode.INVALID_ARGUMENTS
    )


@respx.mock
async def test_idempotent_create_replays_instead_of_duplicating(svc: Services) -> None:
    route = respx.post(ISSUES).mock(
        return_value=httpx.Response(201, json={"number": 9, "html_url": "u", "title": "t"})
    )
    c = claims_for("developer")
    a = await invoke(svc, c, "github_create_issue", {"repo": REPO, "title": "t"})
    b = await invoke(svc, c, "github_create_issue", {"repo": REPO, "title": "t"})
    assert a["number"] == b["number"] == 9
    assert b["idempotent_replay"] is True and "idempotent_replay" not in a
    assert route.call_count == 1


@respx.mock
async def test_explicit_idempotency_key_replays_identical_request(svc: Services) -> None:
    route = respx.post(ISSUES).mock(
        return_value=httpx.Response(201, json={"number": 1, "html_url": "u", "title": "t"})
    )
    c = claims_for("developer")
    args = {"repo": REPO, "title": "a", "idempotency_key": "k1"}
    first = await invoke(svc, c, "github_create_issue", dict(args))
    second = await invoke(svc, c, "github_create_issue", dict(args))
    assert first["number"] == second["number"] == 1
    assert second["idempotent_replay"] is True
    assert route.call_count == 1


@respx.mock
async def test_reused_idempotency_key_with_different_args_is_rejected(svc: Services) -> None:
    """Replaying someone else's result because they recycled a key is worse than failing."""
    route = respx.post(ISSUES).mock(
        return_value=httpx.Response(201, json={"number": 1, "html_url": "u", "title": "t"})
    )
    c = claims_for("developer")
    await invoke(
        svc, c, "github_create_issue", {"repo": REPO, "title": "a", "idempotency_key": "k1"}
    )
    assert (
        await err(
            svc, c, "github_create_issue", {"repo": REPO, "title": "b", "idempotency_key": "k1"}
        )
        is ErrorCode.IDEMPOTENCY_KEY_REUSED
    )
    assert route.call_count == 1
    assert last_audit(svc)["error_code"] == "IDEMPOTENCY_KEY_REUSED"


@respx.mock
async def test_idempotency_is_per_user(svc: Services) -> None:
    route = respx.post(ISSUES).mock(
        return_value=httpx.Response(201, json={"number": 1, "html_url": "u", "title": "t"})
    )
    for sub in ("u1", "u2"):
        await invoke(
            svc,
            claims_for("developer", sub=sub),
            "github_create_issue",
            {"repo": REPO, "title": "t"},
        )
    assert route.call_count == 2


@respx.mock
async def test_failed_write_can_be_retried(svc: Services) -> None:
    route = respx.post(ISSUES).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(201, json={"number": 3, "html_url": "u", "title": "t"}),
        ]
    )
    c = claims_for("developer")
    assert (
        await err(svc, c, "github_create_issue", {"repo": REPO, "title": "t"})
        is ErrorCode.UPSTREAM_ERROR
    )
    out = await invoke(svc, c, "github_create_issue", {"repo": REPO, "title": "t"})
    assert out["number"] == 3 and route.call_count == 2
    assert [r["status"] for r in svc.audit.rows(2)] == ["ok", "error"]


async def test_in_flight_duplicate_rejected(svc: Services) -> None:
    from mcp_hub.idempotency import make_key

    c = claims_for("developer")
    args = {"repo": REPO, "title": "t", "body": "", "labels": []}
    svc.idempotency.begin(make_key(c.subject, "github_create_issue", args, None))
    assert (
        await err(svc, c, "github_create_issue", {"repo": REPO, "title": "t"})
        is ErrorCode.DUPLICATE_IN_PROGRESS
    )


@respx.mock
async def test_internal_errors_do_not_leak_details(svc: Services) -> None:
    respx.get(ISSUES).mock(return_value=httpx.Response(200, json=[{"unexpected": "shape"}]))
    with pytest.raises(ToolError) as e:
        await invoke(svc, claims_for("viewer"), "github_list_issues", {"repo": REPO})
    assert e.value.code is ErrorCode.INTERNAL and "KeyError" not in str(e.value)
    assert "KeyError" in (last_audit(svc)["detail"] or "")  # but the audit log has it


async def test_upstream_failures_are_audited_as_error(svc: Services) -> None:
    with respx.mock:
        respx.get("https://jira.test/rest/api/3/search/jql").mock(return_value=httpx.Response(401))
        assert (
            await err(svc, claims_for("viewer"), "jira_search_issues", {"project": PROJECT})
            is ErrorCode.UPSTREAM_AUTH
        )
    row = last_audit(svc)
    assert (row["status"], row["error_code"]) == ("error", "UPSTREAM_AUTH")


@respx.mock
async def test_slack_list_only_shows_allowlisted_channels(svc: Services) -> None:
    respx.get("https://slack.com/api/conversations.list").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [
                    {"id": CHANNEL, "name": "ok"},
                    {"id": "C9999999999", "name": "secret-hr"},
                ],
            },
        )
    )
    out = await invoke(svc, claims_for("viewer"), "slack_list_channels", {})
    assert [c["name"] for c in out["channels"]] == ["ok"]


@respx.mock
async def test_audit_redaction_proven_end_to_end(svc: Services) -> None:
    """Secrets in arguments, in error details and in usernames never reach the audit DB."""
    secrets = [
        "ghp_" + "Z9y8X7w6V5" * 4,
        "xoxb-999999999999-abcdefghijkl",
        "Bearer supersecrettokenvalue123",
    ]
    respx.post(ISSUES).mock(
        return_value=httpx.Response(201, json={"number": 1, "html_url": "u", "title": "t"})
    )
    await invoke(
        svc,
        claims_for("developer"),
        "github_create_issue",
        {"repo": REPO, "title": f"leak {secrets[0]}", "body": f"{secrets[1]} and {secrets[2]}"},
    )
    with pytest.raises(ToolError):
        await invoke(
            svc,
            claims_for("developer"),
            "github_create_issue",
            {"repo": REPO, "title": "x", "password": secrets[0], "note": secrets[2]},
        )
    dump = json.dumps(svc.audit.rows(50))
    for s in secrets:
        assert s not in dump
    assert "[REDACTED]" in dump
    # the real upstream credentials configured on the server are never logged either
    assert svc.settings.github_token not in dump and svc.settings.slack_bot_token not in dump
