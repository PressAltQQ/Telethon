"""
Token-bucket rate limiter for the MCP bridge.

Module-level singleton ``get_rate_limiter(config)`` returns the shared bucket.
Tokens are consumed one-per-call via ``await acquire()``.
When the bucket is empty and max_wait_seconds elapses, raises RateLimitError.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from mcp_bridge.errors import RateLimitError

__log__ = logging.getLogger(__name__)

_singleton: Optional["TokenBucket"] = None


class TokenBucket:
    """Asyncio-friendly token bucket.

    Args:
        rate_per_minute: Steady-state refill rate (tokens per minute).
        burst:           Maximum instantaneous burst capacity.
    """

    def __init__(self, rate_per_minute: int, burst: int) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        self._rate_per_second: float = rate_per_minute / 60.0
        self._burst = burst
        self._tokens: float = float(burst)  # start full
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Compute elapsed time and add tokens (capped at burst)."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate_per_second)

    async def acquire(self, max_wait_seconds: float = 5.0) -> None:
        """Consume one token. Waits up to max_wait_seconds for a token.

        Raises RateLimitError if no token is available within the wait budget.
        """
        deadline = time.monotonic() + max_wait_seconds

        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # Compute how long until a token is available
            tokens_needed = 1.0 - self._tokens
            wait_needed = tokens_needed / self._rate_per_second
            remaining = deadline - time.monotonic()

            if wait_needed > remaining:
                retry_after = wait_needed
                raise RateLimitError(
                    f"Rate limit exceeded; retry after {retry_after:.1f}s",
                    retry_after_seconds=retry_after,
                )

            # Wait for the token
            await asyncio.sleep(wait_needed)
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # Shouldn't reach here, but fail safely
            retry_after = 1.0 / self._rate_per_second
            raise RateLimitError(
                f"Rate limit exceeded; retry after {retry_after:.1f}s",
                retry_after_seconds=retry_after,
            )


def get_rate_limiter(config) -> TokenBucket:
    """Lazy singleton: returns the module-level TokenBucket."""
    global _singleton
    if _singleton is None:
        _singleton = TokenBucket(
            rate_per_minute=config.max_ops_per_minute,
            burst=config.burst,
        )
    return _singleton


def reset_rate_limiter() -> None:
    """Reset the singleton (for testing)."""
    global _singleton
    _singleton = None
