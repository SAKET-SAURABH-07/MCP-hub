from mcp_hub.ratelimit import TokenBucketLimiter


def test_bucket_allows_burst_then_blocks_then_refills() -> None:
    now = [0.0]
    lim = TokenBucketLimiter(3, 1.0, clock=lambda: now[0])
    assert all(lim.try_acquire("a")[0] for _ in range(3))
    ok, wait = lim.try_acquire("a")
    assert not ok and wait == 1.0
    now[0] += 2
    assert lim.try_acquire("a")[0]


def test_buckets_are_per_key() -> None:
    lim = TokenBucketLimiter(1, 0.001, clock=lambda: 0.0)
    assert lim.try_acquire("a")[0]
    assert not lim.try_acquire("a")[0]
    assert lim.try_acquire("b")[0]


def test_zero_refill_reports_infinite_wait() -> None:
    lim = TokenBucketLimiter(1, 0.0, clock=lambda: 0.0)
    lim.try_acquire("a")
    assert lim.try_acquire("a") == (False, float("inf"))
