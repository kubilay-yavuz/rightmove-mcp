"""uk-property-scrapers - pure-Python parsers for UK property listing sites.

The base package is I/O-free: pass HTML in, get :class:`Listing` or
:class:`AuctionLot` models out.

For fetching HTML there are two companion packages:

* :mod:`uk_property_listings` (public, in the same monorepo) - the free tier,
  ``httpx``-only ``SimpleCrawler`` plus ``SearchQuery``, URL builders, and
  pagination helpers for Zoopla/Rightmove/OnTheMarket.
* ``uk-property-apify-shared`` (private, in the ``uk-property-apify`` repo) -
  the production-grade ``Crawler`` with TLS-impersonating + Playwright-stealth
  fetchers, anti-bot classification, proxy rotation, rate limiting, and tier
  escalation. Used by the hosted Apify actors.
"""

from uk_property_scrapers import auctions, onthemarket, rightmove, zoopla
from uk_property_scrapers.schema import (
    Address,
    Agent,
    AgentProfile,
    AgentStockSummary,
    AuctionGuidePrice,
    AuctionHouse,
    AuctionLot,
    AuctionLotStatus,
    AuctionSaleMethod,
    BranchTeamMember,
    BuyerIdentity,
    BuyerInterest,
    BuyerMortgageStatus,
    BuyerPosition,
    FreeValuationRequest,
    Image,
    InquiryChannel,
    InquiryOutcome,
    InquiryRequest,
    InquiryResult,
    Listing,
    ListingChangeEvent,
    ListingChangeKind,
    ListingFeature,
    ListingSnapshot,
    ListingType,
    Price,
    PriceQualifier,
    PropertyType,
    RentPeriod,
    RentPrice,
    SnapshotDiff,
    Source,
    Tenure,
    TransactionType,
    ViewingRequest,
)

__version__ = "0.2.0"

__all__ = [
    "Address",
    "Agent",
    "AgentProfile",
    "AgentStockSummary",
    "AuctionGuidePrice",
    "AuctionHouse",
    "AuctionLot",
    "AuctionLotStatus",
    "AuctionSaleMethod",
    "BranchTeamMember",
    "BuyerIdentity",
    "BuyerInterest",
    "BuyerMortgageStatus",
    "BuyerPosition",
    "FreeValuationRequest",
    "Image",
    "InquiryChannel",
    "InquiryOutcome",
    "InquiryRequest",
    "InquiryResult",
    "Listing",
    "ListingChangeEvent",
    "ListingChangeKind",
    "ListingFeature",
    "ListingSnapshot",
    "ListingType",
    "Price",
    "PriceQualifier",
    "PropertyType",
    "RentPeriod",
    "RentPrice",
    "SnapshotDiff",
    "Source",
    "Tenure",
    "TransactionType",
    "ViewingRequest",
    "__version__",
    "auctions",
    "onthemarket",
    "rightmove",
    "zoopla",
]
