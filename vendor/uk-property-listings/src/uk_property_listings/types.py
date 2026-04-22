"""Shared types for listings search: ``SearchQuery``, ``FetchResult``, ``CrawlerProtocol``.

Everything here is pure data or a ``Protocol`` - no network I/O - so both the
public :class:`~uk_property_listings.simple_crawler.SimpleCrawler` and the
private ``uk_property_apify_shared.crawler.Crawler`` can satisfy the same
interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

TransactionKind = Literal["sale", "rent"]


@dataclass(slots=True)
class SearchQuery:
    """Cross-portal search parameters."""

    location: str
    """Free-text town, postcode, or region - interpreted per portal."""
    transaction: TransactionKind = "sale"
    min_price: int | None = None
    max_price: int | None = None
    min_beds: int | None = None
    max_beds: int | None = None
    max_pages: int = 3
    """Upper bound on paginated search pages to fetch."""


@dataclass(slots=True)
class FetchResult:
    """Minimal successful fetch output shared across crawlers.

    The private production :class:`Crawler` returns a richer ``FetchResult``
    (with per-tier attempts, anti-bot signals, etc.) but duck-types to this
    shape for the fields consumers use here (``html``, ``final_url``,
    ``status_code``).
    """

    url: str
    final_url: str
    status_code: int
    html: str
    captured_at: datetime
    duration_ms: int
    headers: dict[str, str] = field(default_factory=dict)


class FetcherError(Exception):
    """Base class for crawler-level failures.

    Pagination helpers catch this to record a per-page error and stop
    paginating for that query. Concrete crawler implementations (including
    the private ``Crawler``) raise subclasses of this.
    """


class CrawlerProtocol(Protocol):
    """Duck-typed crawler interface used by the pagination helpers.

    Implementations MUST be usable as an async context manager (so
    ``async with crawler_factory() as crawler:`` works), but that isn't
    enforced here because :class:`~typing.Protocol` can't express it.
    """

    async def fetch(
        self,
        url: str,
        *,
        expect_search_markers: bool = False,
    ) -> FetchResult:
        """Fetch ``url`` and return a :class:`FetchResult`.

        ``expect_search_markers`` is a hint to the production crawler that
        the response is expected to contain search-result markers (used by
        anti-bot detection on Zoopla / OnTheMarket). Simple crawlers can
        ignore it.
        """
        ...
