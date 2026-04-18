"""MCP server wiring for Rightmove - exposes :mod:`.tools` over stdio by default."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from rightmove_mcp.tools import (
    ExtractListingUrlsInput,
    GetListingInput,
    SearchListingsInput,
    extract_listing_urls,
    get_listing,
    search_listings,
)

logger = logging.getLogger("rightmove_mcp")


@asynccontextmanager
async def default_crawler_factory():
    """Yield a :class:`SimpleCrawler` for best-effort httpx-only crawling.

    The public MCP ships with :class:`uk_property_listings.SimpleCrawler`,
    which has no anti-bot moat, no Playwright fallback, and no
    ``curl_cffi`` transport - it is the "free tier" crawler. For
    production-grade reliability (proxy rotation, TLS fingerprint
    impersonation, browser fallback, tier escalation), use the hosted
    Apify actors instead.
    """
    from uk_property_listings import SimpleCrawler

    async with SimpleCrawler() as crawler:
        yield crawler


def build_server() -> Any:
    """Construct an MCP :class:`FastMCP` server with all Rightmove tools registered."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - runtime-only dep
        raise RuntimeError(
            "mcp SDK not installed. Install with `pip install rightmove-mcp` or `pip install mcp`."
        ) from exc

    server = FastMCP("rightmove-mcp", "0.1.0")

    @server.tool(
        name="search_listings",
        description=(
            "Search Rightmove UK property listings by location, transaction type, "
            "price range and bed range. Returns normalized Listing records "
            "(prices in pence)."
        ),
    )
    async def _search(
        location: str,
        transaction: str = "sale",
        min_price: int | None = None,
        max_price: int | None = None,
        min_beds: int | None = None,
        max_beds: int | None = None,
        max_pages: int = 1,
        hydrate_details: bool = False,
    ) -> dict[str, Any]:
        inp = SearchListingsInput(
            location=location,
            transaction=transaction,  # type: ignore[arg-type]
            min_price=min_price,
            max_price=max_price,
            min_beds=min_beds,
            max_beds=max_beds,
            max_pages=max_pages,
            hydrate_details=hydrate_details,
        )
        out = await search_listings(inp, crawler_factory=default_crawler_factory)
        return out.model_dump(mode="json")

    @server.tool(
        name="get_listing",
        description=(
            "Fetch + parse a single Rightmove listing detail page into a canonical Listing record."
        ),
    )
    async def _get(url: str) -> dict[str, Any]:
        out = await get_listing(GetListingInput(url=url), crawler_factory=default_crawler_factory)
        return out.model_dump(mode="json")

    @server.tool(
        name="extract_listing_urls",
        description=(
            "Given a Rightmove search/page URL (or its raw HTML), return the list "
            "of listing detail URLs present on that page."
        ),
    )
    async def _extract(url: str | None = None, html: str | None = None) -> dict[str, Any]:
        out = await extract_listing_urls(
            ExtractListingUrlsInput(url=url, html=html),
            crawler_factory=default_crawler_factory,
        )
        return out.model_dump(mode="json")

    return server


def run_stdio() -> None:
    """CLI entry point - serve over stdio."""
    logging.basicConfig(level=logging.INFO)
    server = build_server()
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    run_stdio()
