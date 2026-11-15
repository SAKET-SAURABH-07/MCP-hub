"""Print real numbers from YOUR audit log (run after real usage). Only quote what this prints.

python scripts/metrics.py [--db data/audit.db] [--json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from mcp_hub.metrics import compute_metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/audit.db")
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args()
    if not Path(ns.db).exists():
        raise SystemExit(f"no audit DB at {ns.db}; run the server (or scripts/smoke_live.py) first")
    m = compute_metrics(sqlite3.connect(ns.db))
    if ns.json:
        print(json.dumps(m, indent=2))
        return
    print(f"total calls      : {m['total_calls']}")
    print(f"unique users     : {m['unique_users']}")
    print(f"status           : {m['by_status']}")
    print(f"denied (authz)   : {m['denied']} ({m['denied_rate']:.1%})")
    print(f"ok latency       : p50 {m['ok_latency_p50_ms']} ms, p95 {m['ok_latency_p95_ms']} ms")
    print("per tool:")
    for name, t in m["tools"].items():
        print(f"  {name:30s} calls={t['calls']:4d} errors={t['errors']:3d} p95={t['p95_ms']} ms")


if __name__ == "__main__":
    main()
