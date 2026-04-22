"""Canonical cross-source schema for UK property listings.

Every parser (Zoopla, Rightmove, OnTheMarket, auctions) returns instances of these
models, so downstream consumers (the agent, MCPs, Apify actors, SaaS API) work
with one unified type regardless of where the data came from.

Design principles:
    * Currency amounts are integers in *pence* (GBP * 100) to avoid floating-point
      drift. Floor areas are integers in square feet.
    * All free-text fields keep the raw site-specific wording alongside a
      normalized enum when parseable (e.g. ``property_type_raw`` + ``property_type``).
    * Optional fields are ``None`` when the site doesn't expose them, never empty strings.
    * Lat/lng are WGS84 decimal degrees.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Source(StrEnum):
    """Which site the listing was scraped from."""

    ZOOPLA = "zoopla"
    RIGHTMOVE = "rightmove"
    ONTHEMARKET = "onthemarket"


class TransactionType(StrEnum):
    """Buy vs rent vs auction vs shared ownership."""

    SALE = "sale"
    RENT = "rent"
    AUCTION = "auction"
    SHARED_OWNERSHIP = "shared_ownership"
    RETIREMENT = "retirement"
    UNKNOWN = "unknown"


class PropertyType(StrEnum):
    """Normalized property type enum used across all sources."""

    DETACHED = "detached"
    SEMI_DETACHED = "semi_detached"
    TERRACED = "terraced"
    END_OF_TERRACE = "end_of_terrace"
    FLAT = "flat"
    APARTMENT = "apartment"
    MAISONETTE = "maisonette"
    BUNGALOW = "bungalow"
    COTTAGE = "cottage"
    LAND = "land"
    COMMERCIAL = "commercial"
    PARK_HOME = "park_home"
    HOUSEBOAT = "houseboat"
    STUDIO = "studio"
    OTHER = "other"
    UNKNOWN = "unknown"


class Tenure(StrEnum):
    """Ownership tenure — England/Wales freehold/leasehold, Scotland commonhold etc."""

    FREEHOLD = "freehold"
    LEASEHOLD = "leasehold"
    SHARE_OF_FREEHOLD = "share_of_freehold"
    COMMONHOLD = "commonhold"
    FEUHOLD = "feuhold"
    UNKNOWN = "unknown"


class ListingType(StrEnum):
    """What kind of listing result this is — summary card vs full detail page."""

    SEARCH_CARD = "search_card"
    DETAIL = "detail"


class ListingFeature(StrEnum):
    """Common feature flags surfaced on listing cards."""

    NEW_HOME = "new_home"
    REDUCED = "reduced"
    CHAIN_FREE = "chain_free"
    RETIREMENT = "retirement"
    SHARED_OWNERSHIP = "shared_ownership"
    AUCTION = "auction"
    INVESTMENT = "investment"
    PART_BUY_PART_RENT = "part_buy_part_rent"
    NEW_LISTING = "new_listing"
    FEATURED = "featured"
    PROPERTY_OF_THE_WEEK = "property_of_the_week"
    PREMIUM = "premium"
    SOLD_STC = "sold_stc"
    UNDER_OFFER = "under_offer"
    LET_AGREED = "let_agreed"
    TENANTED_INVESTMENT = "tenanted_investment"
    OPEN_DAY = "open_day"
    VIDEO_TOUR = "video_tour"
    VIRTUAL_TOUR = "virtual_tour"


class LatLng(BaseModel):
    """WGS84 decimal degrees coordinate."""

    model_config = ConfigDict(frozen=True)

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


class Address(BaseModel):
    """Address as surfaced by the source site.

    Listing portals are deliberately vague with addresses pre-sale; we capture the
    raw display string and a postcode when visible. Geocoding to full OS-grade
    address happens in the enricher layer, not here.
    """

    raw: str = Field(..., description="Address string as displayed on the listing.")
    postcode_outcode: str | None = Field(
        None, description="Postcode outcode only if full postcode not shown, e.g. 'CB1'."
    )
    postcode: str | None = Field(
        None, description="Full postcode if available, e.g. 'CB1 2QA'."
    )


class PriceQualifier(StrEnum):
    """How the listed price should be interpreted."""

    GUIDE_PRICE = "guide_price"
    OFFERS_OVER = "offers_over"
    OFFERS_IN_EXCESS_OF = "offers_in_excess_of"
    OFFERS_IN_REGION = "offers_in_region"
    FIXED_PRICE = "fixed_price"
    FROM = "from"
    POA = "poa"
    SHARED_OWNERSHIP_FROM = "shared_ownership_from"
    ASKING_PRICE = "asking_price"
    UNKNOWN = "unknown"


class Price(BaseModel):
    """Price expressed in pence, with optional qualifier."""

    amount_pence: int | None = Field(
        None, description="Amount in pence (GBP * 100). None if POA or not parseable."
    )
    qualifier: PriceQualifier = PriceQualifier.UNKNOWN
    raw: str = Field(..., description="Original price string from the listing, e.g. 'OIEO £450,000'.")


class RentPeriod(StrEnum):
    """Per-period rent cadence."""

    PER_MONTH = "per_month"
    PER_WEEK = "per_week"
    PER_DAY = "per_day"
    PER_YEAR = "per_year"
    UNKNOWN = "unknown"


class RentPrice(Price):
    """Rental price has a cadence as well as an amount."""

    period: RentPeriod = RentPeriod.UNKNOWN


class Agent(BaseModel):
    """Estate/lettings agent associated with the listing.

    ``source_id`` holds the portal's internal branch identifier when one is
    exposed (Zoopla ``branchId``, Rightmove branch slug, OTM ``branchId``).
    It's what lets callers dedupe "Connells Cambourne" across portals and
    pivot later queries (``get_agent_profile``, ``list_agent_stock``) back
    onto the same branch.
    """

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    branch: str | None = None
    address: str | None = None
    url: HttpUrl | None = None
    logo_url: HttpUrl | None = None
    source_id: str | None = Field(
        None,
        description="Portal-internal branch id (e.g. Zoopla `1855`, Rightmove `211166`).",
    )
    group_name: str | None = Field(
        None,
        description="Franchise / corporate parent (e.g. Connells for 'Connells Cambourne').",
    )


class Image(BaseModel):
    """Listing photo."""

    url: HttpUrl
    caption: str | None = None


# ---------------------------------------------------------------------------
# Detail-page enrichments
# ---------------------------------------------------------------------------
#
# All UK portals now publish the same structured "key information" block
# (driven by the National Trading Standards "Material Information" rules).
# The exact shape differs between portals — Zoopla exposes it in its Next.js
# RSC stream as an ``ntsInfo`` array, Rightmove ships a ``livingCosts`` JSON
# blob inside ``window.PAGE_MODEL``, OnTheMarket renders icon-plus-text rows
# in the DOM — but the content is congruent. We normalize everything here.


class PropertyTimelineEventKind(StrEnum):
    """What kind of state change is described by a timeline entry.

    Portals publish listings' recent history as a small ordered timeline:
    when it was first listed, when the price was reduced, when a previous
    owner sold it, when it went SSTC, etc. Kind lets callers filter by event
    type without string-matching.
    """

    LISTED = "listed"
    REDUCED = "reduced"
    INCREASED = "increased"
    UNDER_OFFER = "under_offer"
    SOLD_STC = "sold_stc"
    SOLD = "sold"
    WITHDRAWN = "withdrawn"
    RELISTED = "relisted"
    LET_AGREED = "let_agreed"
    UNKNOWN = "unknown"


class PropertyTimelineEvent(BaseModel):
    """One entry in a listing's recent-history timeline.

    ``occurred_at`` is best-effort: portals usually report "February 2026",
    which we widen to the first of the month. When a full date is exposed
    (``13/04/2026`` on Rightmove, ISO on Zoopla's RSC) we use it verbatim.
    """

    kind: PropertyTimelineEventKind = PropertyTimelineEventKind.UNKNOWN
    occurred_at: date | None = None
    occurred_at_text: str = Field(
        ..., description="Raw date string as rendered on the site (e.g. 'February 2026')."
    )
    price_pence: int | None = Field(
        None, ge=0, description="Price at this event, in pence. None when site doesn't state."
    )
    change_pence: int | None = Field(
        None,
        description="Delta from the previous event, in pence. Negative for reductions.",
    )
    change_pct: float | None = Field(
        None,
        description="Percentage change as displayed (negative for reductions). None if absent.",
    )
    raw: str = Field(
        ..., description="Entire raw text of the event row, for audit / future re-parse."
    )


class LeaseTerms(BaseModel):
    """Normalized leasehold economics.

    Populated only when the property is leasehold / share-of-freehold. Ground
    rent and service charge are in pence per year to match the rest of the
    schema; review frequency is in whole years.
    """

    years_remaining: int | None = Field(
        None, ge=0, le=10_000, description="Years left on the lease."
    )
    length_years: int | None = Field(
        None, ge=0, le=10_000, description="Total length of the lease."
    )
    ground_rent_pence_per_year: int | None = Field(None, ge=0)
    ground_rent_review_period_years: int | None = Field(None, ge=0, le=1000)
    ground_rent_review_pct: float | None = Field(None, ge=0, le=100)
    service_charge_pence_per_year: int | None = Field(None, ge=0)
    expires_on: date | None = None
    raw: dict[str, str] = Field(
        default_factory=dict,
        description="Original snippets per sub-field — 'ground_rent' -> '£450 per annum'.",
    )


class BroadbandTier(StrEnum):
    """Ofcom-aligned broadband speed tiers."""

    BASIC = "basic"
    SUPERFAST = "superfast"
    ULTRAFAST = "ultrafast"
    GIGABIT = "gigabit"
    UNKNOWN = "unknown"


class BroadbandSpeed(BaseModel):
    """Broadband availability / speed estimate for the postcode.

    Only OnTheMarket currently exposes a numeric Mbps estimate. Zoopla and
    Rightmove default to a qualitative "FTTP / FTTC / Ask agent" disclosure.
    We keep both shapes so consumers can display whichever the site had.
    """

    tier: BroadbandTier = BroadbandTier.UNKNOWN
    max_download_mbps: int | None = Field(None, ge=0, le=10_000)
    technology: str | None = Field(
        None,
        description="Connection type as disclosed, e.g. 'FTTP', 'FTTC', 'ADSL'.",
    )
    raw: str = Field("", description="Original display text.")


class MobileCoverageLevel(StrEnum):
    """Qualitative signal level per carrier.

    OnTheMarket publishes green/amber/red icons per carrier (EE, O2, Three,
    Vodafone) for both voice and data. We collapse green+enhanced into
    ``ENHANCED`` (the highest band), green into ``LIKELY``, amber into
    ``LIMITED``, red into ``NONE``.
    """

    NONE = "none"
    LIMITED = "limited"
    LIKELY = "likely"
    ENHANCED = "enhanced"
    UNKNOWN = "unknown"


class MobileSignal(BaseModel):
    """Mobile coverage prediction for one carrier at this property."""

    carrier: str = Field(..., description="Network name as displayed, e.g. 'EE', 'O2'.")
    voice: MobileCoverageLevel = MobileCoverageLevel.UNKNOWN
    data: MobileCoverageLevel = MobileCoverageLevel.UNKNOWN


class EnergyRating(BaseModel):
    """Energy Performance Certificate (EPC) rating band."""

    current: str | None = Field(
        None,
        pattern=r"^[A-G]$",
        description="Current rating band, A (best) through G (worst).",
    )
    potential: str | None = Field(
        None,
        pattern=r"^[A-G]$",
        description="Potential rating band after recommended improvements.",
    )
    raw: str = Field("", description="Original display text (e.g. 'EPC Rating: C').")


class MaterialInformation(BaseModel):
    """National Trading Standards "Material Information" bundle.

    Required disclosure from estate agents under UK law since 2023. All three
    portals now publish a version of this (Zoopla `ntsInfo`, Rightmove
    `livingCosts` + Material Information Report link, OnTheMarket Key-info
    block + MIP doc). Populated fields depend on what the seller has lodged.
    """

    report_url: HttpUrl | None = Field(
        None,
        description="Link to the full Material Information Report document (PDF) when the agent has uploaded one.",
    )
    council_tax_band: str | None = Field(
        None,
        description="Single uppercase letter A-H (E&W) or A-I (Wales) / A-H (Scotland).",
    )
    tenure: Tenure = Tenure.UNKNOWN
    lease: LeaseTerms | None = None
    epc: EnergyRating | None = None
    broadband: BroadbandSpeed | None = None
    mobile_signal: list[MobileSignal] = Field(default_factory=list)
    parking_raw: str | None = None
    heating_raw: str | None = None
    electricity_raw: str | None = None
    water_raw: str | None = None
    sewerage_raw: str | None = None
    restrictions_raw: str | None = None
    rights_and_easements_raw: str | None = None
    flood_risk_raw: str | None = None
    extra: dict[str, str] = Field(
        default_factory=dict,
        description="Any additional NTS / Material-Info fields the portal exposes verbatim.",
    )


class Listing(BaseModel):
    """Canonical normalized listing — the shape every downstream consumer sees."""

    model_config = ConfigDict(extra="forbid", ser_json_bytes="utf8")

    # Provenance
    source: Source
    source_id: str = Field(..., description="Listing ID as assigned by the source site.")
    source_url: HttpUrl
    listing_type: ListingType
    scraped_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())

    # Transaction
    transaction_type: TransactionType = TransactionType.UNKNOWN

    # Price — exactly one of these is populated based on transaction_type
    sale_price: Price | None = None
    rent_price: RentPrice | None = None

    # Physical attributes
    property_type: PropertyType = PropertyType.UNKNOWN
    property_type_raw: str | None = Field(
        None, description="Original type string from the listing, e.g. 'Terraced house'."
    )
    bedrooms: int | None = Field(None, ge=0, le=100)
    bathrooms: int | None = Field(None, ge=0, le=100)
    reception_rooms: int | None = Field(None, ge=0, le=100)
    floor_area_sqft: int | None = Field(None, ge=0, le=100_000)
    tenure: Tenure = Tenure.UNKNOWN

    # Location
    address: Address
    coords: LatLng | None = None

    # Descriptive
    title: str | None = None
    summary: str | None = None
    description: str | None = Field(
        None, description="Full listing description. Only populated for DETAIL listings."
    )
    features: list[ListingFeature] = Field(default_factory=list)
    image_urls: list[Image] = Field(default_factory=list)
    image_count: int | None = Field(
        None, ge=0, description="Total images available on the source, even if we only captured the first."
    )

    # Commercial
    agent: Agent | None = None

    # Timestamps on the source site (when available)
    first_listed_at: datetime | None = None
    last_updated_at: datetime | None = None

    # Detail-page enrichments (populated on DETAIL listings; None/empty on cards)
    lease: LeaseTerms | None = None
    broadband: BroadbandSpeed | None = None
    mobile_signal: list[MobileSignal] = Field(default_factory=list)
    epc: EnergyRating | None = None
    council_tax_band: str | None = Field(
        None,
        description="Band letter (A-H/I) as disclosed by the agent, or None if absent.",
    )
    timeline: list[PropertyTimelineEvent] = Field(
        default_factory=list,
        description="Recent history of the listing — reductions, status changes, prior sales.",
    )
    material_information: MaterialInformation | None = Field(
        None,
        description="Structured Material Information bundle (NTS disclosure).",
    )

    # Raw passthrough — always keep what the site gave us, for debugging and future re-parses
    raw_site_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Site-specific raw values not yet normalized. Keys are snake_case.",
    )


# ---------------------------------------------------------------------------
# Auction lots
# ---------------------------------------------------------------------------
#
# Auctions are their own animal: the primary key is (auction house, catalogue
# date, lot number), not a single listing ID; prices are almost always a guide
# range rather than a point; the sale method (traditional / modern / online /
# conditional) materially changes the bidding mechanics and completion timeline.
# Rather than overload :class:`Listing` with fields that are meaningless for
# non-auction listings, auctions get their own top-level model.


class AuctionHouse(StrEnum):
    """Which auctioneer the lot was scraped from."""

    ALLSOP = "allsop"
    AUCTION_HOUSE_UK = "auction_house_uk"
    SAVILLS_AUCTIONS = "savills_auctions"
    IAMSOLD = "iamsold"


class AuctionSaleMethod(StrEnum):
    """How the lot is being sold.

    ``TRADITIONAL`` is the classic "hammer falls, 10% exchange on the day, 28
    days to complete" English auction. ``MODERN`` (aka Modern Method of
    Auction) gives buyers 56 days and usually a reservation agreement. Online
    timed auctions share the traditional completion window but have no live
    floor. ``CONDITIONAL`` is Savills' term for an auction with a contractual
    condition such as planning.
    """

    TRADITIONAL = "traditional"
    MODERN = "modern"
    ONLINE_TIMED = "online_timed"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"


class AuctionLotStatus(StrEnum):
    """Lifecycle state as surfaced on the catalogue page."""

    AVAILABLE = "available"
    UNDER_OFFER = "under_offer"
    SOLD_PRIOR = "sold_prior"
    SOLD = "sold"
    WITHDRAWN = "withdrawn"
    POSTPONED = "postponed"
    UNSOLD = "unsold"
    UNKNOWN = "unknown"


class AuctionGuidePrice(BaseModel):
    """Guide price as published in the catalogue.

    Guide prices are commonly a *range* (``"£250,000 - £275,000"``) or an
    "excess" (``"In excess of £500,000"``) rather than a single number.
    We capture both endpoints when present; ``low_pence`` alone is populated
    for single-value guides and ``high_pence`` stays ``None``.
    """

    low_pence: int | None = Field(
        None, ge=0, description="Lower end of the guide range, in pence."
    )
    high_pence: int | None = Field(
        None, ge=0, description="Upper end of the guide range, in pence."
    )
    qualifier: PriceQualifier = PriceQualifier.UNKNOWN
    raw: str = Field(..., description="Original guide-price string from the catalogue.")


class AuctionLot(BaseModel):
    """Canonical normalized auction lot — the shape every auction parser emits."""

    model_config = ConfigDict(extra="forbid", ser_json_bytes="utf8")

    # Provenance
    auction_house: AuctionHouse
    source_id: str = Field(
        ...,
        description="Lot ID as assigned by the auction house. Often a catalogue slug or numeric ID.",
    )
    source_url: HttpUrl
    scraped_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())

    # Catalogue position
    catalogue_id: str | None = Field(
        None,
        description="Catalogue / sale identifier (e.g. 'March 2026 Residential').",
    )
    lot_number: str | None = Field(
        None,
        description="Printed lot number. String because some houses use alphanumeric ('12A').",
    )

    # Timing
    auction_date: date | None = Field(
        None, description="Scheduled auction date (local date)."
    )
    auction_end_at: datetime | None = Field(
        None,
        description="For timed online auctions, the close-of-bidding timestamp (UTC).",
    )
    sale_method: AuctionSaleMethod = AuctionSaleMethod.UNKNOWN

    # Status
    status: AuctionLotStatus = AuctionLotStatus.UNKNOWN
    sold_price_pence: int | None = Field(
        None, ge=0, description="Final hammer price, in pence, if published post-sale."
    )

    # Pricing
    guide_price: AuctionGuidePrice | None = None
    reserve_price_pence: int | None = Field(
        None,
        ge=0,
        description="Reserve price if explicitly published (rare — most houses keep this private).",
    )

    # Physical attributes — optional because many auction lots are land / commercial / mixed use
    property_type: PropertyType = PropertyType.UNKNOWN
    property_type_raw: str | None = None
    tenure: Tenure = Tenure.UNKNOWN
    bedrooms: int | None = Field(None, ge=0, le=100)
    bathrooms: int | None = Field(None, ge=0, le=100)
    floor_area_sqft: int | None = Field(None, ge=0, le=1_000_000)

    # Income (investment lots)
    annual_rent_pence: int | None = Field(
        None,
        ge=0,
        description="Annual rent roll in pence if the lot is tenanted/investment.",
    )
    is_vacant_possession: bool | None = Field(
        None,
        description="True if sold with vacant possession, False if tenanted, None if not stated.",
    )

    # Location
    address: Address
    coords: LatLng | None = None

    # Descriptive
    title: str | None = None
    summary: str | None = None
    description: str | None = None
    image_urls: list[Image] = Field(default_factory=list)
    catalogue_pdf_url: HttpUrl | None = Field(
        None, description="Link to the full catalogue PDF if the site exposes one."
    )
    legal_pack_url: HttpUrl | None = Field(
        None,
        description="Link to the legal pack / special conditions (often gated behind login).",
    )

    # Raw passthrough — identical intent to :attr:`Listing.raw_site_fields`.
    raw_site_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Site-specific raw values not yet normalized. Keys are snake_case.",
    )


# ---------------------------------------------------------------------------
# Agent profiles
# ---------------------------------------------------------------------------
#
# Each listing exposes a minimal :class:`Agent` via ``Listing.agent``. The
# agent *branch page* on each portal carries much richer information — team
# roster, trade-body memberships, opening hours, per-branch live stock, per-
# transaction-type stats. :class:`AgentProfile` is what
# ``get_agent_profile`` returns; :class:`AgentStockSummary` is the rolling
# inventory snapshot underneath ``list_agent_stock`` +
# ``agent_performance_stats``.


class BranchTeamMember(BaseModel):
    """One named individual listed on an agent-branch page."""

    name: str
    role: str | None = None
    phone: str | None = None
    email: str | None = None
    photo_url: HttpUrl | None = None


class AgentStockSummary(BaseModel):
    """Rolling snapshot of a branch's live and recently-sold inventory."""

    captured_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    total_live: int | None = Field(
        None, ge=0, description="Currently-marketed listings across all transaction types."
    )
    for_sale: int | None = Field(None, ge=0)
    to_rent: int | None = Field(None, ge=0)
    sold_stc: int | None = Field(None, ge=0)
    under_offer: int | None = Field(None, ge=0)
    sold_in_last_12m: int | None = Field(None, ge=0)
    let_agreed_in_last_12m: int | None = Field(None, ge=0)
    median_price_pence: int | None = Field(
        None, ge=0, description="Median asking price across live for-sale stock, in pence."
    )
    median_rent_pence_per_month: int | None = Field(
        None, ge=0, description="Median asking rent (PCM) across live to-rent stock, in pence."
    )


class AgentProfile(BaseModel):
    """Full public-facing agent-branch profile, as rendered on portal branch pages."""

    model_config = ConfigDict(extra="forbid", ser_json_bytes="utf8")

    source: Source
    source_id: str = Field(..., description="Portal-internal branch id.")
    source_url: HttpUrl
    scraped_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())

    name: str
    group_name: str | None = Field(
        None, description="Franchise / corporate parent (e.g. Connells, Savills)."
    )
    branch: str | None = None
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: HttpUrl | None = None
    logo_url: HttpUrl | None = None

    bio: str | None = None
    opening_hours: dict[str, str] = Field(
        default_factory=dict,
        description="Weekday-keyed opening hours as the branch publishes them.",
    )
    trade_bodies: list[str] = Field(
        default_factory=list,
        description="Registrations displayed on the branch page — TPO, ARLA, NAEA, TDS…",
    )
    socials: dict[str, HttpUrl] = Field(
        default_factory=dict,
        description="Keys: 'twitter', 'facebook', 'instagram', 'linkedin', 'youtube'.",
    )
    team: list[BranchTeamMember] = Field(default_factory=list)
    stock: AgentStockSummary | None = None
    raw_site_fields: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Listing actions — inquiry, viewing request, valuation
# ---------------------------------------------------------------------------
#
# Used by the portal-specific ``send_inquiry`` / ``request_viewing`` tools.
# ``dry_run=True`` is the default: every tool must explicitly gate the real
# network submission behind an opt-in flag. Downstream legal/compliance rules
# (GDPR, portal T&Cs) require that buyer identity is genuine and consent is
# explicit, hence the required ``consent_to_portal_tcs`` acknowledgement.


class BuyerInterest(StrEnum):
    """Buyer stage disclosure as offered by the portals' inquiry forms."""

    BROWSING = "browsing"
    MOVING_WITHIN_3_MONTHS = "moving_within_3_months"
    MOVING_WITHIN_6_MONTHS = "moving_within_6_months"
    MOVING_WITHIN_12_MONTHS = "moving_within_12_months"
    INVESTOR = "investor"
    RELOCATION = "relocation"
    UNKNOWN = "unknown"


class BuyerPosition(StrEnum):
    """Whether the buyer's purchase is contingent on selling an existing property."""

    FIRST_TIME_BUYER = "first_time_buyer"
    NOTHING_TO_SELL = "nothing_to_sell"
    PROPERTY_ON_MARKET = "property_on_market"
    PROPERTY_UNDER_OFFER = "property_under_offer"
    PROPERTY_SOLD_STC = "property_sold_stc"
    UNKNOWN = "unknown"


class BuyerMortgageStatus(StrEnum):
    MORTGAGE_ARRANGED = "mortgage_arranged"
    MORTGAGE_NEEDED = "mortgage_needed"
    CASH_BUYER = "cash_buyer"
    UNKNOWN = "unknown"


class BuyerIdentity(BaseModel):
    """Minimum-viable buyer identity required for a portal inquiry.

    Portals all require name + email + phone at minimum; anything less is
    silently rejected. We don't persist this object anywhere by default —
    it's passed through into the form submitter.
    """

    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(..., min_length=5, max_length=200, pattern=r"^[^@]+@[^@]+\.[^@]+$")
    phone: str = Field(..., min_length=6, max_length=30)


class InquiryRequest(BaseModel):
    """Structured request to email / message an agent about a listing."""

    model_config = ConfigDict(extra="forbid")

    listing_url: HttpUrl
    identity: BuyerIdentity
    message: str = Field(..., min_length=1, max_length=2000)
    interest: BuyerInterest = BuyerInterest.UNKNOWN
    position: BuyerPosition = BuyerPosition.UNKNOWN
    mortgage_status: BuyerMortgageStatus = BuyerMortgageStatus.UNKNOWN
    consent_to_portal_tcs: bool = Field(
        False,
        description="Must be True to actually submit. Portal T&Cs + GDPR opt-in.",
    )
    dry_run: bool = Field(
        True,
        description="When True (default), validate + build the payload but do NOT submit.",
    )


class ViewingRequest(BaseModel):
    """Structured viewing-request submission."""

    model_config = ConfigDict(extra="forbid")

    listing_url: HttpUrl
    identity: BuyerIdentity
    preferred_slots: list[datetime] = Field(
        default_factory=list,
        description="Up to three preferred viewing slots (portals typically allow 2-3).",
        max_length=5,
    )
    message: str | None = Field(None, max_length=2000)
    consent_to_portal_tcs: bool = False
    dry_run: bool = True


class FreeValuationRequest(BaseModel):
    """Structured 'book a free valuation' submission (sell-side lead)."""

    model_config = ConfigDict(extra="forbid")

    address: Address
    identity: BuyerIdentity
    transaction: Literal["sale", "rent"] = "sale"
    property_type: PropertyType = PropertyType.UNKNOWN
    bedrooms: int | None = Field(None, ge=0, le=50)
    target_portal: Source
    target_agent_source_id: str | None = Field(
        None,
        description="Optional — pin the request to a specific agent branch id.",
    )
    consent_to_portal_tcs: bool = False
    dry_run: bool = True


class InquiryChannel(StrEnum):
    EMAIL = "email"
    CALLBACK = "callback"
    VIEWING_REQUEST = "viewing_request"
    VALUATION = "valuation"


class InquiryOutcome(StrEnum):
    """Final status of the submission attempt.

    ``DRY_RUN`` means the request was validated and serialized but not sent.
    ``CAPTCHA_UNSOLVED`` distinguishes "we reached the form and the captcha
    solver declined" from generic submission failures so callers can retry
    with a different solver.
    """

    SUBMITTED = "submitted"
    DRY_RUN = "dry_run"
    CAPTCHA_UNSOLVED = "captcha_unsolved"
    REJECTED_BY_PORTAL = "rejected_by_portal"
    AGENT_OPTED_OUT = "agent_opted_out"
    NETWORK_ERROR = "network_error"
    VALIDATION_ERROR = "validation_error"


class InquiryResult(BaseModel):
    """Result of a ``send_inquiry`` / ``request_viewing`` / ``request_free_valuation`` call."""

    outcome: InquiryOutcome
    channel: InquiryChannel
    listing_url: HttpUrl | None = None
    submitted_at: datetime | None = None
    portal_reference_id: str | None = Field(
        None,
        description="Any id/reference the portal returned after successful submission.",
    )
    captcha_required: bool = False
    captcha_solved: bool | None = None
    portal_message: str | None = Field(
        None,
        max_length=2000,
        description="Human-readable confirmation / error as shown by the portal.",
    )
    error: str | None = Field(
        None, max_length=2000, description="Developer-facing error message."
    )


# ---------------------------------------------------------------------------
# Delta / watch
# ---------------------------------------------------------------------------
#
# The delta layer is storage-backed: a :class:`ListingSnapshot` captures the
# fields we care about at a point in time, a :class:`SnapshotDiff` is a
# structured "what changed" between two snapshots for the same listing,
# and a :class:`ListingChangeEvent` is the union the firehose MCP tools emit
# (``reductions_firehose``, ``new_listings_firehose``, ``back_on_market``).


class ListingSnapshot(BaseModel):
    """Canonical subset of :class:`Listing` persisted by the snapshot store.

    We intentionally do NOT persist the full listing — we keep the fields
    that most watchers care about (price, status, photos, description,
    agent), plus a fingerprint that lets us hash-compare efficiently. Other
    fields are recoverable on demand by re-fetching the listing.
    """

    source: Source
    source_id: str
    source_url: HttpUrl
    captured_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    fingerprint: str = Field(
        ...,
        description="Stable hash of the snapshot payload; identical fingerprints mean no change.",
    )
    price_pence: int | None = None
    price_qualifier: PriceQualifier = PriceQualifier.UNKNOWN
    features: list[ListingFeature] = Field(default_factory=list)
    image_count: int | None = None
    image_fingerprints: list[str] = Field(
        default_factory=list,
        description="Per-image hashes (URL hash) so we can spot added/removed photos.",
    )
    description_fingerprint: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    reception_rooms: int | None = None
    floor_area_sqft: int | None = None
    property_type: PropertyType = PropertyType.UNKNOWN
    tenure: Tenure = Tenure.UNKNOWN
    agent_source_id: str | None = None
    status_text: str | None = Field(
        None,
        description="Raw status line (e.g. 'Reduced on 13/04/2026', 'SSTC').",
    )


class ListingChangeKind(StrEnum):
    """What kind of delta a snapshot pair represents.

    ``NEW`` / ``DELETED`` are for listings we hadn't seen before / are no
    longer present. The rest compare a `before` to an `after` snapshot.
    """

    NEW = "new"
    DELETED = "deleted"
    PRICE_REDUCED = "price_reduced"
    PRICE_INCREASED = "price_increased"
    BACK_ON_MARKET = "back_on_market"
    SOLD_STC = "sold_stc"
    UNDER_OFFER = "under_offer"
    REMOVED_SOLD_STC = "removed_sold_stc"
    DESCRIPTION_CHANGED = "description_changed"
    PHOTOS_ADDED = "photos_added"
    PHOTOS_REMOVED = "photos_removed"
    AGENT_CHANGED = "agent_changed"
    FEATURE_CHANGED = "feature_changed"
    UNCHANGED = "unchanged"


class SnapshotDiff(BaseModel):
    """Per-field breakdown of what changed between two snapshots."""

    price_change_pence: int | None = None
    price_change_pct: float | None = None
    added_features: list[ListingFeature] = Field(default_factory=list)
    removed_features: list[ListingFeature] = Field(default_factory=list)
    image_count_delta: int | None = None
    description_changed: bool = False
    agent_changed: bool = False
    status_changed: bool = False
    field_changes: dict[str, tuple[str | None, str | None]] = Field(
        default_factory=dict,
        description="Arbitrary 'field -> (before, after)' pairs for any other scalar change.",
    )


class ListingChangeEvent(BaseModel):
    """Union event emitted by watchers when a listing's snapshot changes materially."""

    kind: ListingChangeKind
    source: Source
    source_id: str
    source_url: HttpUrl
    detected_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    before: ListingSnapshot | None = None
    after: ListingSnapshot | None = None
    diff: SnapshotDiff | None = None
    notes: str | None = None
