"""Lightweight httpx-only crawler for public consumers.

:class:`SimpleCrawler` is the "free tier" fetcher: a single GET per URL via
``httpx`` with a realistic desktop Chrome ``User-Agent``, nothing else. It
will get Cloudflare-challenged on Zoopla fairly often; that's deliberate -
the production TLS-impersonating + Playwright-stealth crawler lives in the
private ``uk-property-apify`` repo and powers the paid Apify-hosted actors.

Used by:

* The three Z/RM/OTM MCPs for ``MODE=local``.
* The OSS ``uk-property-agent`` when no ``APIFY_API_TOKEN`` is configured.
* Local development and tests.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

import httpx

from uk_property_listings.types import FetcherError, FetchResult

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_DEFAULT_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8"
)


class SimpleCrawler:
    """Minimal, no-moat crawler backed by a single :class:`httpx.AsyncClient`.

    Intentionally has the same public surface as the private production
    ``Crawler`` (``fetch``, ``close``, async context manager, ``from_env``) so
    the two are drop-in swappable wherever the pagination helpers are called.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        proxy_url: str | None = None,
        request_timeout_s: float = 30.0,
        user_agent: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._request_timeout_s = request_timeout_s
        headers = {
            "User-Agent": user_agent or _DEFAULT_USER_AGENT,
            "Accept": _DEFAULT_ACCEPT,
            "Accept-Language": "en-GB,en;q=0.9",
        }
        if extra_headers:
            headers.update(extra_headers)

        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(request_timeout_s),
                "follow_redirects": True,
                "headers": headers,
            }
            if proxy_url:
                kwargs["proxy"] = proxy_url
            self._client = httpx.AsyncClient(**kwargs)
            self._owns_client = True

    @classmethod
    def from_env(cls) -> SimpleCrawler:
        """Build a :class:`SimpleCrawler` from common environment variables.

        Respects ``HTTP_PROXY``/``HTTPS_PROXY`` and
        ``UK_PROPERTY_CRAWLER_USER_AGENT`` if present.
        """
        proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        user_agent = os.getenv("UK_PROPERTY_CRAWLER_USER_AGENT")
        return cls(proxy_url=proxy_url, user_agent=user_agent)

    async def fetch(
        self,
        url: str,
        *,
        expect_search_markers: bool = False,
    ) -> FetchResult:
        """Fetch ``url`` with a single GET. Raises :class:`FetcherError` on failure.

        ``expect_search_markers`` is accepted for protocol compatibility with
        the production :class:`Crawler` but ignored here - we do not do
        anti-bot classification in the simple tier.
        """
        started = time.perf_counter()
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise FetcherError(f"httpx error for {url}: {exc}") from exc

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            raise FetcherError(
                f"HTTP {response.status_code} for {url}: "
                f"{response.text[:200].strip() or '(empty body)'}"
            )

        return FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            html=response.text,
            captured_at=datetime.now(tz=UTC),
            duration_ms=elapsed_ms,
            headers={k.lower(): v for k, v in response.headers.items()},
        )

    async def close(self) -> None:
        """Close the underlying :class:`httpx.AsyncClient` if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> SimpleCrawler:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
