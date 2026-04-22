# rightmove-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets AI assistants (Claude Desktop, Cursor, ChatGPT with MCP) query Rightmove UK property listings directly — and, with explicit consent, send real inquiries / viewing requests / free-valuation requests to Rightmove agents on the user's behalf.

## Tools exposed (13)

Every output uses Pydantic models from [`uk-property-scrapers`](https://pypi.org/project/uk-property-scrapers/). Prices are in **pence** (integer — no float drift).

### Listings (3)

| Tool | Purpose |
| --- | --- |
| `search_listings` | Search Rightmove by location, transaction, price / bed range; returns up to N normalized listings. Dual-mode — local `SimpleCrawler` by default, hosted Apify `rightmove-listings` actor when `APIFY_API_TOKEN` is set. |
| `get_listing` | Fetch a single Rightmove property detail page and return the canonical `Listing`. |
| `extract_listing_urls` | Parse a Rightmove HTML page (or URL) and return a list of listing detail URLs. |

### Agent branch (2)

| Tool | Purpose |
| --- | --- |
| `get_agent_profile` | Fetch a Rightmove estate-agent branch page and return an `AgentProfile` — branch name, normalised phone / email / website, address, coords, logo, opening hours, service list, typed `team: list[BranchTeamMember]`. Optional `include_stock=True` also appends the branch's current listings. URL must be `https://www.rightmove.co.uk/estate-agents/agent/<slug>-<id>.html`. |
| `list_agent_stock` | Return the branch's current `Listing[]` (search-card shape) — the stock side of the branch page, independent of `get_agent_profile`. |

### Actions (3) — safety defaults

All three action tools default to **dry run with no consent and no marketing opt-in**. Nothing is ever submitted unless the caller explicitly flips every gate:

- `dry_run=True` (default) → fills every field and screenshots the filled form, but never clicks submit.
- `consent_to_portal_tcs=False` (default) → any non-dry-run call raises `ValueError`. The MCP refuses to submit without explicit caller consent to Rightmove's terms and conditions.
- `opt_in=False` (default) → marketing-opt-in boxes are left **unticked**. Rightmove's inquiry form defaults its own marketing checkbox to ON; the submitter explicitly unticks it unless the caller sets `opt_in=True`.

| Tool | Purpose |
| --- | --- |
| `send_inquiry` | Contact the agent for a Rightmove listing. Inputs: `listing_url`, `first_name`, `last_name`, `email`, `phone`, `message`, `interest`, `position`, `mortgage_status`, plus the three safety flags. |
| `request_viewing` | Request a property viewing with optional `preferred_datetime`. |
| `request_free_valuation` | Request a free valuation for a caller's own property (not for a Rightmove listing). |

### Delta pipeline (5)

Track listing changes over time. First call on a listing snapshots it; every subsequent call diffs against the stored snapshot and emits `ListingChangeEvent`s (e.g. `PRICE_REDUCED`, `PHOTOS_CHANGED`, `BACK_ON_MARKET`). Events persist to SQLite and are queryable via the three firehose tools.

| Tool | Purpose |
| --- | --- |
| `watch_listing` | Fetch a single listing, snapshot the watch-relevant fields, emit any change events vs the previous snapshot. |
| `watch_query` | Fetch a full search result page and ingest every listing into the snapshot store (cron-friendly bulk feeder). |
| `reductions_firehose` | Return recent `PRICE_REDUCED` events. |
| `new_listings_firehose` | Return recent `NEW` events (listings the store has never seen). |
| `back_on_market` | Return recent `BACK_ON_MARKET` events (SOLD_STC / UNDER_OFFER → available). |

Default store path: `~/.uk-property-mcp/rightmove.sqlite`. Override per-call via the tool's `store_path` argument or globally via `UK_PROPERTY_DELTA_STORE_PATH`.

## Rightmove notes

- `search_listings` uses Rightmove's textual location box (the in-site autocomplete). For finer geo-targeting, delegate to the hosted `rightmove-listings` Apify actor (see below) which accepts location identifiers.
- Rightmove is more aggressive than OnTheMarket about anti-bot; when an IP is flagged you'll see `BlockedError` propagated as an MCP error. Either set `PROXY_URL` to route through a residential proxy, or enable Apify delegation so the hosted actor handles rotation for you.
- The agent tools (`get_agent_profile`, `list_agent_stock`) and action tools (`send_inquiry`, `request_viewing`, `request_free_valuation`) always run locally — they don't have an Apify fallback.

## Install

```bash
pipx install rightmove-mcp
uvx --from rightmove-mcp rightmove-mcp
```

### Claude Desktop config

```json
{
  "mcpServers": {
    "rightmove": {
      "command": "uvx",
      "args": ["--from", "rightmove-mcp", "rightmove-mcp"]
    }
  }
}
```

## Apify delegation

If `APIFY_API_TOKEN` is set (and `UK_PROPERTY_APIFY_MODE` is not `off`), the MCP's `search_listings` tool transparently delegates to the hosted `rightmove-listings` Apify actor rather than running a local crawl. This lets you sidestep home-IP blocks without running a proxy or browser locally. Set `UK_PROPERTY_APIFY_MODE=force` to make delegation mandatory (raises `DelegationError` instead of falling back) or `off` to disable even when a token is present. `get_listing`, `extract_listing_urls`, and the agent / action / delta tools always run locally.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `APIFY_API_TOKEN` | Apify personal token — enables delegation of `search_listings` to the hosted `rightmove-listings` actor |
| `APIFY_USERNAME` | Default actor owner (combined with the actor slug as `username~rightmove-listings`, unless `APIFY_ACTOR_RIGHTMOVE_LISTINGS` overrides it) |
| `UK_PROPERTY_APIFY_MODE` | `auto` (default, delegate if token is set), `on` (same as auto), `force` (error if unconfigured), `off` (never delegate) |
| `APIFY_ACTOR_RIGHTMOVE_LISTINGS` | Override — fully-qualified `username~slug` identifier (takes precedence over `APIFY_USERNAME`) |
| `UK_PROPERTY_APIFY_BUILD` | Optional actor build tag (`latest` / `beta` / version) |
| `UK_PROPERTY_APIFY_TIMEOUT_S` | Wall-clock cap for the run (seconds, default `600`) |
| `UK_PROPERTY_APIFY_MEMORY_MB` | Memory cap for the run (MB, default `1024`) |
| `UK_PROPERTY_DELTA_STORE_PATH` | Path to the SQLite snapshot store (default `~/.uk-property-mcp/rightmove.sqlite`). Per-call `store_path` overrides this. |
| `PROXY_URL` | Optional HTTP proxy (e.g. Apify residential) for the local `SimpleCrawler` |
| `DISCORD_WEBHOOK_URL` | Optional alert webhook for anti-bot escalations |
| `CRAWLER_RATE_PER_SEC` | Global requests-per-second ceiling for the local crawler |

## Example prompts

> *"Use the rightmove tools to list all 3-bed houses to rent in central Cambridge under £2,500/month, then summarize how rent per bedroom varies by postcode."*

> *"Start watching every listing on this Rightmove search page, and ping me with any price reductions via `reductions_firehose` next week."*

> *"Here's a Rightmove branch URL: https://www.rightmove.co.uk/estate-agents/agent/Savills/Cambridge-12345.html — fetch the profile and list current stock."*
