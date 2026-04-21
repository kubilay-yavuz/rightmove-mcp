"""Tests for the Rightmove delta / watch MCP tools.

Drives :func:`watch_listing`, :func:`watch_query`, and the three
firehose helpers through a respx-mocked ``SimpleCrawler`` and a
per-test SQLite snapshot store (``tmp_path``). Each firehose tool
reads from the *same* store, so we ingest via ``watch_*`` and then
assert the firehose emits the expected kinds.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import respx
from uk_property_apify_shared.delta import FirehoseInput
from uk_property_listings import SimpleCrawler

from rightmove_mcp.tools import (
    WatchListingInput,
    WatchQueryInput,
    back_on_market,
    new_listings_firehose,
    reductions_firehose,
    watch_listing,
    watch_query,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


@asynccontextmanager
async def _crawler_factory():
    async with SimpleCrawler(request_timeout_s=5.0) as crawler:
        yield crawler


class TestWatchListing:
    @respx.mock
    async def test_first_watch_emits_new(self, tmp_path: Path) -> None:
        html = _read("detail_173261858_2026-04.html")
        respx.get("https://www.rightmove.co.uk/properties/173261858").mock(
            return_value=httpx.Response(200, html=html)
        )
        store_path = str(tmp_path / "watch.sqlite")
        out = await watch_listing(
            WatchListingInput(
                url="https://www.rightmove.co.uk/properties/173261858",
                store_path=store_path,
            ),
            crawler_factory=_crawler_factory,
        )
        assert out.source.value == "rightmove"
        assert out.snapshot.source_id == out.source_id
        kinds = [e.kind.value for e in out.events]
        assert "new" in kinds

    @respx.mock
    async def test_repeat_watch_is_unchanged(self, tmp_path: Path) -> None:
        html = _read("detail_173261858_2026-04.html")
        respx.get("https://www.rightmove.co.uk/properties/173261858").mock(
            return_value=httpx.Response(200, html=html)
        )
        store_path = str(tmp_path / "watch.sqlite")
        inp = WatchListingInput(
            url="https://www.rightmove.co.uk/properties/173261858",
            store_path=store_path,
        )
        first = await watch_listing(inp, crawler_factory=_crawler_factory)
        second = await watch_listing(inp, crawler_factory=_crawler_factory)
        assert [e.kind.value for e in first.events] == ["new"]
        assert [e.kind.value for e in second.events] == ["unchanged"]


class TestWatchQuery:
    @respx.mock
    async def test_ingests_search_results(self, tmp_path: Path) -> None:
        html = _read("search_cambridge_2026-04.html")
        respx.get(url__regex=r"https://www\.rightmove\.co\.uk/property-for-sale/.*").mock(
            return_value=httpx.Response(200, html=html)
        )
        store_path = str(tmp_path / "watch.sqlite")
        out = await watch_query(
            WatchQueryInput(
                location="Cambridge",
                transaction="sale",
                max_pages=1,
                store_path=store_path,
            ),
            crawler_factory=_crawler_factory,
        )
        assert out.ingested >= 1
        assert len(out.events) >= 1
        assert out.kinds.get("new", 0) >= 1


class TestFirehoses:
    @respx.mock
    async def test_new_listings_firehose_reads_back(self, tmp_path: Path) -> None:
        html = _read("search_cambridge_2026-04.html")
        respx.get(url__regex=r"https://www\.rightmove\.co\.uk/property-for-sale/.*").mock(
            return_value=httpx.Response(200, html=html)
        )
        store_path = str(tmp_path / "firehose.sqlite")
        await watch_query(
            WatchQueryInput(
                location="Cambridge",
                transaction="sale",
                max_pages=1,
                store_path=store_path,
            ),
            crawler_factory=_crawler_factory,
        )
        firehose = await new_listings_firehose(
            FirehoseInput(limit=100, store_path=store_path)
        )
        assert firehose.kind == "new"
        assert len(firehose.events) >= 1
        assert all(e.kind.value == "new" for e in firehose.events)

    async def test_reductions_firehose_empty_when_no_price_drops(
        self, tmp_path: Path
    ) -> None:
        store_path = str(tmp_path / "firehose.sqlite")
        firehose = await reductions_firehose(
            FirehoseInput(limit=100, store_path=store_path)
        )
        assert firehose.kind == "price_reduced"
        assert firehose.events == []

    async def test_back_on_market_empty_on_fresh_store(self, tmp_path: Path) -> None:
        store_path = str(tmp_path / "firehose.sqlite")
        firehose = await back_on_market(
            FirehoseInput(limit=100, store_path=store_path)
        )
        assert firehose.kind == "back_on_market"
        assert firehose.events == []
