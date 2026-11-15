"""Append-only audit log in SQLite. Everything stored is passed through redact()."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .redact import redact, redact_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  request_id TEXT NOT NULL,
  subject TEXT NOT NULL,
  username TEXT NOT NULL,
  roles TEXT NOT NULL,
  tool TEXT NOT NULL,
  status TEXT NOT NULL,          -- ok | denied | error
  error_code TEXT,
  latency_ms REAL NOT NULL,
  args_json TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_tool ON audit_log(tool);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class AuditLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._lock = threading.Lock()
        with self._lock:
            self.conn.executescript(SCHEMA)

    def record(
        self,
        *,
        request_id: str,
        subject: str,
        username: str,
        roles: list[str],
        tool: str,
        status: str,
        latency_ms: float,
        args: Any,
        error_code: str | None = None,
        detail: str | None = None,
    ) -> None:
        args_json = json.dumps(redact(args), default=str, sort_keys=True)
        with self._lock:
            self.conn.execute(
                "INSERT INTO audit_log (ts, request_id, subject, username, roles, tool, status,"
                " error_code, latency_ms, args_json, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    request_id,
                    subject,
                    redact_text(username),
                    ",".join(sorted(roles)),
                    tool,
                    status,
                    error_code,
                    latency_ms,
                    args_json,
                    redact_text(detail) if detail else None,
                ),
            )
            self.conn.commit()

    def rows(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]
