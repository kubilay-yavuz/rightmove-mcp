"""Unit tests for Rightmove MCP agent tool functions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import respx
from uk_property_listings import SimpleCrawler
from uk_property_scrapers.schema import ListingFeature

from rightmove_mcp.tools import (
    GetAgentProfileInput,
    ListAgentStockInput,
    get_agent_profile,
    list_agent_stock,
)

FIXTURES = Path(__file__).parent / "fixtures"
_AGENT_HTML = (FIXTURES / "agent_hockeys_cambridge_2026-04.html").read_text(
    encoding="utf-8"
)
_AGENT_URL = (
    "https://www.rightmove.co.uk/estate-agents/agent/Hockeys/Cambridge-211166.html"
)


@asynccontextmanager
async def _crawler_factory():
    async with SimpleCrawler(request_timeout_s=5.0) as crawler:
        yield crawler


class TestGetAgentProfile:
    @respx.mock
    async def test_via_url(self) -> None:
        respx.get(_AGENT_URL).mock(return_value=httpx.Response(200, html=_AGENT_HTML))
        out = await get_agent_profile(
            GetAgentProfileInput(url=_AGENT_URL),
            crawler_factory=_crawler_factory,
        )
        assert out.profile is not None
        assert out.profile.source_id == "211166"
        assert out.profile.name == "Hockeys, Cambridge"
        assert out.stock is None

    async def test_via_inline_html(self) -> None:
        out = await get_agent_profile(
            GetAgentProfileInput(html=_AGENT_HTML),
            crawler_factory=_crawler_factory,
        )
        assert out.profile is not None
        assert out.profile.address == "10 Mill Road, Cambridge, CB1 2AD"
        assert out.profile.phone == "01223 972878"

    async def test_include_stock_returns_live_and_sold(self) -> None:
        out = await get_agent_profile(
            GetAgentProfileInput(html=_AGENT_HTML, include_stock=True),
            crawler_factory=_crawler_factory,
        )
        assert out.stock is not None
        assert len(out.stock) >= 10
        assert any(ListingFeature.SOLD_STC in l.features for l in out.stock)

    async def test_url_shape_validated(self) -> None:
        with pytest.raises(ValueError, match="branch page"):
            await get_agent_profile(
                GetAgentProfileInput(
                    url="https://www.rightmove.co.uk/properties/173261858"
                ),
                crawler_factory=_crawler_factory,
            )


class TestListAgentStock:
    async def test_returns_listings(self) -> None:
        out = await list_agent_stock(
            ListAgentStockInput(html=_AGENT_HTML),
            crawler_factory=_crawler_factory,
        )
        assert len(out.listings) >= 10
        assert out.agent_source_id == "211166"

    async def test_exclude_sold(self) -> None:
        out = await list_agent_stock(
            ListAgentStockInput(html=_AGENT_HTML, include_sold=False),
            crawler_factory=_crawler_factory,
        )
        assert out.listings
        for listing in out.listings:
            assert ListingFeature.SOLD_STC not in listing.features
            assert ListingFeature.LET_AGREED not in listing.features

    async def test_include_sold_default(self) -> None:
        out = await list_agent_stock(
            ListAgentStockInput(html=_AGENT_HTML),
            crawler_factory=_crawler_factory,
        )
        assert any(ListingFeature.SOLD_STC in l.features for l in out.listings)
