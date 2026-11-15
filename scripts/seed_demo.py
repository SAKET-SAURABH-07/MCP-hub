"""Fill a SEPARATE demo database with SYNTHETIC audit rows so the dashboard has something
to show before you have real usage. These numbers are fake: never put them on a resume.

    python scripts/seed_demo.py --db data/demo_audit.db
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from mcp_hub.audit import AuditLog, connect
from mcp_hub.rbac import ROLE_TOOLS

USERS = {"alice": "viewer", "bob": "developer", "carol": "admin"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/demo_audit.db")
    ap.add_argument("--rows", type=int, default=300)
    ns = ap.parse_args()
    if Path(ns.db) == Path("data/audit.db"):
        raise SystemExit("refusing to seed the real audit DB")
    rng = random.Random(7)
    log = AuditLog(connect(ns.db))
    all_tools = sorted(ROLE_TOOLS["admin"])
    for _ in range(ns.rows):
        user = rng.choice(list(USERS))
        role = USERS[user]
        tool = rng.choice(all_tools)
        allowed = tool in ROLE_TOOLS[role]
        status, code = ("ok", None) if allowed else ("denied", "FORBIDDEN")
        if allowed and rng.random() < 0.05:
            status, code = "error", "UPSTREAM_ERROR"
        log.record(
            request_id=f"{rng.getrandbits(64):016x}",
            subject=user,
            username=user,
            roles=[role],
            tool=tool,
            status=status,
            error_code=code,
            latency_ms=rng.uniform(40, 600),
            args={"demo": True},
        )
    # shift timestamps over the last 7 days so charts look alive
    log.conn.execute("UPDATE audit_log SET ts = ? - (id * 2000)", (time.time(),))
    log.conn.commit()
    print(f"seeded {ns.rows} SYNTHETIC rows into {ns.db}")


if __name__ == "__main__":
    main()
