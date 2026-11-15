from __future__ import annotations

import httpx
import pytest
import respx

from mcp_hub.clients.github import GitHubClient
from mcp_hub.clients.http import UpstreamClient
from mcp_hub.clients.jira import JiraClient, adf, build_jql
from mcp_hub.clients.slack import SlackClient
from mcp_hub.errors import ErrorCode, ToolError

BASE = "https://api.test"


def client(retries: int = 2) -> tuple[UpstreamClient, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    return UpstreamClient(BASE, {}, retries=retries, sleep=fake_sleep), sleeps


async def code_of(coro: object) -> ErrorCode:
    with pytest.raises(ToolError) as e:
        await coro  # type: ignore[misc]
    return e.value.code


@respx.mock
async def test_retries_5xx_then_succeeds() -> None:
    c, sleeps = client()
    respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})]
    )
    assert await c.request("GET", "/x") == {"ok": 1}
    assert len(sleeps) == 1


@respx.mock
async def test_gives_up_after_retries() -> None:
    c, sleeps = client(retries=2)
    route = respx.get(f"{BASE}/x").mock(return_value=httpx.Response(502))
    assert await code_of(c.request("GET", "/x")) is ErrorCode.UPSTREAM_ERROR
    assert route.call_count == 3 and len(sleeps) == 2


@respx.mock
async def test_honours_retry_after() -> None:
    c, sleeps = client(retries=1)
    respx.get(f"{BASE}/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={}),
        ]
    )
    await c.request("GET", "/x")
    assert sleeps == [7.0]


@respx.mock
async def test_transport_error_retried_then_mapped() -> None:
    c, sleeps = client(retries=1)
    respx.get(f"{BASE}/x").mock(side_effect=httpx.ConnectError("boom"))
    assert await code_of(c.request("GET", "/x")) is ErrorCode.UPSTREAM_ERROR
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (401, {}, ErrorCode.UPSTREAM_AUTH),
        (403, {}, ErrorCode.UPSTREAM_AUTH),
        (403, {"X-RateLimit-Remaining": "0"}, ErrorCode.UPSTREAM_RATE_LIMITED),
        (404, {}, ErrorCode.UPSTREAM_NOT_FOUND),
        (422, {}, ErrorCode.UPSTREAM_ERROR),
    ],
)
@respx.mock
async def test_status_mapping(status: int, headers: dict[str, str], expected: ErrorCode) -> None:
    c, _ = client(retries=0)
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(status, headers=headers))
    assert await code_of(c.request("GET", "/x")) is expected


@respx.mock
async def test_429_exhausted_maps_to_rate_limited() -> None:
    c, _ = client(retries=1)
    respx.get(f"{BASE}/x").mock(return_value=httpx.Response(429))
    assert await code_of(c.request("GET", "/x")) is ErrorCode.UPSTREAM_RATE_LIMITED


@respx.mock
async def test_empty_and_non_json_bodies() -> None:
    c, _ = client(retries=0)
    respx.get(f"{BASE}/empty").mock(return_value=httpx.Response(204))
    respx.get(f"{BASE}/html").mock(return_value=httpx.Response(200, text="<html>"))
    assert await c.request("GET", "/empty") == {}
    assert await code_of(c.request("GET", "/html")) is ErrorCode.UPSTREAM_ERROR


@respx.mock
async def test_github_list_filters_out_pull_requests(settings) -> None:  # type: ignore[no-untyped-def]
    gh = GitHubClient.from_settings(settings)
    respx.get("https://api.github.com/repos/acme/app/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 1,
                    "title": "bug",
                    "state": "open",
                    "html_url": "u1",
                    "labels": [{"name": "x"}],
                    "user": {"login": "a"},
                },
                {
                    "number": 2,
                    "title": "a PR",
                    "state": "open",
                    "html_url": "u2",
                    "pull_request": {},
                },
            ],
        )
    )
    out = await gh.list_issues("acme/app", "open", 10)
    assert [i["number"] for i in out] == [1] and out[0]["labels"] == ["x"]
    sent = respx.calls.last.request
    assert sent.headers["authorization"].startswith("Bearer ghp_")


@respx.mock
async def test_github_create_and_get_pr(settings) -> None:  # type: ignore[no-untyped-def]
    gh = GitHubClient.from_settings(settings)
    respx.post("https://api.github.com/repos/acme/app/issues").mock(
        return_value=httpx.Response(201, json={"number": 5, "html_url": "u", "title": "t"})
    )
    respx.get("https://api.github.com/repos/acme/app/pulls/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 7,
                "title": "pr",
                "state": "open",
                "html_url": "u",
                "base": {"ref": "main"},
                "head": {"ref": "f"},
                "user": {"login": "z"},
            },
        )
    )
    assert (await gh.create_issue("acme/app", "t", "b", ["bug"]))["number"] == 5
    assert b'"labels"' in respx.calls.last.request.content
    pr = await gh.get_pull_request("acme/app", 7)
    assert pr["base"] == "main" and pr["author"] == "z"


def test_jql_is_quoted_and_escaped() -> None:
    q = build_jql("SBX", 'x" OR project = "SECRET', "In Progress")
    assert q.startswith('project = "SBX" AND text ~ "x\\" OR project = \\"SECRET"')
    assert 'status = "In Progress"' in q


def test_adf_structure() -> None:
    d = adf("one\n\ntwo")
    assert d["type"] == "doc" and len(d["content"]) == 2


@respx.mock
async def test_jira_search_uses_new_endpoint_and_basic_auth(settings) -> None:  # type: ignore[no-untyped-def]
    j = JiraClient.from_settings(settings)
    route = respx.get("https://jira.test/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={
                "issues": [
                    {"key": "SBX-1", "fields": {"summary": "s", "status": {"name": "To Do"}}}
                ]
            },
        )
    )
    out = await j.search("SBX", "", "", 5)
    assert out[0]["key"] == "SBX-1" and out[0]["url"] == "https://jira.test/browse/SBX-1"
    assert route.calls.last.request.headers["authorization"].startswith("Basic ")
    assert "maxResults=5" in str(route.calls.last.request.url)


@respx.mock
async def test_jira_create_and_transition(settings) -> None:  # type: ignore[no-untyped-def]
    j = JiraClient.from_settings(settings)
    respx.post("https://jira.test/rest/api/3/issue").mock(
        return_value=httpx.Response(201, json={"key": "SBX-2"})
    )
    assert (await j.create_issue("SBX", "s", "desc", "Task"))["key"] == "SBX-2"
    respx.get("https://jira.test/rest/api/3/issue/SBX-2/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "31", "name": "Done"}]})
    )
    post = respx.post("https://jira.test/rest/api/3/issue/SBX-2/transitions").mock(
        return_value=httpx.Response(204)
    )
    assert (await j.transition("SBX-2", "done"))["transitioned_to"] == "Done"
    assert b'"31"' in post.calls.last.request.content
    with pytest.raises(ToolError) as e:
        await j.transition("SBX-2", "Nonexistent")
    assert e.value.code is ErrorCode.INVALID_ARGUMENTS and "Done" in e.value.message


@pytest.mark.parametrize(
    ("slack_error", "expected"),
    [
        ("invalid_auth", ErrorCode.UPSTREAM_AUTH),
        ("channel_not_found", ErrorCode.UPSTREAM_NOT_FOUND),
        ("ratelimited", ErrorCode.UPSTREAM_RATE_LIMITED),
        ("boom", ErrorCode.UPSTREAM_ERROR),
    ],
)
@respx.mock
async def test_slack_ok_false_mapped(settings, slack_error: str, expected: ErrorCode) -> None:  # type: ignore[no-untyped-def]
    s = SlackClient.from_settings(settings)
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": slack_error})
    )
    assert await code_of(s.post_message("C0123456789", "hi")) is expected


@respx.mock
async def test_slack_list_and_post_ok(settings) -> None:  # type: ignore[no-untyped-def]
    s = SlackClient.from_settings(settings)
    respx.get("https://slack.com/api/conversations.list").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "channels": [
                    {"id": "C1", "name": "general", "num_members": 3, "topic": {"value": "t"}}
                ],
            },
        )
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "channel": "C1", "ts": "1.2"})
    )
    assert (await s.list_channels(10))[0]["name"] == "general"
    assert (await s.post_message("C1", "hi"))["ts"] == "1.2"
