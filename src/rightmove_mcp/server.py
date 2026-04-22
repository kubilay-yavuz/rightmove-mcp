"""MCP server wiring for Rightmove - exposes :mod:`.tools` over stdio by default."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from uk_property_apify_shared.delta import FirehoseInput

from rightmove_mcp.tools import (
    ExtractListingUrlsInput,
    GetAgentProfileInput,
    GetListingInput,
    ListAgentStockInput,
    RequestFreeValuationInput,
    RequestViewingInput,
    SearchListingsInput,
    SendInquiryInput,
    WatchListingInput,
    WatchQueryInput,
    back_on_market,
    extract_listing_urls,
    get_agent_profile,
    get_listing,
    list_agent_stock,
    new_listings_firehose,
    reductions_firehose,
    request_free_valuation,
    request_viewing,
    search_listings,
    send_inquiry,
    watch_listing,
    watch_query,
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
            "Search Rightmove UK property listings by location, transaction "
            "type, price range and bed range. Returns normalized Listing "
            "records (prices in pence). Card-level output has address, "
            "price, beds/baths, property_type, tenure, agent summary and "
            "image_urls. Set hydrate_details=True to additionally fetch "
            "each detail page and populate the rich fields from "
            "`window.PAGE_MODEL`: `description`, `features`, `coords`, "
            "`first_listed_at`, `lease` (years remaining, ground rent, "
            "service charge), `broadband` (FTTP/FTTC tier), `epc`, "
            "`council_tax_band`, `timeline` (HMLR sale history + "
            "Rightmove price changes) and `material_information` (the "
            "Material Information Report link + NTS disclosure bundle)."
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
            "Fetch + parse a single Rightmove listing detail page into a "
            "canonical Listing record. Enriches from Rightmove's embedded "
            "`window.PAGE_MODEL` with: tenure, lease economics, EPC, "
            "broadband tier, council_tax_band, first_listed_at, the full "
            "HMLR sale history merged with Rightmove price-change events "
            "into `timeline`, branch contact (phone, postal address, "
            "branch source_id, franchise group_name), and the "
            "material_information bundle."
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

    @server.tool(
        name="get_agent_profile",
        description=(
            "Fetch a Rightmove estate-agent branch page "
            "(``/estate-agents/agent/<Company>/<Slug>-<branchId>.html``) and "
            "return a canonical AgentProfile: branchId, display name, "
            "flattened postal address, sales phone + switchboard phone, bio, "
            "opening hours, logo, stock summary (for_sale, sold_stc, "
            "median_price_pence, sold_in_last_12m) and team members. Set "
            "``include_stock=True`` to additionally return the full "
            "sales + lettings inventory, including previous-sold and "
            "previous-let cards that carry the ``sold_stc`` / "
            "``let_agreed`` ListingFeature flags for downstream filtering."
        ),
    )
    async def _get_agent_profile(
        url: str | None = None,
        html: str | None = None,
        include_stock: bool = False,
    ) -> dict[str, Any]:
        out = await get_agent_profile(
            GetAgentProfileInput(url=url, html=html, include_stock=include_stock),
            crawler_factory=default_crawler_factory,
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="list_agent_stock",
        description=(
            "Return the sales + lettings inventory for a Rightmove branch as "
            "Listing search cards (same shape as ``search_listings``). "
            "``transaction`` filters sale / rent / all. ``include_sold`` "
            "(default true) controls whether previously-sold-STC and "
            "previously-let-agreed cards are included; these carry the "
            "``sold_stc`` / ``let_agreed`` ListingFeature flags so callers "
            "can distinguish live stock from trade history."
        ),
    )
    async def _list_agent_stock(
        url: str | None = None,
        html: str | None = None,
        transaction: str = "all",
        include_sold: bool = True,
    ) -> dict[str, Any]:
        out = await list_agent_stock(
            ListAgentStockInput(
                url=url,
                html=html,
                transaction=transaction,  # type: ignore[arg-type]
                include_sold=include_sold,
            ),
            crawler_factory=default_crawler_factory,
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="send_inquiry",
        description=(
            "Contact the agent for a Rightmove listing. Fills Rightmove's "
            "``Contact agent`` form with the supplied buyer identity, "
            "message body, and optional buyer-stage disclosures. "
            "SAFETY DEFAULTS: ``dry_run=True``, ``consent_to_portal_tcs=False``, "
            "``opt_in=False``. Rightmove specifically defaults its "
            "``marketing / similar-properties`` checkbox to ON in the UI; "
            "this tool explicitly UNTICKS it unless ``opt_in=True`` is set "
            "— so buyers never get silently subscribed. Returns an "
            "``InquiryResult`` with outcome, portal reference id, captcha "
            "status, and a snippet of the confirmation page."
        ),
    )
    async def _send_inquiry(
        listing_url: str,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        message: str,
        interest: str = "unknown",
        position: str = "unknown",
        mortgage_status: str = "unknown",
        opt_in: bool = False,
        consent_to_portal_tcs: bool = False,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        out = await send_inquiry(
            SendInquiryInput(
                listing_url=listing_url,
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                message=message,
                interest=interest,  # type: ignore[arg-type]
                position=position,  # type: ignore[arg-type]
                mortgage_status=mortgage_status,  # type: ignore[arg-type]
                opt_in=opt_in,
                consent_to_portal_tcs=consent_to_portal_tcs,
                dry_run=dry_run,
            )
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="request_viewing",
        description=(
            "Request a viewing for a Rightmove listing using the portal's "
            "dedicated viewing-request form (``Request a viewing`` CTA). "
            "Accepts up to 3 preferred ISO-8601 slots and an optional note. "
            "Same DRY_RUN / consent / opt-in safety defaults as "
            "``send_inquiry``. Rightmove's opt-in checkbox stays UNTICKED "
            "unless ``opt_in=True`` is explicitly set."
        ),
    )
    async def _request_viewing(
        listing_url: str,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        preferred_slots: list[str] | None = None,
        message: str | None = None,
        opt_in: bool = False,
        consent_to_portal_tcs: bool = False,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        from datetime import datetime
        slots = [datetime.fromisoformat(s) for s in (preferred_slots or [])]
        out = await request_viewing(
            RequestViewingInput(
                listing_url=listing_url,
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                preferred_slots=slots,
                message=message,
                opt_in=opt_in,
                consent_to_portal_tcs=consent_to_portal_tcs,
                dry_run=dry_run,
            )
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="request_free_valuation",
        description=(
            "Submit a free-valuation / sell-side lead via Rightmove. "
            "Takes a display address (+ optional postcode or outcode), "
            "buyer identity, target transaction (``sale`` | ``rent``), "
            "property type + bedroom count. Uses Rightmove's portal-wide "
            "``free-valuation`` landing page by default; override "
            "``valuation_page_url`` to pin the request to a specific "
            "branch's ``/valuation/`` page. Same DRY_RUN / consent / "
            "opt-in safety defaults as ``send_inquiry``."
        ),
    )
    async def _request_free_valuation(
        address: str,
        first_name: str,
        last_name: str,
        email: str,
        phone: str,
        postcode: str | None = None,
        postcode_outcode: str | None = None,
        transaction: str = "sale",
        property_type: str = "unknown",
        bedrooms: int | None = None,
        target_agent_source_id: str | None = None,
        valuation_page_url: str | None = None,
        opt_in: bool = False,
        consent_to_portal_tcs: bool = False,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        out = await request_free_valuation(
            RequestFreeValuationInput(
                address=address,
                postcode=postcode,
                postcode_outcode=postcode_outcode,
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                transaction=transaction,  # type: ignore[arg-type]
                property_type=property_type,  # type: ignore[arg-type]
                bedrooms=bedrooms,
                target_agent_source_id=target_agent_source_id,
                valuation_page_url=valuation_page_url,
                opt_in=opt_in,
                consent_to_portal_tcs=consent_to_portal_tcs,
                dry_run=dry_run,
            )
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="watch_listing",
        description=(
            "Fetch a single Rightmove listing, snapshot the watch-relevant "
            "fields (price, status, photos, description, agent, features), "
            "and persist the snapshot to a local SQLite store. Returns "
            "the structured change events (``price_reduced``, ``new``, "
            "``back_on_market``, ``photos_added``, …) that fired vs. the "
            "previous snapshot for the same listing. The snapshot store "
            "lives at ``~/.uk-property-mcp/rightmove.sqlite`` by default."
        ),
    )
    async def _watch_listing(
        url: str,
        store_path: str | None = None,
    ) -> dict[str, Any]:
        out = await watch_listing(
            WatchListingInput(url=url, store_path=store_path),
            crawler_factory=default_crawler_factory,
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="watch_query",
        description=(
            "Run a Rightmove search and ingest every returned listing into "
            "the snapshot store. Downstream firehose tools "
            "(``reductions_firehose``, ``new_listings_firehose``, "
            "``back_on_market``) read from the same store, so this is "
            "how you keep the watch pipeline fresh — schedule "
            "``watch_query`` on a cron, then read out deltas via the "
            "firehose tools."
        ),
    )
    async def _watch_query(
        location: str,
        transaction: str = "sale",
        min_price: int | None = None,
        max_price: int | None = None,
        min_beds: int | None = None,
        max_beds: int | None = None,
        max_pages: int = 1,
        hydrate_details: bool = False,
        store_path: str | None = None,
    ) -> dict[str, Any]:
        out = await watch_query(
            WatchQueryInput(
                location=location,
                transaction=transaction,  # type: ignore[arg-type]
                min_price=min_price,
                max_price=max_price,
                min_beds=min_beds,
                max_beds=max_beds,
                max_pages=max_pages,
                hydrate_details=hydrate_details,
                store_path=store_path,
            ),
            crawler_factory=default_crawler_factory,
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="reductions_firehose",
        description=(
            "Return recent ``price_reduced`` events from the Rightmove "
            "snapshot store. Use ``since`` (ISO-8601) to page through "
            "deltas; ``limit`` caps the returned set (max 1000)."
        ),
    )
    async def _reductions_firehose(
        since: str | None = None,
        limit: int = 100,
        store_path: str | None = None,
    ) -> dict[str, Any]:
        from datetime import datetime

        out = await reductions_firehose(
            FirehoseInput(
                since=datetime.fromisoformat(since) if since else None,
                limit=limit,
                store_path=store_path,
            )
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="new_listings_firehose",
        description=(
            "Return recent ``new`` events (first-time-seen listings) "
            "from the Rightmove snapshot store."
        ),
    )
    async def _new_listings_firehose(
        since: str | None = None,
        limit: int = 100,
        store_path: str | None = None,
    ) -> dict[str, Any]:
        from datetime import datetime

        out = await new_listings_firehose(
            FirehoseInput(
                since=datetime.fromisoformat(since) if since else None,
                limit=limit,
                store_path=store_path,
            )
        )
        return out.model_dump(mode="json")

    @server.tool(
        name="back_on_market",
        description=(
            "Return recent ``back_on_market`` events (listings that went "
            "from Sold STC / Under Offer back to Available) from the "
            "Rightmove snapshot store."
        ),
    )
    async def _back_on_market(
        since: str | None = None,
        limit: int = 100,
        store_path: str | None = None,
    ) -> dict[str, Any]:
        from datetime import datetime

        out = await back_on_market(
            FirehoseInput(
                since=datetime.fromisoformat(since) if since else None,
                limit=limit,
                store_path=store_path,
            )
        )
        return out.model_dump(mode="json")

    return server


def run_stdio() -> None:
    """CLI entry point - serve over stdio."""
    logging.basicConfig(level=logging.INFO)
    server = build_server()
    asyncio.run(server.run_stdio_async())


def run_http() -> None:
    """CLI entry point - serve over Streamable HTTP.

    Used by Smithery's hosted container runtime (where stdio is no longer
    supported). Binds to ``HOST:PORT`` from the environment, defaulting to
    ``0.0.0.0:8081``. Runs in stateless mode so the gateway can route each
    request independently.

    Local users on Claude Desktop / Cursor keep using :func:`run_stdio`.
    """
    import os

    logging.basicConfig(level=logging.INFO)
    server = build_server()
    server.settings.host = os.environ.get("HOST", "0.0.0.0")
    server.settings.port = int(os.environ.get("PORT", "8081"))
    server.settings.stateless_http = True
    server.run(transport="streamable-http")


if __name__ == "__main__":
    run_stdio()
