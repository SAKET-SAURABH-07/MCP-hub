from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
from mcp.shared.memory import create_connected_server_and_client_session as connect

from mcp_hub.rbac import ROLE_TOOLS
from mcp_hub.server import HubMCP, create_server
from mcp_hub.services import Services

from .conftest import REPO, claims_for


def server_as(svc: Services, *roles: str) -> HubMCP:
    return create_server(svc, static_claims=claims_for(*roles) if roles else None)


@pytest.mark.parametrize("role", ["viewer", "developer", "admin"])
async def test_tool_listing_is_filtered_by_role(svc: Services, role: str) -> None:
    async with connect(server_as(svc, role)._mcp_server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == ROLE_TOOLS[role]


async def test_unauthenticated_client_sees_no_tools(svc: Services) -> None:
    async with connect(server_as(svc)._mcp_server) as client:
        assert (await client.list_tools()).tools == []


async def test_tool_schemas_expose_expected_params(svc: Services) -> None:
    async with connect(server_as(svc, "admin")._mcp_server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert len(tools) == 9
    props = tools["github_create_issue"].inputSchema["properties"]
    assert {"repo", "title", "body", "labels", "idempotency_key"} <= set(props)
    assert tools["bulk_create_issues_from_csv"].inputSchema["required"] == [
        "target",
        "destination",
        "csv_text",
    ]


@respx.mock
async def test_call_allowed_tool_returns_json(svc: Services) -> None:
    respx.get(f"https://api.github.com/repos/{REPO}/issues").mock(
        return_value=httpx.Response(
            200, json=[{"number": 4, "title": "t", "state": "open", "html_url": "u"}]
        )
    )
    async with connect(server_as(svc, "viewer")._mcp_server) as client:
        res = await client.call_tool("github_list_issues", {"repo": REPO})
    assert not res.isError
    assert json.loads(res.content[0].text)["issues"][0]["number"] == 4  # type: ignore[union-attr]


async def test_call_forbidden_tool_is_an_error_result(svc: Services) -> None:
    async with connect(server_as(svc, "viewer")._mcp_server) as client:
        res = await client.call_tool("github_create_issue", {"repo": REPO, "title": "x"})
    assert res.isError
    assert "FORBIDDEN" in res.content[0].text  # type: ignore[union-attr]
    assert svc.audit.rows(1)[0]["status"] == "denied"


async def test_call_without_identity_is_unauthenticated(svc: Services) -> None:
    async with connect(server_as(svc)._mcp_server) as client:
        res = await client.call_tool("github_list_issues", {"repo": REPO})
    assert res.isError and "UNAUTHENTICATED" in res.content[0].text  # type: ignore[union-attr]


async def test_invalid_args_surface_as_error_not_crash(svc: Services) -> None:
    async with connect(server_as(svc, "viewer")._mcp_server) as client:
        res = await client.call_tool("github_list_issues", {"repo": "../etc/passwd"})
        assert res.isError and "INVALID_ARGUMENTS" in res.content[0].text  # type: ignore[union-attr]
        res2 = await client.call_tool("github_list_issues", {})  # missing required: FastMCP layer
        assert res2.isError


@respx.mock
async def test_all_nine_tools_callable_by_admin(svc: Services) -> None:
    respx.get(f"https://api.github.com/repos/{REPO}/issues").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(f"https://api.github.com/repos/{REPO}/issues").mock(
        return_value=httpx.Response(201, json={"number": 1, "html_url": "u", "title": "t"})
    )
    respx.get(f"https://api.github.com/repos/{REPO}/pulls/3").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 3,
                "title": "p",
                "state": "open",
                "html_url": "u",
                "base": {"ref": "m"},
                "head": {"ref": "h"},
            },
        )
    )
    respx.get("https://jira.test/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": []})
    )
    respx.post("https://jira.test/rest/api/3/issue").mock(
        return_value=httpx.Response(201, json={"key": "SBX-1"})
    )
    respx.get("https://jira.test/rest/api/3/issue/SBX-1/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": "Done"}]})
    )
    respx.post("https://jira.test/rest/api/3/issue/SBX-1/transitions").mock(
        return_value=httpx.Response(204)
    )
    respx.get("https://slack.com/api/conversations.list").mock(
        return_value=httpx.Response(200, json={"ok": True, "channels": []})
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "channel": "C0123456789", "ts": "1"})
    )
    calls: list[tuple[str, dict[str, Any]]] = [
        ("github_list_issues", {"repo": REPO}),
        ("github_create_issue", {"repo": REPO, "title": "t", "labels": ["a"]}),
        ("github_get_pull_request", {"repo": REPO, "number": 3}),
        ("jira_search_issues", {"project": "SBX", "text": "x"}),
        ("jira_create_issue", {"project": "SBX", "summary": "s"}),
        ("jira_transition_issue", {"issue_key": "SBX-1", "transition": "Done"}),
        ("slack_list_channels", {}),
        ("slack_post_message", {"channel": "C0123456789", "text": "hi"}),
        (
            "bulk_create_issues_from_csv",
            {"target": "github", "destination": REPO, "csv_text": "title\nx"},
        ),
    ]
    async with connect(server_as(svc, "admin")._mcp_server) as client:
        for name, args in calls:
            res = await client.call_tool(name, args)
            assert not res.isError, (name, res.content)
    assert {r["tool"] for r in svc.audit.rows(50)} == {n for n, _ in calls}
