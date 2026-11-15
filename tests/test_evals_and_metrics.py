from __future__ import annotations

import sqlite3

from evals.run_evals import load_cases, run_all

from mcp_hub.audit import AuditLog, connect
from mcp_hub.metrics import compute_metrics


async def test_eval_set_passes_completely() -> None:
    report = await run_all()
    assert report["failures"] == []
    assert report["total"] == len(load_cases()) >= 40
    assert set(report["by_category"]) == {"authz", "scope", "validation", "injection", "redaction"}


def test_metrics_numbers() -> None:
    conn = connect(":memory:")
    log = AuditLog(conn)
    for i, (status, code, lat) in enumerate(
        [
            ("ok", None, 10.0),
            ("ok", None, 30.0),
            ("denied", "FORBIDDEN", 1.0),
            ("error", "UPSTREAM_ERROR", 5.0),
        ]
    ):
        log.record(
            request_id=str(i),
            subject=f"u{i % 2}",
            username="x",
            roles=["viewer"],
            tool="t1",
            status=status,
            error_code=code,
            latency_ms=lat,
            args={},
        )
    m = compute_metrics(conn)
    assert m["total_calls"] == 4 and m["unique_users"] == 2 and m["denied"] == 1
    assert m["denied_rate"] == 0.25
    assert m["by_error_code"] == {"FORBIDDEN": 1, "UPSTREAM_ERROR": 1}
    assert m["tools"]["t1"]["calls"] == 4 and m["tools"]["t1"]["errors"] == 2
    assert m["ok_latency_p50_ms"] == 10.0 and m["ok_latency_p95_ms"] == 30.0


def test_metrics_empty_db() -> None:
    conn = connect(":memory:")
    AuditLog(conn)
    m = compute_metrics(conn)
    assert (
        m["total_calls"] == 0
        and m["ok_latency_p95_ms"] == 0.0
        and isinstance(conn, sqlite3.Connection)
    )


def test_metrics_timeline_and_roles() -> None:
    conn = connect(":memory:")
    log = AuditLog(conn)
    for i, (status, code, roles) in enumerate(
        [
            ("ok", None, ["viewer"]),
            ("denied", "FORBIDDEN", ["viewer"]),
            ("ok", None, ["admin", "developer"]),
        ]
    ):
        log.record(
            request_id=str(i),
            subject=f"u{i}",
            username="x",
            roles=roles,
            tool="t1",
            status=status,
            error_code=code,
            latency_ms=1.0,
            args={},
        )
    m = compute_metrics(conn)
    assert m["by_role"] == {"admin": 1, "developer": 1, "viewer": 2}
    assert sum(b["total"] for b in m["timeline"]) == 3
    assert sum(b["denied"] for b in m["timeline"]) == 1
    buckets = [b["bucket"] for b in m["timeline"]]
    assert buckets == sorted(buckets) and buckets[0].endswith(":00Z")


def test_metrics_timeline_empty_db() -> None:
    conn = connect(":memory:")
    AuditLog(conn)
    m = compute_metrics(conn)
    assert m["timeline"] == [] and m["by_role"] == {}
