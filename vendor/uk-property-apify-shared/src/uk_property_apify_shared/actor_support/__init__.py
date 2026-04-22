"""Apify Actor run-loops.

Two generic run-loops live here:

* :func:`run_listings_actor` — scraping/crawler-based listings actors
  (zoopla / rightmove / onthemarket). Wraps the private moat
  :class:`Crawler` + the public :mod:`uk_property_listings` pagination
  helpers.
* :func:`run_api_actor` — API-only actors (A4 ``epc-ct-ppd-unified``,
  A5 ``planning-aggregator``, A7 ``landlord-network``). Pure
  fan-out-then-emit flow with no crawler moat; callers provide a
  small :class:`ApiActorHooks` object describing input parsing, units
  of work, and per-unit execution.
"""

from __future__ import annotations

from uk_property_apify_shared.actor_support.api_resilience import (
    API_RESILIENCE_SCHEMA_PROPERTIES,
    ApiResilienceInput,
)
from uk_property_apify_shared.actor_support.input import (
    ActorInput,
    ProxyInput,
    QueryInput,
    UrlInput,
    parse_input,
)
from uk_property_apify_shared.actor_support.run_api import (
    ApiActorHooks,
    RunContext,
    run_api_actor,
)
from uk_property_apify_shared.actor_support.run_hydrate import (
    DetectSourceFn,
    HydrateActorHooks,
    HydrateInput,
    parse_hydrate_input,
    run_hydrate_actor,
)
from uk_property_apify_shared.actor_support.run_listings import (
    ListingsActorHooks,
    run_listings_actor,
)

__all__ = [
    "API_RESILIENCE_SCHEMA_PROPERTIES",
    "ActorInput",
    "ApiActorHooks",
    "ApiResilienceInput",
    "DetectSourceFn",
    "HydrateActorHooks",
    "HydrateInput",
    "ListingsActorHooks",
    "ProxyInput",
    "QueryInput",
    "RunContext",
    "UrlInput",
    "parse_hydrate_input",
    "parse_input",
    "run_api_actor",
    "run_hydrate_actor",
    "run_listings_actor",
]
