# uk-property-apify-shared

**Private.** Reliability-moat infrastructure shared by the UK property Apify actors **and** the three MCP servers. This is the paywall: everything that separates "you can scrape a few pages on your laptop" from "reliably scrape Zoopla / Rightmove / OTM at scale, actually submit inquiries, and track every listing change over time".

## Subpackages

### `uk_property_apify_shared.crawler` — anti-bot moat

- `CurlCffiTransport` — TLS + HTTP/2 fingerprint impersonation (Chrome 124).
- `HttpFetcher` — tier-1 fetcher with rate limiting, retries, session warming.
- `BrowserFetcher` — tier-2 Playwright + stealth escalation.
- `antibot.classify_response` — Cloudflare / Akamai / DataDome / PerimeterX / reCAPTCHA / login-wall detection.
- `DomainRateLimiter` — sliding-window per-host rate limiter.
- `DiscordAlertSink` — escalation notices.
- `CrawlerConfig` — knobs for the above.

### `uk_property_apify_shared.actor_support` — Apify run-loop

Generic Apify run-loop used by the Zoopla / Rightmove / OnTheMarket listings actors:

- `ActorInput` / `parse_input` — input schema validation.
- `run_listings_actor(hooks)` — orchestrates `Actor.get_input` → `Actor.push_data` using the production `Crawler` and a site-specific pagination helper from `uk_property_listings`.

### `uk_property_apify_shared.actions` — portal action tools

Programmatic form submission for the MCP `send_inquiry`, `request_viewing`, `request_free_valuation` tools. Safety-defaults-first: nothing is submitted without three explicit caller gates.

- `FormSubmitter` — Playwright-based form filler. Default `dry_run=True` screenshots the filled form but never clicks submit.
- `PortalActionBundle` — per-portal dataclass of CSS selectors, URL patterns, and opt-in checkbox wiring for Zoopla / Rightmove / OTM.
- `execute_inquiry` / `execute_viewing` / `execute_valuation` — orchestrators that enforce the `consent_to_portal_tcs` and `opt_in` gates before touching the submitter.
- `CaptchaSolver` protocol — `NullCaptchaSolver` (raise if challenged), `ManualCaptchaSolver` (return a pre-supplied token for tests), stubbed third-party solver adapter. Pluggable per-call.
- `uk_property_apify_shared.actions.mcp` — standardised Pydantic input / output models the three MCPs import so every portal exposes the same tool surface.

### `uk_property_apify_shared.delta` — change-tracking pipeline

Powers the MCP `watch_listing`, `watch_query`, `reductions_firehose`, `new_listings_firehose`, `back_on_market` tools.

- `fingerprint.py` — deterministic `fingerprint_payload(model: BaseModel)` hashing. Same input → same fingerprint across processes.
- `snapshot.py` — `snapshot_from_listing(listing)` projects a `Listing` into the watch-relevant subset (`ListingSnapshot`).
- `diff.py` — `compute_diff(old, new)` → `SnapshotDiff`, `compute_events(diff)` → `list[ListingChangeEvent]` (e.g. `PRICE_REDUCED`, `PHOTOS_CHANGED`, `BACK_ON_MARKET`).
- `store.py` — `SnapshotStore` async protocol.
- `sqlite_store.py` — `SqliteSnapshotStore`, the production backend. WAL mode, I/O offloaded via `asyncio.to_thread`, default path `~/.uk-property-mcp/<portal>.sqlite` (override globally via `UK_PROPERTY_DELTA_STORE_PATH`).
- `uk_property_apify_shared.delta.mcp` — helpers the three MCP servers use to wire their watch / firehose tools to the store.

## How consumers use it

### Listings actors

The three listings actors (`actors/{zoopla,rightmove,onthemarket}-listings/`) have three-line `run.py` files:

```python
from uk_property_apify_shared.actor_support import ListingsActorHooks, run_listings_actor
from uk_property_listings import crawl_zoopla_search
from zoopla_listings_actor import __version__

_HOOKS = ListingsActorHooks(source="zoopla", actor_version=__version__, crawl_fn=crawl_zoopla_search)

async def run() -> None:
    await run_listings_actor(_HOOKS)
```

### MCP action tools

Each MCP imports the portal bundle + orchestrator + input/output models and wires them into a FastMCP tool:

```python
from uk_property_apify_shared.actions import execute_inquiry
from uk_property_apify_shared.actions.portals import ZOOPLA_BUNDLE
from uk_property_apify_shared.actions.mcp import InquiryToolInput, InquiryToolOutput
```

### MCP delta tools

```python
from uk_property_apify_shared.delta import SqliteSnapshotStore, watch_listing_once
```

## Why this is private

The three public OSS MCPs and the OSS `uk-property-agent` only have access to `uk-property-listings` (`SimpleCrawler` — plain httpx, no TLS impersonation, no Playwright). That's the "free tier". Upgrading to this package — which powers the Apify-hosted actors, the MCP action tools, and the delta pipeline — is the paywall.

## License

Proprietary; **not** published to PyPI.
