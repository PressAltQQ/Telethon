"""Tests for mcp_bridge/rate_limit.py — token bucket."""
from __future__ import annotations

import asyncio
import time

import pytest

from mcp_bridge.errors import RateLimitError
from mcp_bridge.rate_limit import TokenBucket, get_rate_limiter, reset_rate_limiter


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure the module-level singleton is reset between tests."""
    reset_rate_limiter()
    yield
    reset_rate_limiter()


class TestTokenBucketBasic:
    @pytest.mark.asyncio
    async def test_acquire_succeeds_immediately_when_tokens_available(self):
        bucket = TokenBucket(rate_per_minute=60, burst=5)
        # Should not raise; bucket starts full (burst=5)
        start = time.monotonic()
        await bucket.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # nearly instantaneous

    @pytest.mark.asyncio
    async def test_burst_capacity_respected(self):
        bucket = TokenBucket(rate_per_minute=60, burst=3)
        # Can consume 3 tokens immediately
        for _ in range(3):
            await bucket.acquire()
        # 4th should fail with a very short max_wait
        with pytest.raises(RateLimitError):
            await bucket.acquire(max_wait_seconds=0.01)

    @pytest.mark.asyncio
    async def test_acquire_raises_rate_limit_error_after_max_wait(self):
        bucket = TokenBucket(rate_per_minute=6, burst=1)  # 1 token / 10 s
        # Drain the single token
        await bucket.acquire()
        # Next acquire should fail within max_wait=0.05 s
        with pytest.raises(RateLimitError):
            await bucket.acquire(max_wait_seconds=0.05)

    @pytest.mark.asyncio
    async def test_rate_limit_error_has_retry_after(self):
        bucket = TokenBucket(rate_per_minute=6, burst=1)
        await bucket.acquire()
        with pytest.raises(RateLimitError) as exc_info:
            await bucket.acquire(max_wait_seconds=0.01)
        err = exc_info.value
        assert err.retry_after_seconds is not None
        assert err.retry_after_seconds > 0

    @pytest.mark.asyncio
    async def test_acquire_waits_when_empty_within_budget(self):
        # 60 tokens/min = 1 per second; burst=1 → drain then expect ~1s wait
        # Use much faster rate: 3600 / min = 1 per 1/60 s ≈ 16.7 ms
        bucket = TokenBucket(rate_per_minute=3600, burst=1)
        await bucket.acquire()
        # Should wait and then succeed
        start = time.monotonic()
        await bucket.acquire(max_wait_seconds=1.0)
        elapsed = time.monotonic() - start
        # Should have waited at least ~16 ms
        assert elapsed >= 0.01
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_tokens_refill_at_configured_rate(self):
        # 120 ops/min = 2 per second; burst=1
        bucket = TokenBucket(rate_per_minute=120, burst=1)
        await bucket.acquire()  # drain

        # After ~0.6s we should have refilled ~1.2 tokens
        await asyncio.sleep(0.55)
        # Should succeed now
        await bucket.acquire(max_wait_seconds=0.1)


class TestGetRateLimiter:
    def test_returns_singleton(self):
        from types import SimpleNamespace
        config = SimpleNamespace(max_ops_per_minute=30, burst=5)
        l1 = get_rate_limiter(config)
        l2 = get_rate_limiter(config)
        assert l1 is l2

    def test_singleton_has_correct_rate(self):
        from types import SimpleNamespace
        config = SimpleNamespace(max_ops_per_minute=60, burst=10)
        bucket = get_rate_limiter(config)
        # Check internal rate: 60/60 = 1.0 token/s
        assert abs(bucket._rate_per_second - 1.0) < 0.001
        assert bucket._burst == 10
