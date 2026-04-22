"""Async client + register for iamsold (Modern Method of Auction).

Target: ``https://www.iamsold.co.uk/``.

iamsold is the UK's largest Modern Method of Auction platform and its
"auction" surface is different from dated catalogues. Every available
property is a lot on a rolling 30-day marketing window, so we model
the whole ``/available-properties/`` feed as a single synthetic
auction (``auction_id="live"``). Discovery returns one summary;
per-auction fetch paginates the feed page by page.

Pagination uses ``?_page=N`` on the ``/available-properties/``
endpoint. Empty-result pages signal exhaustion.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, ClassVar, Final

import httpx
from uk_property_scrapers.auctions import iamsold as iamsold_parser
from uk_property_scrapers.schema import AuctionHouse, AuctionLot

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.auctions._core import (
    AuctionFetchResult,
    AuctionSummary,
)

_BASE_URL: Final = "https://www.iamsold.co.uk/"
_LIST_PATH: Final = "available-properties/"
_LIVE_AUCTION_ID: Final = "live"

_BROWSER_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


class IamsoldClient(BaseAPIClient):
    """Async HTML client for ``iamsold.co.uk`` available-properties feed."""

    def __init__(
        self,
        *,
        base_url: str = _BASE_URL,
        timeout: float = 45.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        merged = dict(_BROWSER_HEADERS)
        if headers:
            merged.update(headers)
        super().__init__(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=merged,
        )

    async def get_available_page_html(self, *, page: int = 1) -> str:
        """Return one page of HTML from ``/available-properties/``."""

        if page < 1:
            raise ValueError("page must be ≥ 1")
        params: dict[str, Any] | None = None
        if page > 1:
            params = {"_page": page}
        return await self._get_text(_LIST_PATH, params=params)


class IamsoldRegister:
    """``AuctionSourceRegister`` for iamsold.

    iamsold doesn't have per-auction catalogues; we expose a single
    synthetic summary (``auction_id='live'``) so the actor fan-out
    surface is identical to the dated houses.
    """

    source: ClassVar[AuctionHouse] = AuctionHouse.IAMSOLD

    def __init__(self, client: IamsoldClient | None = None) -> None:
        self._client = client or IamsoldClient()
        self._owns_client = client is None

    async def __aenter__(self) -> IamsoldRegister:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client:
            await self._client.__aexit__(*exc)

    async def list_upcoming_auctions(self) -> list[AuctionSummary]:
        return [
            AuctionSummary(
                auction_id=_LIVE_AUCTION_ID,
                source=self.source,
                name="iamsold Available Properties",
                reference=None,
                venue="Online (Modern Method of Auction)",
                extra_context={"list_url": f"{_BASE_URL}{_LIST_PATH}"},
            )
        ]

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
        del summary, available_only, page_size, include_gallery

        list_url = f"{_BASE_URL}{_LIST_PATH}"
        meta = iamsold_parser.build_synthetic_auction_meta(list_url=list_url)

        pages = max_pages or 20
        all_lots: list[AuctionLot] = []
        seen: set[str] = set()
        pages_fetched = 0

        for page_num in range(1, pages + 1):
            html = await self._client.get_available_page_html(page=page_num)
            page_lots = iamsold_parser.parse_available_properties(
                html, list_url=list_url
            )
            pages_fetched += 1
            added = 0
            for lot in page_lots:
                if lot.source_id in seen:
                    continue
                seen.add(lot.source_id)
                all_lots.append(lot)
                added += 1
            if added == 0:
                break

        return AuctionFetchResult(
            source=self.source,
            auction_id=auction_id or _LIVE_AUCTION_ID,
            auction_meta=meta,
            lots=all_lots,
            raw_envelope={"pages_fetched": pages_fetched, "list_url": list_url},
        )


__all__ = [
    "IamsoldClient",
    "IamsoldRegister",
]
