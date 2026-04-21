"""Core Rightmove tool implementations - transport-agnostic.

Each tool takes a Pydantic input model and returns a Pydantic output model.
The :mod:`.server` layer turns these into MCP tool handlers over stdio or
HTTP; the same functions are reusable directly from Python.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from uk_property_apify_shared.actions import (
    RIGHTMOVE_BUNDLE,
    RequestFreeValuationInput,
    RequestFreeValuationOutput,
    RequestViewingInput,
    RequestViewingOutput,
    SendInquiryInput,
    SendInquiryOutput,
)
from uk_property_apify_shared.actions import mcp as _action_mcp
from uk_property_apify_shared.delta import (
    FirehoseInput,
    FirehoseOutput,
    WatchListingOutput,
    WatchQueryOutput,
    ingest_listings,
    load_firehose,
    open_store,
)
from uk_property_scrapers import rightmove as rm
from uk_property_scrapers.schema import (  # noqa: TC002 (Pydantic field type)
    AgentProfile,
    Listing,
    ListingChangeKind,
    Source,
)

_AGENT_URL_RE = re.compile(
    r"^https?://(?:www\.)?rightmove\.co\.uk/estate-agents/agent/"
    r"[^/]+/[^/?#]+-\d+\.html(?:[?#].*)?$",
    re.IGNORECASE,
)


class SearchListingsInput(BaseModel):
    """Input to :func:`search_listings`."""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(..., min_length=1, description="Town, postcode, or region.")
    transaction: Literal["sale", "rent"] = "sale"
    min_price: int | None = Field(None, ge=0, description="Min price in GBP.")
    max_price: int | None = Field(None, ge=0, description="Max price in GBP.")
    min_beds: int | None = Field(None, ge=0, le=20)
    max_beds: int | None = Field(None, ge=0, le=20)
    max_pages: int = Field(1, ge=1, le=10, description="Number of search pages to fetch.")
    hydrate_details: bool = Field(
        False,
        description=(
            "Also fetch each listing's detail page. Required to populate "
            "description, features, coords, first_listed_at, lease, "
            "broadband, EPC, council_tax_band, timeline (including the "
            "full HMLR sale history), material_information, and the "
            "enriched Agent with branch source_id + franchise group_name."
        ),
    )


class SearchListingsOutput(BaseModel):
    """Output of :func:`search_listings`."""

    listings: list[Listing]
    pages_fetched: int
    detail_pages_fetched: int
    errors: list[str] = Field(default_factory=list)


class GetListingInput(BaseModel):
    """Input to :func:`get_listing`."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., description="Full Rightmove listing detail URL.")


class GetListingOutput(BaseModel):
    """Output of :func:`get_listing`."""

    listing: Listing | None


class ExtractListingUrlsInput(BaseModel):
    """Input to :func:`extract_listing_urls`."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(
        None,
        description="Rightmove search page URL to fetch + parse. Mutually exclusive with `html`.",
    )
    html: str | None = Field(
        None,
        description="Rightmove search page HTML to parse directly (no network). "
        "Mutually exclusive with `url`.",
    )


class ExtractListingUrlsOutput(BaseModel):
    """Output of :func:`extract_listing_urls`."""

    urls: list[str]


async def search_listings(
    inp: SearchListingsInput,
    *,
    crawler_factory,
) -> SearchListingsOutput:
    """Run a Rightmove search and return normalized listings.

    Dual-mode: if ``APIFY_API_TOKEN`` is set (and ``UK_PROPERTY_APIFY_MODE``
    isn't forced ``off``), delegates to the hosted ``rightmove-listings``
    Apify actor via :mod:`uk_property_apify_client`, which carries the full
    anti-bot moat. Otherwise runs the local :class:`SimpleCrawler` path.
    Both paths return the exact same :class:`SearchListingsOutput` shape.
    """
    from rightmove_mcp.apify_mode import maybe_delegate_search_listings

    delegated = await maybe_delegate_search_listings(inp)
    if delegated is not None:
        return delegated

    from uk_property_listings import SearchQuery, crawl_rightmove_search

    async with crawler_factory() as crawler:
        report = await crawl_rightmove_search(
            crawler,
            SearchQuery(
                location=inp.location,
                transaction=inp.transaction,
                min_price=inp.min_price,
                max_price=inp.max_price,
                min_beds=inp.min_beds,
                max_beds=inp.max_beds,
                max_pages=inp.max_pages,
            ),
            hydrate_details=inp.hydrate_details,
        )
    return SearchListingsOutput(
        listings=report.listings,
        pages_fetched=report.pages_fetched,
        detail_pages_fetched=report.detail_pages_fetched,
        errors=report.errors,
    )


async def get_listing(
    inp: GetListingInput,
    *,
    crawler_factory,
) -> GetListingOutput:
    """Fetch + parse a single Rightmove detail page."""
    async with crawler_factory() as crawler:
        result = await crawler.fetch(inp.url)
    listing = rm.parse_detail_page(result.html, source_url=result.final_url)
    return GetListingOutput(listing=listing)


async def extract_listing_urls(
    inp: ExtractListingUrlsInput,
    *,
    crawler_factory,
) -> ExtractListingUrlsOutput:
    """Extract Rightmove detail URLs from a page of HTML."""
    if inp.url and inp.html:
        raise ValueError("provide either `url` or `html`, not both")
    if not inp.url and not inp.html:
        raise ValueError("provide one of `url` or `html`")

    if inp.html is not None:
        html = inp.html
    else:
        assert inp.url is not None
        async with crawler_factory() as crawler:
            result = await crawler.fetch(inp.url, expect_search_markers=True)
        html = result.html

    urls = rm.extract_listing_urls(html)
    return ExtractListingUrlsOutput(urls=urls)


# ── Agent branch tools ─────────────────────────────────────────────────────


class GetAgentProfileInput(BaseModel):
    """Input to :func:`get_agent_profile`.

    Accepts either the Rightmove branch URL
    (``/estate-agents/agent/<company>/<slug>-<branchId>.html``) or the raw
    HTML.
    """

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(
        None,
        description=(
            "Full Rightmove branch URL, e.g. "
            "``https://www.rightmove.co.uk/estate-agents/agent/Foo/Bar-12345.html``. "
            "Mutually exclusive with ``html``."
        ),
    )
    html: str | None = Field(
        None,
        description="Raw branch-page HTML. Mutually exclusive with ``url``.",
    )
    include_stock: bool = Field(
        False,
        description=(
            "If true, also return the branch's full live + recently-sold "
            "stock as normalized Listing cards. Rightmove's page ships all "
            "four inventories (sales, lettings, previous-sold, previous-let) "
            "in one ``__NEXT_DATA__`` blob, so this adds no extra HTTP."
        ),
    )


class GetAgentProfileOutput(BaseModel):
    """Output of :func:`get_agent_profile`."""

    profile: AgentProfile | None
    stock: list[Listing] | None = None


class ListAgentStockInput(BaseModel):
    """Input to :func:`list_agent_stock`."""

    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(
        None, description="Rightmove branch URL. Mutually exclusive with ``html``."
    )
    html: str | None = Field(
        None, description="Raw branch-page HTML. Mutually exclusive with ``url``."
    )
    transaction: Literal["sale", "rent", "all"] = Field(
        "all",
        description=(
            "Filter stock by transaction type. ``all`` (default) returns "
            "sales + lettings combined."
        ),
    )
    include_sold: bool = Field(
        True,
        description=(
            "If true (default), include previous-sold / previous-let cards in "
            "the response. These are tagged with the ``sold_stc`` / "
            "``let_agreed`` ListingFeature so callers can filter downstream. "
            "Set to false to return only live stock."
        ),
    )


class ListAgentStockOutput(BaseModel):
    """Output of :func:`list_agent_stock`."""

    listings: list[Listing]
    agent_source_id: str | None = None
    agent_source_url: str | None = None


async def get_agent_profile(
    inp: GetAgentProfileInput,
    *,
    crawler_factory,
) -> GetAgentProfileOutput:
    """Fetch + parse a Rightmove branch page into an :class:`AgentProfile`.

    Rightmove embeds the full branch payload (contact, bio, fees, team,
    stock) under ``__NEXT_DATA__.props.pageProps.data.branchProfileResponse``.
    A single HTTP call therefore yields the entire profile + optional
    search-card stock.
    """
    html, source_url = await _fetch_branch_html(inp.url, inp.html, crawler_factory)
    profile = rm.parse_branch_page(html, source_url=source_url)
    stock: list[Listing] | None = None
    if inp.include_stock:
        stock = rm.parse_branch_stock(html, source_url=source_url)
    return GetAgentProfileOutput(profile=profile, stock=stock)


async def list_agent_stock(
    inp: ListAgentStockInput,
    *,
    crawler_factory,
) -> ListAgentStockOutput:
    """Return the Rightmove branch's sales + lettings stock as
    :class:`Listing` search cards."""
    from uk_property_scrapers.schema import ListingFeature

    html, source_url = await _fetch_branch_html(inp.url, inp.html, crawler_factory)
    listings = rm.parse_branch_stock(html, source_url=source_url)

    if inp.transaction != "all":
        listings = [l for l in listings if l.transaction_type.value == inp.transaction]
    if not inp.include_sold:
        listings = [
            l
            for l in listings
            if ListingFeature.SOLD_STC not in l.features
            and ListingFeature.LET_AGREED not in l.features
        ]

    profile = rm.parse_branch_page(html, source_url=source_url)
    return ListAgentStockOutput(
        listings=listings,
        agent_source_id=profile.source_id if profile else None,
        agent_source_url=str(profile.source_url) if profile else None,
    )


async def _fetch_branch_html(
    url: str | None,
    html: str | None,
    crawler_factory,
) -> tuple[str, str | None]:
    """Resolve ``(url, html)`` into ``(html, canonical_url)``."""
    if url and html:
        raise ValueError("provide either `url` or `html`, not both")
    if not url and not html:
        raise ValueError("provide one of `url` or `html`")

    if html is not None:
        return html, url

    assert url is not None
    if not _AGENT_URL_RE.match(url):
        raise ValueError(
            "URL does not look like a Rightmove branch page. Expected "
            "`https://www.rightmove.co.uk/estate-agents/agent/<Company>/<Slug>-<branchId>.html`"
        )
    async with crawler_factory() as crawler:
        result = await crawler.fetch(url)
    return result.html, result.final_url


# ── Action tools (send inquiry / request viewing / request valuation) ───────
#
# Thin wrappers around :mod:`uk_property_apify_shared.actions.mcp` bound
# to the Rightmove :class:`PortalActionBundle`. Rightmove specifically
# defaults its "let me know about similar properties" opt-in checkbox
# to ON — the shared :class:`FormSubmitter` only ticks it when
# ``submission.opt_in=True``, and our MCP tool surface defaults
# ``opt_in=False``, so the user stays un-opted-in unless they explicitly
# set the flag.


async def send_inquiry(inp: SendInquiryInput) -> SendInquiryOutput:
    """Contact the agent for a Rightmove listing.

    ``dry_run`` defaults to True (validate + fill, do not submit) and
    ``opt_in`` defaults to False so Rightmove's default-on marketing
    checkbox is explicitly unticked before submit.
    """
    return await _action_mcp.send_inquiry(inp, bundle=RIGHTMOVE_BUNDLE)


async def request_viewing(inp: RequestViewingInput) -> RequestViewingOutput:
    """Request a viewing for a Rightmove listing."""
    return await _action_mcp.request_viewing(inp, bundle=RIGHTMOVE_BUNDLE)


async def request_free_valuation(
    inp: RequestFreeValuationInput,
) -> RequestFreeValuationOutput:
    """Submit a free-valuation / sell-side lead through Rightmove."""
    return await _action_mcp.request_free_valuation(inp, bundle=RIGHTMOVE_BUNDLE)


# ── Delta / watch tools ─────────────────────────────────────────────


def _rightmove_status_text(listing: Listing) -> str | None:
    """Extract Rightmove's status ribbon from a parsed :class:`Listing`.

    Rightmove surfaces its current listing status in ``displayStatus``
    on the propertyData payload (captured into ``raw_site_fields`` as
    ``listingStatus`` / ``displayStatus`` depending on the page).
    """
    raw = listing.raw_site_fields or {}
    for key in ("listingStatus", "displayStatus", "listing_status", "status_badges"):
        value = raw.get(key)
        if value:
            return str(value)
    return None


class WatchListingInput(BaseModel):
    """Input to :func:`watch_listing`."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., description="Full Rightmove listing URL to snapshot.")
    store_path: str | None = Field(
        None,
        description=(
            "Override the snapshot store path. Defaults to "
            "``~/.uk-property-mcp/rightmove.sqlite``."
        ),
    )


class WatchQueryInput(BaseModel):
    """Input to :func:`watch_query`."""

    model_config = ConfigDict(extra="forbid")

    location: str = Field(..., min_length=1)
    transaction: Literal["sale", "rent"] = "sale"
    min_price: int | None = Field(None, ge=0)
    max_price: int | None = Field(None, ge=0)
    min_beds: int | None = Field(None, ge=0, le=20)
    max_beds: int | None = Field(None, ge=0, le=20)
    max_pages: int = Field(1, ge=1, le=10)
    hydrate_details: bool = False
    store_path: str | None = None


async def watch_listing(
    inp: WatchListingInput,
    *,
    crawler_factory,
) -> WatchListingOutput:
    """Fetch a single Rightmove listing, snapshot it, return change events."""
    async with crawler_factory() as crawler:
        result = await crawler.fetch(inp.url)
    listing = rm.parse_detail_page(result.html, source_url=result.final_url)
    store = open_store(inp.store_path, Source.RIGHTMOVE)
    try:
        snapshots, events = await ingest_listings(
            [listing], store=store, status_text_fn=_rightmove_status_text
        )
    finally:
        await store.close()
    snapshot = snapshots[0]
    return WatchListingOutput(
        source=Source.RIGHTMOVE,
        source_id=listing.source_id,
        snapshot=snapshot,
        events=events,
    )


async def watch_query(
    inp: WatchQueryInput,
    *,
    crawler_factory,
) -> WatchQueryOutput:
    """Run a Rightmove search and ingest every listing into the snapshot store."""
    search_input = SearchListingsInput(
        location=inp.location,
        transaction=inp.transaction,
        min_price=inp.min_price,
        max_price=inp.max_price,
        min_beds=inp.min_beds,
        max_beds=inp.max_beds,
        max_pages=inp.max_pages,
        hydrate_details=inp.hydrate_details,
    )
    search_result = await search_listings(search_input, crawler_factory=crawler_factory)
    store = open_store(inp.store_path, Source.RIGHTMOVE)
    try:
        _, events = await ingest_listings(
            search_result.listings, store=store, status_text_fn=_rightmove_status_text
        )
    finally:
        await store.close()
    kinds: dict[str, int] = {}
    for event in events:
        kinds[event.kind.value] = kinds.get(event.kind.value, 0) + 1
    return WatchQueryOutput(
        ingested=len(search_result.listings),
        events=events,
        kinds=kinds,
    )


async def reductions_firehose(inp: FirehoseInput) -> FirehoseOutput:
    """Return recent ``PRICE_REDUCED`` events from the Rightmove snapshot store."""
    events = await load_firehose(
        inp, source=Source.RIGHTMOVE, kinds=[ListingChangeKind.PRICE_REDUCED]
    )
    return FirehoseOutput(kind="price_reduced", events=events)


async def new_listings_firehose(inp: FirehoseInput) -> FirehoseOutput:
    """Return recent ``NEW`` events from the Rightmove snapshot store."""
    events = await load_firehose(
        inp, source=Source.RIGHTMOVE, kinds=[ListingChangeKind.NEW]
    )
    return FirehoseOutput(kind="new", events=events)


async def back_on_market(inp: FirehoseInput) -> FirehoseOutput:
    """Return recent ``BACK_ON_MARKET`` events from the Rightmove snapshot store."""
    events = await load_firehose(
        inp, source=Source.RIGHTMOVE, kinds=[ListingChangeKind.BACK_ON_MARKET]
    )
    return FirehoseOutput(kind="back_on_market", events=events)
