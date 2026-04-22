"""UK property auction API clients.

Four sources are supported, each via a dedicated client + a thin
:class:`AuctionSourceRegister` wrapper so the ``uk-auctions`` actor
can dispatch across all of them via one protocol:

* :class:`AllsopClient` / :class:`AllsopRegister` — Allsop LLP JSON
  API (``www.allsop.co.uk``).
* :class:`AuctionHouseClient` / :class:`AuctionHouseRegister` — the
  federated Auction House UK network (``www.auctionhouse.co.uk``).
* :class:`SavillsAuctionsClient` / :class:`SavillsRegister` — Savills
  Auctions (``auctions.savills.co.uk``).
* :class:`IamsoldClient` / :class:`IamsoldRegister` — iamsold Modern
  Method of Auction (``www.iamsold.co.uk``).

Every register returns :class:`AuctionFetchResult` from
:meth:`fetch_auction` with normalised :class:`AuctionLot` rows from
:mod:`uk_property_scrapers.auctions`. Discovery returns
:class:`AuctionSummary` objects; the ``extra_context`` mapping on
each summary carries the source-specific metadata
(``branch`` slug for Auction House UK, URL ``slug`` for Savills) the
matching ``fetch_auction`` call needs.

Typical call flow::

    async with AllsopRegister() as register:
        upcoming = await register.list_upcoming_auctions()
        next_auction = upcoming[0]
        result = await register.fetch_auction(
            next_auction.auction_id, summary=next_auction
        )
        # result.lots: list[AuctionLot]

The ``source`` class-var on each register lets callers look up the
right register for an :class:`AuctionHouse` enum value without an
``isinstance`` ladder::

    REGISTERS: dict[AuctionHouse, Callable[[], AuctionSourceRegister]] = {
        AuctionHouse.ALLSOP: AllsopRegister,
        AuctionHouse.AUCTION_HOUSE_UK: AuctionHouseRegister,
        AuctionHouse.SAVILLS_AUCTIONS: SavillsRegister,
        AuctionHouse.IAMSOLD: IamsoldRegister,
    }
"""

from __future__ import annotations

from uk_property_apis.auctions._core import (
    AuctionFetchResult,
    AuctionSourceRegister,
    AuctionSummary,
)
from uk_property_apis.auctions.allsop_client import (
    AllsopClient,
    AllsopRegister,
    AllsopSearchPage,
    UpcomingAuctions,
    list_upcoming_auctions,
    search_lots,
)
from uk_property_apis.auctions.auction_house_client import (
    AuctionHouseClient,
    AuctionHouseRegister,
)
from uk_property_apis.auctions.iamsold_client import (
    IamsoldClient,
    IamsoldRegister,
)
from uk_property_apis.auctions.savills_client import (
    SavillsAuctionsClient,
    SavillsRegister,
)

__all__ = [
    "AllsopClient",
    "AllsopRegister",
    "AllsopSearchPage",
    "AuctionFetchResult",
    "AuctionHouseClient",
    "AuctionHouseRegister",
    "AuctionSourceRegister",
    "AuctionSummary",
    "IamsoldClient",
    "IamsoldRegister",
    "SavillsAuctionsClient",
    "SavillsRegister",
    "UpcomingAuctions",
    "list_upcoming_auctions",
    "search_lots",
]
