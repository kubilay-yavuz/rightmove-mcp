"""Async token-bucket rate limiter for pacing outbound requests."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


class AsyncTokenBucket:
    """Simple async token bucket.

    Tokens refill continuously at ``rate`` tokens per second up to ``capacity``.
    Call :meth:`acquire` before issuing a request to stay under the configured rate.
    """

    def __init__(self, *, capacity: float, rate: float) -> None:
        if capacity <= 0 or rate <= 0:
            msg = "capacity and rate must be positive"
            raise ValueError(msg)
        self._capacity = capacity
        self._rate = rate
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, *, cost: float = 1.0) -> None:
        """Block until ``cost`` tokens are available, then consume them."""

        if cost <= 0:
            msg = "cost must be positive"
            raise ValueError(msg)
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                self._updated = now
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                deficit = cost - self._tokens
                wait_s = deficit / self._rate
                await asyncio.sleep(wait_s)


def monotonic_clock() -> Callable[[], float]:
    """Return ``time.monotonic`` for easier testing."""

    return time.monotonic
