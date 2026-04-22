"""Tier-1 fetcher: fast, cheap, fingerprint-impersonating HTTP.

Handles the common case - a real Chrome-shaped request succeeds on 80 % of
portal URLs without spinning up a browser. Anything that classifies as
blocked bubbles up to the orchestrator, which may decide to escalate.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime
from urllib.parse import urlparse

from uk_property_apify_shared.crawler.antibot import AntiBotSignal, classify_response
from uk_property_apify_shared.crawler.config import CrawlerConfig
from uk_property_apify_shared.crawler.rate_limit import DomainRateLimiter
from uk_property_apify_shared.crawler.types import (
    BlockedError,
    FetchAttempt,
    FetcherTier,
    FetchResult,
    Transport,
)

logger = logging.getLogger(__name__)


class HttpFetcher:
    """Async HTTP fetcher with rate limiting, retries, and anti-bot detection."""

    def __init__(
        self,
        transport: Transport,
        config: CrawlerConfig,
        rate_limiter: DomainRateLimiter,
    ) -> None:
        self._transport = transport
        self._config = config
        self._limiter = rate_limiter
        self._warmed: set[str] = set()

    async def fetch(
        self,
        url: str,
        *,
        expect_search_markers: bool = False,
        referer: str | None = None,
    ) -> FetchResult:
        """Fetch ``url`` with retries. Raises :class:`BlockedError` on block."""
        attempts: list[FetchAttempt] = []
        last_exception: Exception | None = None
        domain = _host(url)

        if self._config.warm_session and domain not in self._warmed:
            await self._warm(domain)

        for attempt_num in range(1, self._config.max_retries + 1):
            await self._limiter.wait(domain)
            headers = self._build_headers(referer=referer)
            started_at = datetime.now(UTC)
            started_mono = asyncio.get_running_loop().time()
            try:
                response = await self._transport.request(
                    "GET",
                    url,
                    headers=headers,
                    timeout=self._config.request_timeout_s,
                )
            except Exception as exc:
                last_exception = exc
                duration_ms = int((asyncio.get_running_loop().time() - started_mono) * 1000)
                attempts.append(
                    FetchAttempt(
                        tier=FetcherTier.HTTP,
                        started_at=started_at,
                        duration_ms=duration_ms,
                        status_code=None,
                        final_url=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                logger.warning(
                    "http_fetcher transport error (attempt %d/%d) %s: %s",
                    attempt_num,
                    self._config.max_retries,
                    url,
                    exc,
                )
                await self._backoff(attempt_num)
                continue

            duration_ms = int(response.elapsed_s * 1000)
            verdict = classify_response(
                status_code=response.status_code,
                html=response.text,
                headers=response.headers,
                expect_search_markers=expect_search_markers,
            )

            if verdict.blocked:
                attempts.append(
                    FetchAttempt(
                        tier=FetcherTier.HTTP,
                        started_at=started_at,
                        duration_ms=duration_ms,
                        status_code=response.status_code,
                        final_url=response.url,
                        error=verdict.reason,
                        anti_bot_signal=verdict.signal.value,
                    )
                )
                logger.warning(
                    "http_fetcher blocked (attempt %d/%d) %s: %s",
                    attempt_num,
                    self._config.max_retries,
                    url,
                    verdict.reason,
                )
                if verdict.signal in (AntiBotSignal.RATE_LIMITED,):
                    await self._backoff(attempt_num)
                    continue
                raise BlockedError(
                    verdict.signal.value,
                    url=url,
                    status_code=response.status_code,
                    attempts=attempts,
                )

            if 500 <= response.status_code < 600:
                attempts.append(
                    FetchAttempt(
                        tier=FetcherTier.HTTP,
                        started_at=started_at,
                        duration_ms=duration_ms,
                        status_code=response.status_code,
                        final_url=response.url,
                        error=f"HTTP {response.status_code}",
                    )
                )
                await self._backoff(attempt_num)
                continue

            attempts.append(
                FetchAttempt(
                    tier=FetcherTier.HTTP,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    status_code=response.status_code,
                    final_url=response.url,
                )
            )
            return FetchResult(
                url=url,
                final_url=response.url,
                status_code=response.status_code,
                html=response.text,
                tier=FetcherTier.HTTP,
                captured_at=started_at,
                duration_ms=duration_ms,
                attempts=attempts,
                headers=response.headers,
            )

        raise BlockedError(
            "transport_exhausted",
            url=url,
            status_code=None,
            attempts=attempts,
        ) from last_exception

    async def _warm(self, domain: str) -> None:
        """Visit ``https://{domain}/`` to collect cookies, once per domain."""
        try:
            await self._limiter.wait(domain)
            await self._transport.request(
                "GET",
                f"https://{domain}/",
                headers=self._build_headers(referer=None),
                timeout=self._config.request_timeout_s,
            )
        except Exception as exc:
            logger.debug("warm-up for %s failed (ignored): %s", domain, exc)
        finally:
            self._warmed.add(domain)

    def _build_headers(self, *, referer: str | None) -> dict[str, str]:
        user_agent = random.choice(self._config.user_agents)
        headers = {
            "User-Agent": user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": self._config.accept_language,
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Cache-Control": "no-cache",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if referer else "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        if referer:
            headers["Referer"] = referer
        if self._config.extra_headers:
            headers.update(self._config.extra_headers)
        return headers

    async def _backoff(self, attempt_num: int) -> None:
        delay = min(
            self._config.backoff_initial_s * (2 ** (attempt_num - 1)),
            self._config.backoff_max_s,
        )
        jitter = delay * 0.2 * random.random()
        await asyncio.sleep(delay + jitter)


def _host(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower()
