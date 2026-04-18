"""Core Rightmove tool implementations - transport-agnostic.

Each tool takes a Pydantic input model and returns a Pydantic output model.
The :mod:`.server` layer turns these into MCP tool handlers over stdio or
HTTP; the same functions are reusable directly from Python.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from uk_property_scrapers import rightmove as rm
from uk_property_scrapers.schema import Listing  # noqa: TC002 (Pydantic field type)


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
        description="Also fetch each listing's detail page for richer fields.",
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
