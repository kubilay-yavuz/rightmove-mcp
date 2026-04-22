# uk-property-apify-client

Typed delegation client for the UK-property hosted Apify actor fleet. Lets MCP
servers, the agent, or any other external caller route a tool call through
the hosted moat actors (`zoopla-listings`, `rightmove-listings`,
`onthemarket-listings`, `epc-ct-ppd-unified`, `planning-aggregator`,
`landlord-network`) instead of running a local `SimpleCrawler` — the core
of the free-tier-vs-paid-tier funnel described in the project plan.

The package is deliberately small. It owns three things:

1. **Actor registry** (`ActorKey` + `KNOWN_ACTOR_SLUGS`) — the canonical list
   of every actor we ship.
2. **Env-driven configuration** (`ApifyDelegation.resolve(actor_key)`) — maps
   `APIFY_API_TOKEN` / `APIFY_USERNAME` / per-actor overrides /
   `UK_PROPERTY_APIFY_MODE` into a ready-to-use `ApifyDelegation`. Returns
   `None` when delegation is off — consumers treat that as "fall back to the
   local crawler / client".
3. **Run execution** (`ApifyDelegation.call(actor_input)`) — fires the run
   via [`apify-client`](https://docs.apify.com/sdk/python/apify_client/),
   awaits completion, pulls down the full dataset + `RUN_META` + `ERRORS`
   key-value records, and returns an `ActorCallResult` that consumers can
   map into whatever output shape their surface expects.

## Quickstart

```python
from uk_property_apify_client import ApifyDelegation

delegation = ApifyDelegation.resolve("rightmove-listings")
if delegation is not None:
    result = await delegation.call(
        {
            "queries": [
                {"location": "Cambridge", "transaction": "sale", "minBeds": 2}
            ],
            "maxPagesPerQuery": 2,
        }
    )
    for listing in result.items:
        print(listing["title"], listing["price"])
    if result.run_meta is not None:
        print("hit pages:", result.run_meta["totals"]["pages_fetched"])
```

The `resolve` step is env-only; there's no `.env`-file loading. Set:

| Env var | Purpose |
|---|---|
| `APIFY_API_TOKEN` | Required. Your Apify personal or organisation token. |
| `APIFY_USERNAME` | Username that owns the actors. Combined with the actor key to form the full `username~slug` identifier. |
| `APIFY_ACTOR_<KEY>` | Per-actor override, e.g. `APIFY_ACTOR_ZOOPLA_LISTINGS=me~my-zoopla-fork`. Takes precedence over `APIFY_USERNAME`. |
| `UK_PROPERTY_APIFY_MODE` | `auto` (default, delegate when a token is set), `off` (never delegate), or `force` (raise `DelegationError` if credentials are missing). |

## Why a whole package?

We keep it isolated for three reasons:

- The MCPs (`zoopla-mcp` / `rightmove-mcp` / `onthemarket-mcp`) don't want to
  pull `apify-client` into their public-tier dep tree just to offer
  delegation as an opt-in escalation path. Wiring delegation through a
  dedicated package keeps that dep explicit in each consumer's
  `pyproject.toml`.
- The agent tools in `uk-property-intel/packages/agent` want to delegate to
  A5 / A7 without reinventing the config/resolve plumbing. A single shared
  helper avoids three slightly-different `_maybe_delegate` helpers drifting
  apart.
- Any future external consumer (a future MCP, a third-party agent, a cron
  script) picks up the same env contract without needing to re-implement
  token/username parsing.

## Scope

- **Does**: call one hosted actor, await completion, materialise full
  dataset + `RUN_META` + `ERRORS`, surface errors structurally.
- **Does not**: stream dataset pages, retry failed runs, cache previous
  results, or express multi-actor pipelines. Those are consumer concerns.
