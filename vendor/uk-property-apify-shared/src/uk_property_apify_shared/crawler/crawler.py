"""Top-level orchestrator: HTTP tier first, browser fallback, Discord on fail.

Public entry point is :class:`Crawler`. It owns:

* A :class:`~.types.Transport` (curl_cffi or httpx) used by the HTTP fetcher.
* An :class:`HttpFetcher` for tier-1 requests.
* An :class:`BrowserFetcher` for tier-2 escalation (optional).
* A :class:`~.alerts.AlertSink` for escalation notices.

Typical usage::

    async with Crawler.from_env() as crawler:
        result = await crawler.fetch("https://www.zoopla.co.uk/...")
        listings = parse_search_results(result.html, source_url=result.url)

The :meth:`Crawler.fetch` method returns a :class:`FetchResult`; convenience
wrappers in :mod:`.sites` combine ``fetch`` with the per-site parsers.
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Self

from uk_property_apify_shared.crawler.alerts import AlertSink, DiscordAlertSink, NullAlertSink
from uk_property_apify_shared.crawler.browser_fetcher import BrowserFetcher
from uk_property_apify_shared.crawler.config import CrawlerConfig
from uk_property_apify_shared.crawler.http_fetcher import HttpFetcher
from uk_property_apify_shared.crawler.rate_limit import DomainRateLimiter
from uk_property_apify_shared.crawler.transport import CurlCffiTransport, HttpxTransport
from uk_property_apify_shared.crawler.types import (
    BlockedError,
    FetchAttempt,
    FetchResult,
    TierExhaustedError,
    Transport,
)

logger = logging.getLogger(__name__)


class Crawler:
    """High-level UK-property crawler orchestrating both fetcher tiers."""

    def __init__(
        self,
        *,
        config: CrawlerConfig | None = None,
        transport: Transport | None = None,
        alert_sink: AlertSink | None = None,
        rate_limiter: DomainRateLimiter | None = None,
    ) -> None:
        self._config = config or CrawlerConfig()
        self._owns_transport = transport is None
        self._transport = transport or _default_transport(self._config)
        self._rate_limiter = rate_limiter or DomainRateLimiter(
            default_rate_per_sec=self._config.default_rate_per_sec,
            per_domain_rate_per_sec=self._config.per_domain_rate_per_sec,
        )
        self._alert_sink = alert_sink or self._default_alert_sink()
        self._http_fetcher = HttpFetcher(self._transport, self._config, self._rate_limiter)
        self._browser_fetcher: BrowserFetcher | None = (
            BrowserFetcher(self._config, self._rate_limiter)
            if self._config.enable_browser_fallback
            else None
        )

    @classmethod
    def from_env(cls) -> Crawler:
        """Build from environment variables (see :meth:`CrawlerConfig.from_env`)."""
        return cls(config=CrawlerConfig.from_env())

    @property
    def config(self) -> CrawlerConfig:
        return self._config

    @property
    def alert_sink(self) -> AlertSink:
        return self._alert_sink

    async def fetch(
        self,
        url: str,
        *,
        expect_search_markers: bool = False,
        referer: str | None = None,
    ) -> FetchResult:
        """Fetch ``url``, escalating to the browser tier on HTTP block.

        Raises:
            TierExhaustedError: both tiers failed (or HTTP failed and browser
                fallback is disabled).
        """
        all_attempts: list[FetchAttempt] = []
        try:
            return await self._http_fetcher.fetch(
                url,
                expect_search_markers=expect_search_markers,
                referer=referer,
            )
        except BlockedError as http_block:
            all_attempts.extend(http_block.attempts)
            logger.info(
                "http tier blocked for %s (%s); escalating to browser tier",
                url,
                http_block.signal,
            )
            if self._browser_fetcher is None:
                await self._escalate_alert(url, http_block)
                raise TierExhaustedError(url, all_attempts) from http_block

            try:
                return await self._browser_fetcher.fetch(
                    url,
                    expect_search_markers=expect_search_markers,
                    referer=referer,
                )
            except BlockedError as browser_block:
                all_attempts.extend(browser_block.attempts)
                await self._escalate_alert(url, browser_block, http_block=http_block)
                raise TierExhaustedError(url, all_attempts) from browser_block

    async def close(self) -> None:
        if self._browser_fetcher is not None:
            await self._browser_fetcher.aclose()
        if self._owns_transport:
            await self._transport.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def _escalate_alert(
        self,
        url: str,
        block: BlockedError,
        *,
        http_block: BlockedError | None = None,
    ) -> None:
        fields = {
            "url": url,
            "signal": block.signal,
            "status_code": str(block.status_code or "n/a"),
        }
        if http_block is not None and http_block is not block:
            fields["http_signal"] = http_block.signal
        try:
            await self._alert_sink.alert(
                title="Anti-bot escalation",
                message=f"All crawler tiers failed for `{url}` with signal `{block.signal}`.",
                fields=fields,
            )
        except Exception:
            logger.exception("alert sink raised while escalating %s", url)

    def _default_alert_sink(self) -> AlertSink:
        if self._config.discord_webhook_url:
            return DiscordAlertSink(self._config.discord_webhook_url)
        return NullAlertSink()


def _default_transport(config: CrawlerConfig) -> Transport:
    """Pick the best available transport.

    Prefers :class:`CurlCffiTransport` when ``curl_cffi`` is importable;
    otherwise falls back to :class:`HttpxTransport` so the class remains
    usable in environments where ``curl_cffi`` binary wheels aren't
    available (e.g. CI under a constrained Docker base).

    Respects ``CRAWLER_FORCE_HTTPX=1`` to force the plain-``httpx`` transport
    regardless of ``curl_cffi`` availability - essential in tests that use
    ``respx`` (which can only intercept ``httpx``).
    """
    import os

    if os.environ.get("CRAWLER_FORCE_HTTPX") == "1":
        return HttpxTransport(
            proxy_url=config.proxy_url,
            default_timeout=config.request_timeout_s,
            extra_headers=config.extra_headers,
        )

    try:
        import curl_cffi  # noqa: F401

        return CurlCffiTransport(
            impersonate=config.impersonate,
            proxy_url=config.proxy_url,
            default_timeout=config.request_timeout_s,
            extra_headers=config.extra_headers,
        )
    except ImportError:
        logger.info("curl_cffi unavailable; falling back to httpx transport")
        return HttpxTransport(
            proxy_url=config.proxy_url,
            default_timeout=config.request_timeout_s,
            extra_headers=config.extra_headers,
        )
