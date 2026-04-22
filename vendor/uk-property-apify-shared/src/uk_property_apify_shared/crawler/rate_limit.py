"""Async per-domain sliding-window rate limiter.

We deliberately avoid token-bucket drift by recording individual request
timestamps in a deque; for the volumes we scrape (a few hundred requests per
session at < 2 rps per domain) the memory cost is trivial and the semantics
are easier to reason about.

Usage::

    limiter = DomainRateLimiter(default_rate_per_sec=1.0)
    async with limiter.acquire("www.zoopla.co.uk"):
        ...

or equivalently::

    await limiter.wait("www.zoopla.co.uk")
    ...
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class DomainRateLimiter:
    """Enforce a maximum requests-per-second per domain."""

    def __init__(
        self,
        *,
        default_rate_per_sec: float = 1.0,
        per_domain_rate_per_sec: dict[str, float] | None = None,
        time_source: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if default_rate_per_sec <= 0:
            raise ValueError("default_rate_per_sec must be positive")
        self._default_rate = default_rate_per_sec
        self._per_domain = dict(per_domain_rate_per_sec or {})
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._loop = time_source

    def _now(self) -> float:
        loop = self._loop or asyncio.get_running_loop()
        return loop.time()

    def _rate_for(self, domain: str) -> float:
        return self._per_domain.get(domain, self._default_rate)

    def _min_gap_s(self, domain: str) -> float:
        return 1.0 / self._rate_for(domain)

    async def wait(self, domain: str) -> None:
        """Block until it is polite to make the next request to ``domain``."""
        async with self._locks[domain]:
            gap = self._min_gap_s(domain)
            window = self._windows[domain]
            now = self._now()
            horizon = now - 1.0
            while window and window[0] < horizon:
                window.popleft()
            rate = self._rate_for(domain)
            if len(window) >= rate:
                wait_for = max(0.0, window[0] + 1.0 - now)
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
                    now = self._now()
                    horizon = now - 1.0
                    while window and window[0] < horizon:
                        window.popleft()
            if window:
                since_last = now - window[-1]
                if since_last < gap:
                    await asyncio.sleep(gap - since_last)
                    now = self._now()
            window.append(now)

    @asynccontextmanager
    async def acquire(self, domain: str) -> AsyncIterator[None]:
        """Context manager flavour of :meth:`wait`."""
        await self.wait(domain)
        yield

    def override(self, domain: str, rate_per_sec: float) -> None:
        """Runtime tweak - useful when a Discord alert asks you to slow down."""
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self._per_domain[domain] = rate_per_sec

    def snapshot(self, domain: str) -> list[float]:
        """Return a copy of the recorded timestamps for ``domain`` (tests)."""
        return list(self._windows[domain])
