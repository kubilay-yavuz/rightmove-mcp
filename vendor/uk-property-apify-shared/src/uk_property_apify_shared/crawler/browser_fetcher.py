"""Tier-2 fetcher: Playwright + stealth patches.

Used as a fallback when :class:`~.http_fetcher.HttpFetcher` is blocked. The
browser fetcher is far slower (~2-5 s per page) but clears most JS challenges
that fingerprint-only attacks hit.

The Playwright import is intentionally lazy so the ``crawler`` package still
imports cleanly without the optional ``crawler`` extra installed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from uk_property_apify_shared.crawler.antibot import classify_response
from uk_property_apify_shared.crawler.config import CrawlerConfig
from uk_property_apify_shared.crawler.rate_limit import DomainRateLimiter
from uk_property_apify_shared.crawler.types import (
    BlockedError,
    FetchAttempt,
    FetcherTier,
    FetchResult,
)

if TYPE_CHECKING:
    from playwright.async_api import Browser, Playwright

logger = logging.getLogger(__name__)


_STEALTH_INIT_JS = """
() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en'] });
  Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map((_, i) => ({ name: `plugin-${i}` })),
  });
  Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
  window.chrome = window.chrome || { runtime: {}, loadTimes: () => ({}), csi: () => ({}) };
  const origQuery = (navigator.permissions || {}).query;
  if (origQuery) {
    navigator.permissions.query = (params) =>
      params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(params);
  }
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, p);
  };
}
"""


class BrowserFetcher:
    """Playwright-based fetcher. Lazy-starts the browser on first fetch."""

    def __init__(
        self,
        config: CrawlerConfig,
        rate_limiter: DomainRateLimiter,
    ) -> None:
        self._config = config
        self._limiter = rate_limiter
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def fetch(
        self,
        url: str,
        *,
        expect_search_markers: bool = False,
        referer: str | None = None,
    ) -> FetchResult:
        """Open a Playwright page, navigate, return HTML."""
        from playwright.async_api import Error as PlaywrightError

        await self._ensure_browser()
        assert self._browser is not None

        domain = _host(url)
        attempts: list[FetchAttempt] = []

        for attempt_num in range(1, self._config.max_retries + 1):
            await self._limiter.wait(domain)
            started_at = datetime.now(UTC)
            started_mono = asyncio.get_running_loop().time()
            user_agent = random.choice(self._config.user_agents)
            context = await self._browser.new_context(
                user_agent=user_agent,
                viewport={
                    "width": self._config.viewport_width,
                    "height": self._config.viewport_height,
                },
                locale="en-GB",
                extra_http_headers={
                    "Accept-Language": self._config.accept_language,
                    **self._config.extra_headers,
                },
            )
            await context.add_init_script(_STEALTH_INIT_JS)
            page = await context.new_page()
            try:
                if self._config.warm_session:
                    try:
                        await page.goto(
                            f"https://{domain}/",
                            timeout=self._config.request_timeout_s * 1000,
                            wait_until="domcontentloaded",
                        )
                        await asyncio.sleep(0.8 + random.random() * 0.6)
                    except PlaywrightError as exc:
                        logger.debug("browser warm-up failed (ignored): %s", exc)

                response = await page.goto(
                    url,
                    timeout=self._config.request_timeout_s * 1000,
                    wait_until="domcontentloaded",
                    referer=referer,
                )
                await asyncio.sleep(0.4 + random.random() * 0.6)
                html = await page.content()
                status_code = response.status if response else 200
                final_url = page.url
                headers = (
                    {k.lower(): v for k, v in (await response.all_headers()).items()}
                    if response
                    else {}
                )
            except PlaywrightError as exc:
                duration_ms = int((asyncio.get_running_loop().time() - started_mono) * 1000)
                attempts.append(
                    FetchAttempt(
                        tier=FetcherTier.BROWSER,
                        started_at=started_at,
                        duration_ms=duration_ms,
                        status_code=None,
                        final_url=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                await context.close()
                logger.warning(
                    "browser_fetcher error (attempt %d/%d) %s: %s",
                    attempt_num,
                    self._config.max_retries,
                    url,
                    exc,
                )
                await self._backoff(attempt_num)
                continue
            finally:
                with contextlib.suppress(Exception):
                    await page.close()

            await context.close()

            duration_ms = int((asyncio.get_running_loop().time() - started_mono) * 1000)
            verdict = classify_response(
                status_code=status_code,
                html=html,
                headers=headers,
                expect_search_markers=expect_search_markers,
            )
            if verdict.blocked:
                attempts.append(
                    FetchAttempt(
                        tier=FetcherTier.BROWSER,
                        started_at=started_at,
                        duration_ms=duration_ms,
                        status_code=status_code,
                        final_url=final_url,
                        error=verdict.reason,
                        anti_bot_signal=verdict.signal.value,
                    )
                )
                if attempt_num < self._config.max_retries:
                    await self._backoff(attempt_num)
                    continue
                raise BlockedError(
                    verdict.signal.value,
                    url=url,
                    status_code=status_code,
                    attempts=attempts,
                )

            attempts.append(
                FetchAttempt(
                    tier=FetcherTier.BROWSER,
                    started_at=started_at,
                    duration_ms=duration_ms,
                    status_code=status_code,
                    final_url=final_url,
                )
            )
            return FetchResult(
                url=url,
                final_url=final_url,
                status_code=status_code,
                html=html,
                tier=FetcherTier.BROWSER,
                captured_at=started_at,
                duration_ms=duration_ms,
                attempts=attempts,
                headers=headers,
            )

        raise BlockedError(
            "browser_exhausted",
            url=url,
            status_code=None,
            attempts=attempts,
        )

    async def aclose(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    async def _ensure_browser(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "Playwright is required for BrowserFetcher - install with "
                    "`pip install uk-property-scrapers[crawler]` and run "
                    "`playwright install chromium`."
                ) from exc

            self._playwright = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {
                "headless": self._config.browser_headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            }
            if self._config.proxy_url:
                launch_kwargs["proxy"] = {"server": self._config.proxy_url}
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)

    async def _backoff(self, attempt_num: int) -> None:
        delay = min(
            self._config.backoff_initial_s * (2 ** (attempt_num - 1)),
            self._config.backoff_max_s,
        )
        await asyncio.sleep(delay + delay * 0.2 * random.random())


def _host(url: str) -> str:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower()
