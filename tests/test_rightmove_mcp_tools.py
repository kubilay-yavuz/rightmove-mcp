"""Unit tests for Rightmove MCP tool functions.

Mirror of ``mcps/zoopla-mcp/tests/test_tools.py``. Drives the tools with a
respx-mocked :class:`SimpleCrawler` rather than the
real network. The ``crawler_factory`` contract is satisfied by a lightweight
``AsyncContextManager`` that yields a :class:`SimpleCrawler`.
Rightmove fixture pages are re-used
from the shared ``packages/scrapers/tests/fixtures/rightmove`` folder.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import respx
from uk_property_listings import SimpleCrawler

from rightmove_mcp.tools import (
    ExtractListingUrlsInput,
    GetListingInput,
    SearchListingsInput,
    extract_listing_urls,
    get_listing,
    search_listings,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


@asynccontextmanager
async def _crawler_factory():
    async with SimpleCrawler(request_timeout_s=5.0) as crawler:
        yield crawler


class TestSearchListings:
    @respx.mock
    async def test_returns_listings(self) -> None:
        html = _read("search_cambridge_2026-04.html")
        respx.get(url__regex=r"https://www\.rightmove\.co\.uk/property-for-sale/.*").mock(
            return_value=httpx.Response(200, html=html)
        )
        result = await search_listings(
            SearchListingsInput(location="Cambridge", transaction="sale", max_pages=1),
            crawler_factory=_crawler_factory,
        )
        assert result.pages_fetched == 1
        assert len(result.listings) >= 5
        assert all(lst.source.value == "rightmove" for lst in result.listings)

    async def test_validates_input(self) -> None:
        with pytest.raises(ValueError):
            SearchListingsInput(location="")

    async def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValueError):
            SearchListingsInput(location="Cambridge", foo=1)  # type: ignore[call-arg]

    @respx.mock
    async def test_rent_search(self) -> None:
        html = _read("torent_cambridge_2026-04.html")
        respx.get(url__regex=r"https://www\.rightmove\.co\.uk/property-to-rent/.*").mock(
            return_value=httpx.Response(200, html=html)
        )
        result = await search_listings(
            SearchListingsInput(location="Cambridge", transaction="rent", max_pages=1),
            crawler_factory=_crawler_factory,
        )
        assert result.pages_fetched == 1
        assert len(result.listings) >= 1


class TestGetListing:
    @respx.mock
    async def test_returns_listing(self) -> None:
        detail_html = _read("detail_173261858_2026-04.html")
        respx.get("https://www.rightmove.co.uk/properties/173261858").mock(
            return_value=httpx.Response(200, html=detail_html)
        )
        out = await get_listing(
            GetListingInput(url="https://www.rightmove.co.uk/properties/173261858"),
            crawler_factory=_crawler_factory,
        )
        assert out.listing is not None
        assert out.listing.source.value == "rightmove"
        assert out.listing.listing_type.value == "detail"


class TestExtractListingUrls:
    @respx.mock
    async def test_via_url(self) -> None:
        html = _read("search_cambridge_2026-04.html")
        respx.get(
            "https://www.rightmove.co.uk/property-for-sale/find.html?searchLocation=Cambridge"
        ).mock(return_value=httpx.Response(200, html=html))
        out = await extract_listing_urls(
            ExtractListingUrlsInput(
                url="https://www.rightmove.co.uk/property-for-sale/find.html?searchLocation=Cambridge"
            ),
            crawler_factory=_crawler_factory,
        )
        assert len(out.urls) >= 5
        assert all(url.startswith("https://www.rightmove.co.uk/") for url in out.urls)

    async def test_via_inline_html(self) -> None:
        html = _read("search_cambridge_2026-04.html")
        out = await extract_listing_urls(
            ExtractListingUrlsInput(html=html),
            crawler_factory=_crawler_factory,
        )
        assert len(out.urls) >= 5

    async def test_both_rejected(self) -> None:
        with pytest.raises(ValueError):
            await extract_listing_urls(
                ExtractListingUrlsInput(url="https://x", html="<html/>"),
                crawler_factory=_crawler_factory,
            )

    async def test_neither_rejected(self) -> None:
        with pytest.raises(ValueError):
            await extract_listing_urls(
                ExtractListingUrlsInput(),
                crawler_factory=_crawler_factory,
            )
