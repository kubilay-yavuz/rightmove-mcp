"""HTTP transports.

Two adaptors implement the :class:`~uk_property_apify_shared.crawler.types.Transport`
protocol:

* :class:`CurlCffiTransport` - production. Uses ``curl_cffi`` to impersonate a
  real Chrome TLS + HTTP/2 fingerprint, which is the single most effective
  anti-bot evasion for UK portals. Requires the optional ``crawler`` extra.

* :class:`HttpxTransport` - test/dev. Plain :mod:`httpx` so ``respx`` can mock
  responses without spinning up a real network stack. Also useful behind
  an MITM proxy for debugging.

Both return a :class:`~uk_property_apify_shared.crawler.types.TransportResponse`,
never a transport-specific type.
"""

from __future__ import annotations

import time
from typing import Any

from uk_property_apify_shared.crawler.types import TransportResponse


class HttpxTransport:
    """httpx-based transport. Preferred in tests."""

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        default_timeout: float = 30.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        import httpx

        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(default_timeout),
            "follow_redirects": True,
            "headers": dict(extra_headers or {}),
        }
        if proxy_url:
            kwargs["proxy"] = proxy_url
        self._client = httpx.AsyncClient(**kwargs)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        import httpx

        timeout_obj = httpx.Timeout(timeout) if timeout else None
        started = time.perf_counter()
        response = await self._client.request(
            method,
            url,
            headers=headers,
            timeout=timeout_obj,
        )
        elapsed = time.perf_counter() - started
        return TransportResponse(
            status_code=response.status_code,
            url=str(response.url),
            headers={k.lower(): v for k, v in response.headers.items()},
            text=response.text,
            content=response.content,
            elapsed_s=elapsed,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class CurlCffiTransport:
    """curl_cffi-based transport - impersonates real Chrome TLS fingerprint.

    This is the default production transport. Blocked responses from TLS
    fingerprinting WAFs (Cloudflare, Akamai) drop dramatically once you use
    this vs vanilla :mod:`httpx`.
    """

    def __init__(
        self,
        *,
        impersonate: str = "chrome124",
        proxy_url: str | None = None,
        default_timeout: float = 30.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError as exc:
            raise RuntimeError(
                "curl_cffi is required for CurlCffiTransport - install with "
                "`pip install uk-property-scrapers[crawler]`"
            ) from exc
        kwargs: dict[str, Any] = {
            "impersonate": impersonate,
            "timeout": default_timeout,
            "allow_redirects": True,
        }
        if proxy_url:
            kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
        if extra_headers:
            kwargs["headers"] = dict(extra_headers)
        self._session = AsyncSession(**kwargs)

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        started = time.perf_counter()
        response = await self._session.request(
            method,
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        )
        elapsed = time.perf_counter() - started
        text: str
        try:
            text = response.text
        except UnicodeDecodeError:
            text = response.content.decode("utf-8", errors="replace")
        return TransportResponse(
            status_code=response.status_code,
            url=str(response.url),
            headers={k.lower(): v for k, v in response.headers.items()},
            text=text,
            content=response.content,
            elapsed_s=elapsed,
        )

    async def aclose(self) -> None:
        await self._session.close()
