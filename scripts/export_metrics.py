"""Export the audit log to the JSON the static dashboard reads.

    python scripts/export_metrics.py --db data/demo_audit.db --source demo
    python scripts/export_metrics.py --db data/audit.db --source live --watch 5

`--watch` regenerates on an interval, so serving `web/` locally gives a live view with
exactly the same code path GitHub Pages uses for the committed snapshot.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_hub.metrics import compute_metrics
from mcp_hub.rbac import ROLE_TOOLS
from mcp_hub.tools import TOOLS

RECENT_COLUMNS = (
    "ts, username, roles, tool, status, error_code, latency_ms, args_json, detail, request_id"
)


def build_payload(db: Path, source: str, recent: int) -> dict[str, Any]:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        metrics = compute_metrics(conn)
        rows = conn.execute(
            f"SELECT {RECENT_COLUMNS} FROM audit_log ORDER BY ts DESC LIMIT ?", (recent,)
        ).fetchall()
    finally:
        conn.close()
    return {
        "generated_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        # "demo" marks synthetic rows from seed_demo.py so the UI can say so plainly.
        "source": source,
        "metrics": metrics,
        "recent": [dict(r) for r in rows],
        # The page's access matrix and request simulator are driven by these, so the
        # site shows the rules the server enforces rather than a hand-kept copy.
        "rbac": {role: sorted(tools) for role, tools in ROLE_TOOLS.items()},
        "tools": {
            name: {
                "description": spec.description,
                "write": spec.write,
                "idempotent": spec.idempotent,
            }
            for name, spec in sorted(TOOLS.items())
        },
    }


def write_once(db: Path, out: Path, source: str, recent: int) -> dict[str, Any]:
    payload = build_payload(db, source, recent)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(out)  # atomic, so a page load never sees a half-written file
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/audit.db")
    ap.add_argument("--out", default="web/data/metrics.json")
    ap.add_argument("--source", choices=["live", "demo"], default="live")
    ap.add_argument("--recent", type=int, default=100)
    ap.add_argument("--watch", type=float, default=0.0, help="seconds between refreshes")
    ns = ap.parse_args()

    db = Path(ns.db)
    if not db.exists():
        raise SystemExit(
            f"no audit DB at {db}. Run the server, or: python scripts/seed_demo.py "
            "--db data/demo_audit.db"
        )
    out = Path(ns.out)
    while True:
        payload = write_once(db, out, ns.source, ns.recent)
        total = payload["metrics"]["total_calls"]
        print(f"wrote {out} ({total} calls, source={ns.source})")
        if ns.watch <= 0:
            return
        time.sleep(ns.watch)


if __name__ == "__main__":
    main()
