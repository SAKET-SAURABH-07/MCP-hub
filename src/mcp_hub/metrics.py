"""Aggregate numbers from the audit log. Used by scripts/metrics.py and the dashboard,
so the resume numbers and the dashboard numbers come from the same code."""

from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime
from typing import Any


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = max(0, math.ceil(p / 100 * len(sorted_vals)) - 1)
    return sorted_vals[k]


def compute_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT tool, status, error_code, latency_ms, subject, roles, ts FROM audit_log"
    ).fetchall()
    total = len(rows)
    by_status: dict[str, int] = {}
    by_tool: dict[str, dict[str, Any]] = {}
    by_code: dict[str, int] = {}
    by_role: dict[str, int] = {}
    buckets: dict[str, dict[str, int]] = {}
    lat_all: list[float] = []
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        if r["error_code"]:
            by_code[r["error_code"]] = by_code.get(r["error_code"], 0) + 1
        for role in (r["roles"] or "").split(","):
            if role:
                by_role[role] = by_role.get(role, 0) + 1
        hour = datetime.fromtimestamp(r["ts"], tz=UTC).strftime("%Y-%m-%dT%H:00Z")
        b = buckets.setdefault(hour, {})
        b[r["status"]] = b.get(r["status"], 0) + 1
        t = by_tool.setdefault(r["tool"], {"calls": 0, "errors": 0, "lat": []})
        t["calls"] += 1
        t["errors"] += r["status"] != "ok"
        if r["status"] == "ok":
            t["lat"].append(r["latency_ms"])
            lat_all.append(r["latency_ms"])
    tools = {
        name: {
            "calls": v["calls"],
            "errors": v["errors"],
            "p50_ms": round(_percentile(sorted(v["lat"]), 50), 2),
            "p95_ms": round(_percentile(sorted(v["lat"]), 95), 2),
        }
        for name, v in sorted(by_tool.items())
    }
    lat_all.sort()
    return {
        "total_calls": total,
        "unique_users": len({r["subject"] for r in rows}),
        "by_status": by_status,
        "denied": by_status.get("denied", 0),
        "denied_rate": round(by_status.get("denied", 0) / total, 4) if total else 0.0,
        "by_error_code": dict(sorted(by_code.items())),
        "by_role": dict(sorted(by_role.items())),
        "timeline": [
            {
                "bucket": k,
                "ok": v.get("ok", 0),
                "denied": v.get("denied", 0),
                "error": v.get("error", 0),
                "total": sum(v.values()),
            }
            for k, v in sorted(buckets.items())
        ],
        "ok_latency_p50_ms": round(_percentile(lat_all, 50), 2),
        "ok_latency_p95_ms": round(_percentile(lat_all, 95), 2),
        "tools": tools,
        "first_ts": min((r["ts"] for r in rows), default=None),
        "last_ts": max((r["ts"] for r in rows), default=None),
    }
