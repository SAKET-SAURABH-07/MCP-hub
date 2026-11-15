from __future__ import annotations

import httpx
import pytest
import respx

from mcp_hub.bulk import parse_csv
from mcp_hub.errors import ErrorCode, ToolError
from mcp_hub.gateway import invoke
from mcp_hub.services import Services

from .conftest import PROJECT, REPO, claims_for

CSV_GH = "title,body,labels\nFirst,body one,bug;p1\nSecond,,\n"
ISSUES = f"https://api.github.com/repos/{REPO}/issues"


def args(csv: str, **kw) -> dict:  # type: ignore[no-untyped-def,type-arg]
    return {"target": "github", "destination": REPO, "csv_text": csv, **kw}


def test_parse_valid_github() -> None:
    p = parse_csv(CSV_GH, "github", REPO, 50, 10_000)
    assert [r["title"] for r in p.rows] == ["First", "Second"]
    assert p.rows[0]["labels"] == ["bug", "p1"] and not p.errors


def test_parse_collects_all_row_errors() -> None:
    p = parse_csv("title\nok\n" + "x" * 300 + "\n" + "y" * 400 + "\n", "github", REPO, 50, 10_000)
    assert (
        len(p.errors) == 2 and p.errors[0].startswith("line 3") and p.errors[1].startswith("line 4")
    )


@pytest.mark.parametrize(
    "text",
    ["body\nx", "title,evil\nx,y", "title\n", "", "title\n" + "a\n" * 60],
)
def test_parse_structural_errors(text: str) -> None:
    with pytest.raises(ToolError) as e:
        parse_csv(text, "github", REPO, 50, 10_000)
    assert e.value.code is ErrorCode.INVALID_ARGUMENTS


def test_parse_size_cap() -> None:
    with pytest.raises(ToolError):
        parse_csv("title\n" + "a" * 200, "github", REPO, 50, 100)


def test_parse_jira() -> None:
    p = parse_csv("summary,description,issue_type\nS,D,Bug\nS2,,\n", "jira", PROJECT, 50, 10_000)
    assert p.rows[1]["issue_type"] == "Task"


async def test_dry_run_is_default_and_writes_nothing(svc: Services) -> None:
    with respx.mock(assert_all_called=False) as m:
        route = m.post(ISSUES)
        out = await invoke(svc, claims_for("admin"), "bulk_create_issues_from_csv", args(CSV_GH))
    assert out == {"dry_run": True, "would_create": 2, "created": [], "failed": []}
    assert route.call_count == 0


async def test_invalid_row_blocks_every_write(svc: Services) -> None:
    with respx.mock(assert_all_called=False) as m:
        route = m.post(ISSUES)
        out = await invoke(
            svc,
            claims_for("admin"),
            "bulk_create_issues_from_csv",
            args("title\nok\n" + "x" * 300, dry_run=False),
        )
    assert out["created"] == [] and out["validation_errors"]
    assert route.call_count == 0


@respx.mock
async def test_real_run_creates_and_rerun_is_idempotent(svc: Services) -> None:
    route = respx.post(ISSUES).mock(
        side_effect=[
            httpx.Response(201, json={"number": n, "html_url": f"u{n}", "title": "t"})
            for n in (1, 2)
        ]
    )
    c = claims_for("admin")
    first = await invoke(svc, c, "bulk_create_issues_from_csv", args(CSV_GH, dry_run=False))
    assert [x["number"] for x in first["created"]] == [1, 2] and not first["failed"]
    again = await invoke(svc, c, "bulk_create_issues_from_csv", args(CSV_GH, dry_run=False))
    assert all(x["replayed"] for x in again["created"])
    assert route.call_count == 2


@respx.mock
async def test_partial_failure_reported_and_retryable(svc: Services) -> None:
    route = respx.post(ISSUES).mock(
        side_effect=[
            httpx.Response(201, json={"number": 1, "html_url": "u", "title": "t"}),
            httpx.Response(500),
            httpx.Response(201, json={"number": 2, "html_url": "u", "title": "t"}),
        ]
    )
    c = claims_for("admin")
    out = await invoke(svc, c, "bulk_create_issues_from_csv", args(CSV_GH, dry_run=False))
    assert len(out["created"]) == 1 and out["failed"] == [{"row": 2, "error": "UPSTREAM_ERROR"}]
    retry = await invoke(svc, c, "bulk_create_issues_from_csv", args(CSV_GH, dry_run=False))
    assert [x.get("replayed", False) for x in retry["created"]] == [True, False]
    assert route.call_count == 3


@respx.mock
async def test_bulk_jira_and_scope(svc: Services) -> None:
    respx.post("https://jira.test/rest/api/3/issue").mock(
        return_value=httpx.Response(201, json={"key": "SBX-5"})
    )
    out = await invoke(
        svc,
        claims_for("admin"),
        "bulk_create_issues_from_csv",
        {"target": "jira", "destination": PROJECT, "csv_text": "summary\nS", "dry_run": False},
    )
    assert out["created"][0]["key"] == "SBX-5"
    with pytest.raises(ToolError) as e:
        await invoke(
            svc,
            claims_for("admin"),
            "bulk_create_issues_from_csv",
            {"target": "jira", "destination": "OTHER", "csv_text": "summary\nS"},
        )
    assert e.value.code is ErrorCode.OUT_OF_SCOPE
    with pytest.raises(ToolError) as e2:
        await invoke(
            svc,
            claims_for("admin"),
            "bulk_create_issues_from_csv",
            args("title\nx", destination="../bad"),
        )
    assert e2.value.code is ErrorCode.INVALID_ARGUMENTS


async def test_pending_duplicate_row_reported(svc: Services) -> None:
    from mcp_hub.idempotency import make_key

    c = claims_for("admin")
    row = {"title": "First", "body": "body one", "labels": ["bug", "p1"]}
    svc.idempotency.begin(make_key(c.subject, f"bulk:github:{REPO}", row, None))
    out = await invoke(
        svc,
        c,
        "bulk_create_issues_from_csv",
        args("title,body,labels\nFirst,body one,bug;p1\n", dry_run=False),
    )
    assert out["failed"] == [{"row": 1, "error": "DUPLICATE_IN_PROGRESS"}]


async def test_bulk_charges_the_rate_limiter_per_row(svc: Services) -> None:
    """One token must not buy N upstream writes: the row count is charged up front."""
    svc.limiter.capacity = 3
    svc.limiter.refill = 0.0001
    csv = "title\n" + "".join(f"row{i}\n" for i in range(10))
    with respx.mock(assert_all_called=False) as m:
        route = m.post(ISSUES)
        with pytest.raises(ToolError) as e:
            await invoke(
                svc,
                claims_for("admin", sub="bulker"),
                "bulk_create_issues_from_csv",
                args(csv, dry_run=False),
            )
    assert e.value.code is ErrorCode.RATE_LIMITED
    assert route.call_count == 0  # refused before any write


async def test_bulk_dry_run_is_not_charged_per_row(svc: Services) -> None:
    svc.limiter.capacity = 3
    svc.limiter.refill = 0.0001
    csv = "title\n" + "".join(f"row{i}\n" for i in range(10))
    out = await invoke(
        svc, claims_for("admin", sub="planner"), "bulk_create_issues_from_csv", args(csv)
    )
    assert out["would_create"] == 10
