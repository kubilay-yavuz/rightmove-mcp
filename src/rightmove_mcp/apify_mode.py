"""Optional delegation of Rightmove MCP calls to the hosted
``rightmove-listings`` Apify actor.

Mirror of :mod:`zoopla_mcp.apify_mode`. See that module's docstring for the
full design rationale; this file only differs in the actor key and source
string. When the shared logic grows to a fourth copy (e.g. the agent
tool's ``search_rightmove``) we should extract a single helper in
``uk-property-apify-client``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from uk_property_apify_client import ApifyDelegation, DelegationError
from uk_property_scrapers.schema import Listing

if TYPE_CHECKING:
    from rightmove_mcp.tools import SearchListingsInput, SearchListingsOutput


_ACTOR_KEY = "rightmove-listings"
_SOURCE = "rightmove"


async def maybe_delegate_search_listings(
    inp: SearchListingsInput,
) -> SearchListingsOutput | None:
    """Return a :class:`SearchListingsOutput` from the hosted actor if
    configured, else ``None`` so the caller can fall back to the local
    ``SimpleCrawler`` path.
    """
    delegation = ApifyDelegation.resolve(_ACTOR_KEY)
    if delegation is None:
        return None

    actor_input = _build_actor_input(inp)
    result = await delegation.call(actor_input)
    return _map_result_to_output(result.items, result.run_meta)


def _build_actor_input(inp: SearchListingsInput) -> dict[str, Any]:
    query: dict[str, Any] = {
        "location": inp.location,
        "transaction": inp.transaction,
    }
    if inp.min_price is not None:
        query["minPrice"] = inp.min_price
    if inp.max_price is not None:
        query["maxPrice"] = inp.max_price
    if inp.min_beds is not None:
        query["minBeds"] = inp.min_beds
    if inp.max_beds is not None:
        query["maxBeds"] = inp.max_beds

    return {
        "queries": [query],
        "maxPagesPerQuery": inp.max_pages,
        "hydrateDetails": inp.hydrate_details,
    }


def _map_result_to_output(
    items: list[dict[str, Any]],
    run_meta: dict[str, Any] | None,
) -> SearchListingsOutput:
    from rightmove_mcp.tools import SearchListingsOutput

    listings: list[Listing] = []
    parse_errors: list[str] = []
    for row in items:
        row_source = row.get("source")
        if row_source not in (None, _SOURCE):
            parse_errors.append(f"skipping {_SOURCE}-actor item with source={row_source!r}")
            continue
        try:
            listings.append(Listing.model_validate(row))
        except ValidationError as exc:
            parse_errors.append(str(exc))

    pages_fetched = 0
    detail_pages_fetched = 0
    run_errors: list[str] = []
    if run_meta is not None:
        totals = run_meta.get("totals")
        if isinstance(totals, dict):
            pages_fetched = int(totals.get("pages_fetched") or 0)
            detail_pages_fetched = int(totals.get("detail_pages_fetched") or 0)
            if int(totals.get("errors") or 0):
                meta_errors = run_meta.get("errors")
                if isinstance(meta_errors, list):
                    run_errors.extend(str(e) for e in meta_errors if e)

    return SearchListingsOutput(
        listings=listings,
        pages_fetched=pages_fetched,
        detail_pages_fetched=detail_pages_fetched,
        errors=[*run_errors, *parse_errors],
    )


__all__ = [
    "DelegationError",
    "maybe_delegate_search_listings",
]
