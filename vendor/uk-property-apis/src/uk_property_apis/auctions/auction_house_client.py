"""Async client + register for Auction House UK.

Target: ``https://www.auctionhouse.co.uk/``.

Auction House UK is a federated network of regional auctioneers
sharing a single server-rendered site. Two pages drive everything
we need:

* ``/auction/future-auction-dates`` — one HTML table with every
  upcoming regional auction (branch + date + catalogue link).
* ``/{branch}/auction/lots/{auction_id}`` — the regional catalogue
  page; a single GET returns every lot on that auction in-HTML.

There's no pagination on the catalogue, so per-auction fetches are a
single ``_get_text`` + :mod:`uk_property_scrapers.auctions.auction_house`
parse. The register implements
:class:`uk_property_apis.auctions.AuctionSourceRegister` so the
``uk-auctions`` Apify actor can dispatch across multiple sources via
one code path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, ClassVar, Final

import httpx
from uk_property_scrapers.auctions import auction_house as auction_house_parser
from uk_property_scrapers.schema import AuctionHouse

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import NotFoundError, ValidationError
from uk_property_apis.auctions._core import (
    AuctionFetchResult,
    AuctionSummary,
)

_BASE_URL: Final = "https://www.auctionhouse.co.uk/"
_FUTURE_PATH: Final = "auction/future-auction-dates"

_BROWSER_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


class AuctionHouseClient(BaseAPIClient):
    """Async HTML client for ``auctionhouse.co.uk``.

    Inherits the shared retry / rate-limit / timeout layer so one
    flaky branch doesn't take down a multi-auction run. Holds
    browser-style headers because the upstream CDN 403s bare
    non-browser UAs.
    """

    def __init__(
        self,
        *,
        base_url: str = _BASE_URL,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        merged_headers = dict(_BROWSER_HEADERS)
        if headers:
            merged_headers.update(headers)
        super().__init__(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=merged_headers,
        )

    async def get_future_auctions_html(self) -> str:
        """Return the raw HTML for ``/auction/future-auction-dates``."""

        return await self._get_text(_FUTURE_PATH)

    async def get_catalogue_html(self, *, branch: str, auction_id: str) -> str:
        """Return the raw HTML for one regional auction catalogue.

        ``branch`` is the lowercase slug from the discovery row
        (``london``, ``manchester``, ``scotland`` …). ``auction_id``
        is the numeric id from the URL.
        """

        branch = branch.strip().lower()
        auction_id = auction_id.strip()
        if not branch or not auction_id:
            raise ValueError("branch and auction_id must be non-empty strings")
        path = f"{branch}/auction/lots/{auction_id}"
        return await self._get_text(path)


class AuctionHouseRegister:
    """``AuctionSourceRegister`` for Auction House UK.

    Discovery normalises the future-auction-dates table into
    :class:`AuctionSummary` objects whose ``extra_context`` carries
    the branch slug — :meth:`fetch_auction` needs it to resolve the
    catalogue URL. When the caller supplies an auction id without
    discovery context we fall back to a one-shot future-feed lookup.
    """

    source: ClassVar[AuctionHouse] = AuctionHouse.AUCTION_HOUSE_UK

    def __init__(self, client: AuctionHouseClient | None = None) -> None:
        self._client = client or AuctionHouseClient()
        self._owns_client = client is None
        self._summary_cache: dict[str, AuctionSummary] = {}

    async def __aenter__(self) -> AuctionHouseRegister:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client:
            await self._client.__aexit__(*exc)

    async def list_upcoming_auctions(self) -> list[AuctionSummary]:
        html = await self._client.get_future_auctions_html()
        records = auction_house_parser.parse_future_auctions(html)
        summaries: list[AuctionSummary] = []
        for rec in records:
            summary = AuctionSummary(
                auction_id=str(rec.get("auction_id")),
                source=self.source,
                name=rec.get("name"),
                reference=None,
                auction_date_iso=rec.get("auction_date"),
                second_day_iso=None,
                venue=rec.get("venue"),
                raw=rec,
                extra_context={
                    "branch": rec.get("branch"),
                    "href": rec.get("href"),
                },
            )
            summaries.append(summary)
            self._summary_cache[summary.auction_id] = summary
        return summaries

    async def fetch_auction(
        self,
        auction_id: str,
        *,
        summary: AuctionSummary | None = None,
        available_only: bool | None = None,
        max_pages: int | None = None,
        page_size: int = 100,
        include_gallery: bool = False,
    ) -> AuctionFetchResult:
        del available_only, max_pages, page_size, include_gallery

        resolved = summary or self._summary_cache.get(auction_id)
        if resolved is None:
            await self.list_upcoming_auctions()
            resolved = self._summary_cache.get(auction_id)
        if resolved is None:
            raise NotFoundError(
                f"Auction House UK auction {auction_id!r} not found in discovery feed"
            )

        branch = str(resolved.extra_context.get("branch") or "").strip()
        if not branch:
            raise ValidationError(
                f"Auction House UK auction {auction_id!r} missing branch context"
            )

        html = await self._client.get_catalogue_html(
            branch=branch, auction_id=auction_id
        )

        auction_url = f"{_BASE_URL}{branch}/auction/lots/{auction_id}"
        meta = auction_house_parser.parse_auction_metadata(
            html, auction_url=auction_url
        )
        if resolved.name and not meta.get("name"):
            meta["name"] = resolved.name
        if resolved.venue:
            meta.setdefault("venue", resolved.venue)
        lots = auction_house_parser.parse_catalogue_html(
            html, auction_url=auction_url, auction_meta=meta
        )

        return AuctionFetchResult(
            source=self.source,
            auction_id=auction_id,
            auction_meta=meta,
            lots=lots,
            raw_envelope={"html_length": len(html), "branch": branch},
        )


__all__ = [
    "AuctionHouseClient",
    "AuctionHouseRegister",
]
