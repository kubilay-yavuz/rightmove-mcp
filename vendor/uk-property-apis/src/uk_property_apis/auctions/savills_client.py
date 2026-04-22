"""Async client + register for Savills Auctions.

Target: ``https://auctions.savills.co.uk/``.

Savills' catalogue is server-rendered PHP with Vue.js hydration —
every lot is already in the initial HTML, so we fetch each page of
the catalogue with a single GET.

Two pages matter:

* ``/upcoming-auctions`` — list of future auctions, each with a
  calendar card carrying the date / venue / lot count and a link to
  the catalogue URL (``…/auctions/{slug}-{id}``).
* ``/auctions/{slug}-{id}[/page-N/quantity-100]`` — the catalogue
  itself. Default is 10 lots per page; the ``quantity-100`` path
  component bumps it to 100, which is what we use.

We continue fetching pages until the parser returns zero new lots
on a page *and* the server stopped rendering ``li.lot`` markers —
indexed pagination means we always know exactly how many pages
there are (``/page-N`` links) but following ``total / 100 + 1`` is
simpler and equivalent in practice.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any, ClassVar, Final

import httpx
from uk_property_scrapers.auctions import savills as savills_parser
from uk_property_scrapers.schema import AuctionHouse, AuctionLot

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import NotFoundError, ValidationError
from uk_property_apis.auctions._core import (
    AuctionFetchResult,
    AuctionSummary,
)

_BASE_URL: Final = "https://auctions.savills.co.uk/"
_UPCOMING_PATH: Final = "upcoming-auctions"
_CATALOGUE_QTY: Final = 100

_BROWSER_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

_TOTAL_PAGES_RE: Final = re.compile(
    r"/page-(\d+)/quantity-\d+", re.IGNORECASE
)


class SavillsAuctionsClient(BaseAPIClient):
    """Async HTML client for ``auctions.savills.co.uk``.

    Inherits retries / semaphore / timeouts from :class:`BaseAPIClient`.
    Browser headers are mandatory — bare ``httpx`` UAs get a 403 on
    the upstream CDN.
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

    async def get_upcoming_html(self) -> str:
        """Return raw HTML for ``/upcoming-auctions``."""

        return await self._get_text(_UPCOMING_PATH)

    async def get_catalogue_page_html(
        self,
        *,
        slug: str,
        auction_id: str,
        page: int = 1,
        quantity: int = _CATALOGUE_QTY,
    ) -> str:
        """Return HTML for one page of a Savills catalogue."""

        slug = slug.strip().strip("/")
        if not slug:
            raise ValueError("slug must be non-empty")
        if page < 1:
            raise ValueError("page must be ≥ 1")
        if quantity < 1:
            raise ValueError("quantity must be ≥ 1")
        path = f"auctions/{slug}-{auction_id}/page-{page}/quantity-{quantity}"
        return await self._get_text(path)


class SavillsRegister:
    """``AuctionSourceRegister`` for Savills Auctions."""

    source: ClassVar[AuctionHouse] = AuctionHouse.SAVILLS_AUCTIONS

    def __init__(self, client: SavillsAuctionsClient | None = None) -> None:
        self._client = client or SavillsAuctionsClient()
        self._owns_client = client is None
        self._summary_cache: dict[str, AuctionSummary] = {}

    async def __aenter__(self) -> SavillsRegister:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client:
            await self._client.__aexit__(*exc)

    async def list_upcoming_auctions(self) -> list[AuctionSummary]:
        html = await self._client.get_upcoming_html()
        records = savills_parser.parse_upcoming_auctions(html)
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
                    "slug": rec.get("slug"),
                    "href": rec.get("href"),
                    "lot_count_hint": rec.get("lot_count_hint"),
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
        page_size: int = _CATALOGUE_QTY,
        include_gallery: bool = False,
    ) -> AuctionFetchResult:
        del available_only, include_gallery

        resolved = summary or self._summary_cache.get(auction_id)
        if resolved is None:
            await self.list_upcoming_auctions()
            resolved = self._summary_cache.get(auction_id)
        if resolved is None:
            raise NotFoundError(
                f"Savills auction {auction_id!r} not found in upcoming feed"
            )

        slug = str(resolved.extra_context.get("slug") or "").strip()
        if not slug:
            raise ValidationError(
                f"Savills auction {auction_id!r} missing slug context"
            )

        auction_url = f"{_BASE_URL}auctions/{slug}-{auction_id}"

        first_html = await self._client.get_catalogue_page_html(
            slug=slug, auction_id=auction_id, page=1, quantity=page_size
        )
        meta = savills_parser.parse_auction_metadata(
            first_html, auction_url=auction_url
        )
        if resolved.venue:
            meta.setdefault("venue", resolved.venue)

        all_lots: list[AuctionLot] = list(
            savills_parser.parse_catalogue_html(
                first_html,
                auction_url=auction_url,
                auction_meta=meta,
            )
        )

        total_pages = _infer_total_pages(first_html)
        if max_pages is not None:
            total_pages = min(total_pages, max_pages)
        seen: set[str] = {lot.source_id for lot in all_lots}

        for page_num in range(2, total_pages + 1):
            page_html = await self._client.get_catalogue_page_html(
                slug=slug,
                auction_id=auction_id,
                page=page_num,
                quantity=page_size,
            )
            page_lots = savills_parser.parse_catalogue_html(
                page_html,
                auction_url=auction_url,
                auction_meta=meta,
            )
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
            auction_id=auction_id,
            auction_meta=meta,
            lots=all_lots,
            raw_envelope={"pages_fetched": total_pages, "slug": slug},
        )


def _infer_total_pages(html: str) -> int:
    """Return the largest page number referenced in the pagination links."""

    pages = [int(m.group(1)) for m in _TOTAL_PAGES_RE.finditer(html)]
    if pages:
        return max(pages)
    return 1


__all__ = [
    "SavillsAuctionsClient",
    "SavillsRegister",
]
