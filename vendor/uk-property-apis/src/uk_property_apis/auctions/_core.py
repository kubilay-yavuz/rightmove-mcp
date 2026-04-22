"""Shared protocol, discovery summary, and result shapes for
multi-source auction clients.

Every auction source register we support exposes the same two-call
surface:

1. :meth:`AuctionSourceRegister.list_upcoming_auctions` — discovery.
2. :meth:`AuctionSourceRegister.fetch_auction` — per-auction catalogue
   + metadata + normalised lots.

Keeping a single protocol here means the ``uk-auctions`` Apify actor
can fan out across Allsop, Auction House UK, Savills, and iamsold
with one code path, and adding a new source stays a drop-in job.

``AuctionSummary`` is deliberately source-agnostic: every field except
``source`` + ``auction_id`` is optional, and the ``raw`` /
``extra_context`` escape hatches carry site-specific material
(branch slug for Auction House UK, URL slug for Savills, catalogue
metadata for Allsop).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from uk_property_scrapers.schema import AuctionHouse

if TYPE_CHECKING:  # pragma: no cover — type imports only.
    from uk_property_scrapers.schema import AuctionLot


@dataclass(frozen=True, slots=True)
class AuctionSummary:
    """Source-agnostic discovery summary for one upcoming auction.

    Every auction-house register emits these from
    :meth:`AuctionSourceRegister.list_upcoming_auctions`. Only
    ``auction_id`` and ``source`` are required; the rest are populated
    when the source exposes them. Source-specific context (branch
    slug, URL slug, catalogue pointer) lives in ``extra_context``.
    """

    auction_id: str
    source: AuctionHouse = AuctionHouse.ALLSOP
    name: str | None = None
    reference: str | None = None
    auction_date_iso: str | None = None
    second_day_iso: str | None = None
    venue: str | None = None
    lots_sold: int | None = None
    lots_unsold: int | None = None
    value_sold_pence: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)
    extra_context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuctionFetchResult:
    """Canonical return shape for ``fetch_auction`` across all sources."""

    source: AuctionHouse
    auction_id: str
    auction_meta: dict[str, Any]
    lots: Sequence[AuctionLot]
    raw_envelope: Any = field(default=None)


@runtime_checkable
class AuctionSourceRegister(Protocol):
    """Uniform async interface every auction source implements.

    Register classes also act as async context managers so callers can
    keep the underlying ``httpx.AsyncClient`` open across multiple
    discovery + catalogue calls, which is how the actor fan-out
    operates in practice.
    """

    source: ClassVar[AuctionHouse]

    async def __aenter__(self) -> AuctionSourceRegister:  # pragma: no cover
        ...

    async def __aexit__(self, *exc: Any) -> None:  # pragma: no cover
        ...

    async def list_upcoming_auctions(self) -> list[AuctionSummary]:
        """Return all upcoming auction summaries from this register."""
        ...

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
        """Fetch one auction's metadata + normalised lot list.

        ``summary`` is optional context the discovery phase produced
        (e.g. the Auction House UK branch slug, Savills URL slug) that
        some clients need to resolve the catalogue URL. When ``None``
        the client falls back to a best-effort lookup against its own
        discovery feed.

        ``include_gallery`` is a per-register opt-in: when supported
        (currently just Allsop), the register fans out a lot-detail
        call per lot and replaces each lot's single-thumbnail
        ``image_urls`` with the full photo gallery. Registers that
        don't expose a cheap gallery endpoint ignore the flag.
        """
        ...


__all__ = [
    "AuctionFetchResult",
    "AuctionSourceRegister",
    "AuctionSummary",
]
