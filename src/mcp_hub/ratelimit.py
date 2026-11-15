"""Per-subject token bucket."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    def __init__(
        self,
        capacity: float,
        refill_per_sec: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.capacity = capacity
        self.refill = refill_per_sec
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def try_acquire(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = self._buckets[key] = _Bucket(self.capacity, now)
            b.tokens = min(self.capacity, b.tokens + (now - b.updated) * self.refill)
            b.updated = now
            if b.tokens >= cost:
                b.tokens -= cost
                return True, 0.0
            wait = (cost - b.tokens) / self.refill if self.refill > 0 else float("inf")
            return False, wait
