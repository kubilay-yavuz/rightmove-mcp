"""Production crawler: anti-bot HTTP + Playwright fallback (private, moat).

The crawler is split across a few small modules so each concern is independently
testable:

* :mod:`config`       - :class:`CrawlerConfig` dataclass (proxy, rates, retries).
* :mod:`types`        - :class:`FetchResult`, :class:`FetchAttempt`, exceptions.
* :mod:`rate_limit`   - per-domain sliding-window async limiter.
* :mod:`antibot`      - HTML-based detection of CF challenges, login walls, etc.
* :mod:`alerts`       - Discord webhook sink for escalation.
* :mod:`transport`    - abstract HTTP :class:`Transport` protocol (curl_cffi prod,
                        httpx tests).
* :mod:`http_fetcher` - tier-1 fetcher: cheap, fast, fingerprint-impersonating HTTP.
* :mod:`browser_fetcher` - tier-2 fetcher: Playwright + stealth, used on tier-1 block.
* :mod:`crawler`      - top-level orchestrator combining both fetchers.

Parsing stays pure (no I/O) in :mod:`uk_property_scrapers`, and the pagination
helpers in :mod:`uk_property_listings.search` are duck-typed against any
crawler with a :meth:`.fetch` method, so the private :class:`Crawler` works
there without changes.
"""

from __future__ import annotations

from uk_property_apify_shared.crawler.alerts import (
    AlertSink,
    DiscordAlertSink,
    NullAlertSink,
)
from uk_property_apify_shared.crawler.antibot import (
    AntiBotSignal,
    AntiBotVerdict,
    classify_response,
)
from uk_property_apify_shared.crawler.browser_fetcher import BrowserFetcher
from uk_property_apify_shared.crawler.config import CrawlerConfig
from uk_property_apify_shared.crawler.crawler import Crawler
from uk_property_apify_shared.crawler.http_fetcher import HttpFetcher
from uk_property_apify_shared.crawler.rate_limit import DomainRateLimiter
from uk_property_apify_shared.crawler.transport import CurlCffiTransport, HttpxTransport
from uk_property_apify_shared.crawler.types import (
    BlockedError,
    FetchAttempt,
    FetcherError,
    FetcherTier,
    FetchResult,
    TierExhaustedError,
    Transport,
    TransportResponse,
)

__all__ = [
    "AlertSink",
    "AntiBotSignal",
    "AntiBotVerdict",
    "BlockedError",
    "BrowserFetcher",
    "Crawler",
    "CrawlerConfig",
    "CurlCffiTransport",
    "DiscordAlertSink",
    "DomainRateLimiter",
    "FetchAttempt",
    "FetchResult",
    "FetcherError",
    "FetcherTier",
    "HttpFetcher",
    "HttpxTransport",
    "NullAlertSink",
    "TierExhaustedError",
    "Transport",
    "TransportResponse",
    "classify_response",
]
