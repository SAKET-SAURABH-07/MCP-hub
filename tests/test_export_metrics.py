"""The export is the contract between the audit log and web/app.js.

If a key here changes name or shape, the dashboard breaks silently in a browser --
nothing else in the suite would catch that, so it is pinned here.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.export_metrics import build_payload, write_once

from mcp_hub.audit import AuditLog, connect

# Every field web/app.js reads off the payload.
REQUIRED_METRIC_KEYS = {
    "total_calls",
    "unique_users",
    "denied",
    "denied_rate",
    "ok_latency_p95_ms",
    "by_error_code",
    "by_role",
    "timeline",
    "tools",
}
REQUIRED_RECENT_KEYS = {
    "ts",
    "username",
    "roles",
    "tool",
    "status",
    "error_code",
    "latency_ms",
    "args_json",
    "detail",
}


def _seed(path: Path, rows: int = 3) -> None:
    log = AuditLog(connect(path))
    for i in range(rows):
        log.record(
            request_id=str(i),
            subject=f"u{i}",
            username="alice",
            roles=["viewer"],
            tool="github_list_issues",
            status="ok" if i else "denied",
            error_code=None if i else "FORBIDDEN",
            latency_ms=12.5,
            args={"repo": "acme/app"},
        )
    log.conn.close()


def test_payload_exposes_every_field_the_dashboard_reads(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    payload = build_payload(db, "demo", recent=10)

    assert payload["source"] == "demo"
    assert payload["generated_at"].endswith("Z")
    assert set(payload["metrics"]) >= REQUIRED_METRIC_KEYS
    assert set(payload["recent"][0]) >= REQUIRED_RECENT_KEYS

    tool = payload["metrics"]["tools"]["github_list_issues"]
    assert {"calls", "errors", "p50_ms", "p95_ms"} <= set(tool)
    bucket = payload["metrics"]["timeline"][0]
    assert {"bucket", "ok", "denied", "error", "total"} <= set(bucket)


def test_recent_is_newest_first_and_capped(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db, rows=8)
    payload = build_payload(db, "live", recent=3)
    timestamps = [r["ts"] for r in payload["recent"]]
    assert len(timestamps) == 3
    assert timestamps == sorted(timestamps, reverse=True)


def test_write_once_emits_valid_json_and_leaves_no_temp_file(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    _seed(db)
    out = tmp_path / "web" / "data" / "metrics.json"
    write_once(db, out, "demo", 10)

    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["metrics"]["total_calls"] == 3
    assert not list(out.parent.glob("*.tmp"))


def test_export_redacts_because_the_audit_log_already_did(tmp_path: Path) -> None:
    """The dashboard publishes this file, so secrets must never reach it."""
    db = tmp_path / "audit.db"
    log = AuditLog(connect(db))
    log.record(
        request_id="r1",
        subject="u1",
        username="alice",
        roles=["admin"],
        tool="slack_post_message",
        status="ok",
        latency_ms=1.0,
        args={"text": "token=ghp_" + "a" * 36, "authorization": "Bearer abcdef123456"},
    )
    log.conn.close()

    blob = json.dumps(build_payload(db, "live", recent=10))
    assert "ghp_" not in blob
    assert "abcdef123456" not in blob
    assert "[REDACTED]" in blob


def test_payload_carries_the_live_role_matrix(tmp_path: Path) -> None:
    """The site's access matrix and simulator must mirror rbac.py, not drift from it."""
    from mcp_hub.rbac import ROLE_TOOLS
    from mcp_hub.tools import TOOLS

    db = tmp_path / "audit.db"
    _seed(db)
    payload = build_payload(db, "demo", recent=1)

    assert payload["rbac"] == {role: sorted(tools) for role, tools in ROLE_TOOLS.items()}
    assert set(payload["tools"]) == set(TOOLS)
    create = payload["tools"]["github_create_issue"]
    assert create["write"] is True and create["idempotent"] is True
    assert payload["tools"]["github_list_issues"]["write"] is False
    assert payload["tools"]["bulk_create_issues_from_csv"]["idempotent"] is False
