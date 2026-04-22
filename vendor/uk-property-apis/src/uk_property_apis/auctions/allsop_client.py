"""Async client for the Allsop LLP auction JSON API.

Target: ``https://www.allsop.co.uk/``.

Allsop runs a public Angular SPA for ``www.allsop.co.uk`` whose
property-search, auction-detail, and carousel views all hit the same
JSON backend. HTML scraping returns an empty shell (client-rendered),
so this client targets the underlying JSON routes directly.

Endpoints in use::

    GET /api/auctions/upcoming      → {"residential": [...], "commercial": [...]}
    GET /api/auctions/current       → {"data": {"next_residential_auction": {...},
                                                  "next_commercial_auction": {...}}}
    GET /api/auctions/{auction_id}  → {"auctionData": {...}, ...}
    GET /api/search                 → {"data": {"results": [...], "total": N}}

The search endpoint is the pagination hot path: it takes query-string
filters (``auction_id``, ``lot_type``, ``available_only``, ``page``,
``size``) and returns up to ``size`` lots per page. The default page
size is 20; we've confirmed ``size=500`` works reliably (a single
auction typically has 150-400 lots, so one call per auction is
sufficient for live-auction fan-outs).

Authentication is **not** required. The Angular SPA appends ``?react``
as a cache-buster; we forward that for parity with the first-party
client.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Final, Literal

import httpx
from uk_property_scrapers.schema import AuctionHouse

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.auctions._core import (
    AuctionFetchResult,
    AuctionSummary,
)

_BASE_URL: Final = "https://www.allsop.co.uk/"
_UPCOMING_PATH: Final = "api/auctions/upcoming"
_CURRENT_PATH: Final = "api/auctions/current"
_AUCTION_PATH: Final = "api/auctions"
_SEARCH_PATH: Final = "api/search"
_LOT_DETAIL_PATH: Final = "api/lot/reference"

_CACHE_BUSTER: Final = {"react": ""}
"""Every browser request carries ``?react`` — forward it so the CDN
serves the same cached variant the SPA sees."""


LotType = Literal["residential", "commercial"]


def _summary_from_allsop_raw(raw: Mapping[str, Any]) -> AuctionSummary:
    """Build the shared :class:`AuctionSummary` from one Allsop upcoming entry."""

    value_sold = raw.get("allsop_auctionvaluesold")
    value_sold_pence: int | None
    if isinstance(value_sold, (int, float)) and value_sold > 0:
        value_sold_pence = round(float(value_sold) * 100)
    else:
        value_sold_pence = None

    return AuctionSummary(
        auction_id=str(raw["allsop_auctionid"]),
        source=AuctionHouse.ALLSOP,
        name=_string_or_none(raw.get("allsop_name")),
        reference=_string_or_none(raw.get("allsop_auctionreference")),
        auction_date_iso=_string_or_none(raw.get("allsop_auctiondate")),
        second_day_iso=_string_or_none(raw.get("allsop_auctiondate2")),
        venue=_string_or_none(raw.get("allsop_venue")),
        lots_sold=_int_or_none(raw.get("allsop_auctionlotssold")),
        lots_unsold=_int_or_none(raw.get("allsop_auctionlotsunsold")),
        value_sold_pence=value_sold_pence,
        raw=dict(raw),
    )


@dataclass(frozen=True, slots=True)
class UpcomingAuctions:
    """Residential + commercial auction summaries from ``/api/auctions/upcoming``."""

    residential: tuple[AuctionSummary, ...]
    commercial: tuple[AuctionSummary, ...]

    def all(self, *, lot_type: LotType | None = None) -> tuple[AuctionSummary, ...]:
        """Flatten residential + commercial summaries into one ordered tuple.

        Use ``lot_type`` to restrict to just one side; omit it to get
        everything (residential first, then commercial) — handy for the
        actor fan-out when the caller didn't specify a side.
        """

        if lot_type == "residential":
            return self.residential
        if lot_type == "commercial":
            return self.commercial
        return self.residential + self.commercial


@dataclass(frozen=True, slots=True)
class AllsopSearchPage:
    """Single page of lot search results.

    ``results`` are raw Allsop dicts, not normalised — parse them with
    :func:`uk_property_scrapers.auctions.allsop.parse_search_results`
    when you need :class:`AuctionLot` rows.
    """

    page: int
    size: int
    total: int
    results: tuple[Mapping[str, Any], ...]

    @property
    def has_next(self) -> bool:
        """Does another page of results exist after this one?"""

        fetched_through = self.page * self.size
        return fetched_through < self.total


class AllsopClient(BaseAPIClient):
    """Async client for the ``www.allsop.co.uk`` JSON API.

    Auth-free. Callers should either use ``async with`` or remember to
    :meth:`aclose` — keeping the underlying httpx client open lets the
    same connection carry multiple paginated search calls, which is
    noticeably faster on big auctions (200+ lots).
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
        super().__init__(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    async def list_upcoming_auctions(self) -> UpcomingAuctions:
        """Fetch upcoming auctions, split by lot type.

        The upstream feed returns two arrays ordered chronologically.
        Items pending cut-off (still accepting lots) and imminent
        auctions both appear — callers that only want the next live
        sale should take ``residential[0]`` / ``commercial[0]``.
        """

        payload = await self._get(_UPCOMING_PATH, params=_CACHE_BUSTER)
        res_raw = payload.get("residential") or []
        com_raw = payload.get("commercial") or []
        residential = tuple(
            _summary_from_allsop_raw(item)
            for item in res_raw
            if isinstance(item, dict) and "allsop_auctionid" in item
        )
        commercial = tuple(
            _summary_from_allsop_raw(item)
            for item in com_raw
            if isinstance(item, dict) and "allsop_auctionid" in item
        )
        return UpcomingAuctions(residential=residential, commercial=commercial)

    async def get_auction(self, auction_id: str) -> dict[str, Any]:
        """Fetch the full envelope for one auction.

        Returns the raw ``{"auctionData": {...}, ...}`` dict — feed it
        to :func:`uk_property_scrapers.auctions.allsop.parse_auction_metadata`
        for the normalised summary shape.
        """

        auction_id = auction_id.strip()
        if not auction_id:
            raise ValueError("auction_id must be a non-empty string")
        path = f"{_AUCTION_PATH}/{auction_id}"
        return await self._get(path, params=_CACHE_BUSTER)

    async def get_lot_detail(self, reference: str) -> dict[str, Any]:
        """Fetch the full envelope for a single lot.

        ``reference`` is the catalogue reference as it appears in the
        lot URL (lowercase, hyphen-separated) — e.g. ``"r260430-098"``
        for the ``R260430 098`` lot. Callers that have the raw feed
        reference (``"R260430 098"``) should normalise it first with
        :func:`_normalise_lot_reference`, which mirrors the slugify
        done on the lot-overview URL.

        The response has a ~20-field top-level envelope with keys
        like ``version``, ``images``, ``legal_documents``,
        ``description``, ``auction``; the ``images`` array is the
        one callers typically want — feed it to
        :func:`uk_property_scrapers.auctions.allsop.parse_lot_gallery`
        for the full photo gallery.
        """

        slug = _normalise_lot_reference(reference)
        if not slug:
            raise ValueError("reference must be a non-empty string")
        path = f"{_LOT_DETAIL_PATH}/{slug}"
        return await self._get(path, params=_CACHE_BUSTER)

    async def search_page(
        self,
        *,
        auction_id: str | None = None,
        lot_type: LotType | None = None,
        available_only: bool | None = None,
        page: int = 1,
        size: int = 100,
        extra_params: Mapping[str, str | int | bool] | None = None,
    ) -> AllsopSearchPage:
        """Fetch a single page of lots from ``/api/search``.

        ``extra_params`` is an escape hatch for passing lesser-used
        Allsop filters (e.g. ``sortOrder``, ``keyword``, ``postcode``)
        without baking every flag into the signature.
        """

        if page < 1:
            raise ValueError("page must be ≥ 1")
        if size < 1:
            raise ValueError("size must be ≥ 1")

        params: dict[str, str | int] = {"page": page, "size": size, "react": ""}
        if auction_id:
            params["auction_id"] = auction_id
        if lot_type:
            params["lot_type"] = lot_type
        if available_only is not None:
            params["available_only"] = "true" if available_only else "false"
        if extra_params:
            for k, v in extra_params.items():
                if isinstance(v, bool):
                    params[k] = "true" if v else "false"
                else:
                    params[k] = v

        payload = await self._get(_SEARCH_PATH, params=params)
        # /api/search wraps the payload in {"data": {"results": ..., "total": ...},
        # "search-uuid": ...}. Tolerate unwrapped envelopes too (some mocks /
        # other routes expose the inner shape directly).
        inner = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        assert isinstance(inner, dict)
        results_raw = inner.get("results") or []
        total = inner.get("total")
        results = tuple(item for item in results_raw if isinstance(item, dict))
        return AllsopSearchPage(
            page=page,
            size=size,
            total=int(total) if isinstance(total, (int, float)) else len(results),
            results=results,
        )

    async def iter_search_pages(
        self,
        *,
        auction_id: str | None = None,
        lot_type: LotType | None = None,
        available_only: bool | None = None,
        size: int = 100,
        max_pages: int | None = None,
        extra_params: Mapping[str, str | int | bool] | None = None,
    ) -> AsyncIterator[AllsopSearchPage]:
        """Stream every search page until exhausted (or ``max_pages``).

        Pagination is 1-indexed. We stop when the returned page has
        fewer than ``size`` rows OR when ``page * size >= total`` — the
        Allsop endpoint reports ``total`` so we don't need to probe
        for an empty page.
        """

        page = 1
        while True:
            result = await self.search_page(
                auction_id=auction_id,
                lot_type=lot_type,
                available_only=available_only,
                page=page,
                size=size,
                extra_params=extra_params,
            )
            yield result
            if not result.results or not result.has_next:
                return
            if max_pages is not None and page >= max_pages:
                return
            page += 1

    async def list_lots_for_auction(
        self,
        auction_id: str,
        *,
        size: int = 500,
        max_pages: int | None = None,
    ) -> list[Mapping[str, Any]]:
        """Collect every raw lot dict for one auction.

        Default ``size=500`` is tuned for live-auction fan-outs — a
        single Allsop catalogue is rarely bigger than that, so one
        call usually suffices. Returns a flat list; caller decides
        whether to parse with
        :func:`uk_property_scrapers.auctions.allsop.parse_search_results`.
        """

        out: list[Mapping[str, Any]] = []
        async for page in self.iter_search_pages(
            auction_id=auction_id, size=size, max_pages=max_pages
        ):
            out.extend(page.results)
        return out


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _normalise_lot_reference(reference: str | None) -> str:
    """Convert a raw catalogue reference into the URL-safe slug form.

    ``reference`` is either the pre-normalised slug (``"r260430-098"``)
    or the raw feed value (``"R260430 098"``). The lot-detail endpoint
    accepts the slug form only — we lowercase and fold whitespace
    into a single hyphen so both inputs collapse to the same shape.
    """

    if not isinstance(reference, str):
        return ""
    stripped = reference.strip().lower()
    if not stripped:
        return ""
    parts = [chunk for chunk in stripped.split() if chunk]
    return "-".join(parts)


async def list_upcoming_auctions() -> UpcomingAuctions:
    """One-shot fetch of the upcoming-auctions feed.

    Opens and disposes of an :class:`AllsopClient` for one call. Prefer
    an explicit ``async with`` when you need more than one call — this
    helper is for ad-hoc scripts and notebooks.
    """

    async with AllsopClient() as client:
        return await client.list_upcoming_auctions()


async def search_lots(
    *,
    auction_id: str | None = None,
    lot_type: LotType | None = None,
    available_only: bool | None = None,
    size: int = 100,
    max_pages: int | None = None,
) -> list[Mapping[str, Any]]:
    """One-shot fetch of all lots matching the given filters.

    Paginates via :meth:`AllsopClient.iter_search_pages` until the
    source reports exhaustion or ``max_pages`` is hit.
    """

    async with AllsopClient() as client:
        out: list[Mapping[str, Any]] = []
        async for page in client.iter_search_pages(
            auction_id=auction_id,
            lot_type=lot_type,
            available_only=available_only,
            size=size,
            max_pages=max_pages,
        ):
            out.extend(page.results)
        return out


class AllsopRegister:
    """``AuctionSourceRegister`` wrapper around :class:`AllsopClient`.

    The wrapper exists so the actor can treat Allsop, Auction House UK,
    Savills, and iamsold interchangeably via one protocol. All the
    actual transport + pagination lives on :class:`AllsopClient`;
    this register only normalises its shape into
    :class:`AuctionFetchResult` / :class:`AuctionSummary`.

    ``lot_type`` pins the discovery side to ``residential`` or
    ``commercial``; ``None`` (default) returns both.
    """

    source: ClassVar[AuctionHouse] = AuctionHouse.ALLSOP

    def __init__(
        self,
        client: AllsopClient | None = None,
        *,
        lot_type: LotType | None = None,
    ) -> None:
        self._client = client or AllsopClient()
        self._owns_client = client is None
        self._lot_type = lot_type

    async def __aenter__(self) -> AllsopRegister:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_client:
            await self._client.__aexit__(*exc)

    async def list_upcoming_auctions(self) -> list[AuctionSummary]:
        upcoming = await self._client.list_upcoming_auctions()
        return list(upcoming.all(lot_type=self._lot_type))

    async def fetch_auction(
        self,
        auction_id: str,
        *,
        summary: AuctionSummary | None = None,
        available_only: bool | None = None,
        max_pages: int | None = None,
        page_size: int = 500,
        include_gallery: bool = False,
        gallery_concurrency: int = 8,
    ) -> AuctionFetchResult:
        from uk_property_scrapers.auctions import allsop as allsop_parser

        auction_envelope = await self._client.get_auction(auction_id)
        raw_lots: list[Mapping[str, Any]] = []
        async for page in self._client.iter_search_pages(
            auction_id=auction_id,
            available_only=available_only,
            size=page_size,
            max_pages=max_pages,
        ):
            raw_lots.extend(page.results)

        auction_meta = allsop_parser.parse_auction_metadata(auction_envelope)
        lots = allsop_parser.parse_search_results(
            {"data": {"results": list(raw_lots)}},
            auction_meta=auction_meta or None,
        )
        if include_gallery and lots:
            lots = await self._hydrate_lot_galleries(
                lots, concurrency=gallery_concurrency
            )
        return AuctionFetchResult(
            source=self.source,
            auction_id=auction_id,
            auction_meta=dict(auction_meta),
            lots=lots,
            raw_envelope=auction_envelope,
        )

    async def _hydrate_lot_galleries(
        self,
        lots: Sequence[Any],
        *,
        concurrency: int,
    ) -> list[Any]:
        """Replace each lot's single thumbnail with its full gallery.

        One ``/api/lot/reference/<ref>`` call per lot — cheap JSON but
        N+1 on a 300-lot catalogue, so we bound the concurrency here
        rather than inline in ``fetch_auction``. Failures are silent:
        when the detail call errors, we fall back to whatever thumbnail
        :func:`parse_search_results` already populated.
        """

        from uk_property_scrapers.auctions import allsop as allsop_parser

        sem = asyncio.Semaphore(max(1, concurrency))

        async def _fetch_one(lot: Any) -> Any:
            reference = (lot.raw_site_fields or {}).get("reference")
            if not isinstance(reference, str) or not reference:
                return lot
            async with sem:
                try:
                    detail = await self._client.get_lot_detail(reference)
                except Exception:
                    return lot
            gallery = allsop_parser.parse_lot_gallery(detail)
            if not gallery:
                return lot
            return lot.model_copy(update={"image_urls": gallery})

        return list(await asyncio.gather(*(_fetch_one(lot) for lot in lots)))


__all__ = [
    "AllsopClient",
    "AllsopRegister",
    "AllsopSearchPage",
    "AuctionFetchResult",
    "AuctionSummary",
    "LotType",
    "UpcomingAuctions",
    "list_upcoming_auctions",
    "search_lots",
]
