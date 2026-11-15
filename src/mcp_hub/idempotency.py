"""Idempotent writes. A retried create/transition returns the stored result instead of
creating a duplicate. State machine per key: (absent) -> pending -> done; failures delete
the key so the caller can retry.

When the caller supplies an explicit `idempotency_key`, the arguments are fingerprinted and
stored with it. Reusing one key for two different payloads returns "conflict" rather than
silently replaying the first result -- otherwise a client that recycles a key would believe
its second, different request had succeeded."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any, Literal

SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  result_json TEXT,
  created REAL NOT NULL,
  fingerprint TEXT
);
"""

Outcome = Literal["new", "pending", "done", "conflict"]


def make_key(subject: str, tool: str, args: dict[str, Any], explicit: str | None) -> str:
    material = (
        {"s": subject, "t": tool, "k": explicit}
        if explicit
        else {
            "s": subject,
            "t": tool,
            "a": args,
        }
    )
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def fingerprint_args(args: dict[str, Any]) -> str:
    """Stable hash of a request payload, used to detect idempotency-key reuse."""
    return hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()


class IdempotencyStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        ttl: float = 3600.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.conn = conn
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        with self._lock:
            self.conn.executescript(SCHEMA)
            # Databases created before fingerprinting existed predate the column.
            columns = {r[1] for r in self.conn.execute("PRAGMA table_info(idempotency)")}
            if "fingerprint" not in columns:
                self.conn.execute("ALTER TABLE idempotency ADD COLUMN fingerprint TEXT")
            self.conn.commit()

    def begin(
        self, key: str, fingerprint: str | None = None
    ) -> tuple[Outcome, dict[str, Any] | None]:
        now = self._clock()
        with self._lock:
            self.conn.execute("DELETE FROM idempotency WHERE created < ?", (now - self.ttl,))
            row = self.conn.execute(
                "SELECT state, result_json, fingerprint FROM idempotency WHERE key=?", (key,)
            ).fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO idempotency (key, state, created, fingerprint)"
                    " VALUES (?, 'pending', ?, ?)",
                    (key, now, fingerprint),
                )
                self.conn.commit()
                return "new", None
            self.conn.commit()
            stored = row["fingerprint"]
            if fingerprint is not None and stored is not None and stored != fingerprint:
                return "conflict", None
            if row["state"] == "done":
                return "done", json.loads(row["result_json"])
            return "pending", None

    def complete(self, key: str, result: dict[str, Any]) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE idempotency SET state='done', result_json=? WHERE key=?",
                (json.dumps(result, default=str), key),
            )
            self.conn.commit()

    def abort(self, key: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM idempotency WHERE key=? AND state='pending'", (key,))
            self.conn.commit()
