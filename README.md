# rightmove-mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server that lets
AI assistants (Claude Desktop, Cursor, ChatGPT with MCP) query Rightmove UK
property listings directly.

## Tools exposed

| Tool | Purpose |
| --- | --- |
| `search_listings` | Search Rightmove by location, transaction, price/bed range; returns up to N normalized listings. |
| `get_listing` | Fetch a single Rightmove property detail page and return the canonical `Listing`. |
| `extract_listing_urls` | Parse a Rightmove HTML page (or URL) and return a list of listing detail URLs. |

All tool outputs use the shared
[`Listing`](https://github.com/kubilayyavuz/uk-property-intel/blob/main/packages/scrapers/src/uk_property_scrapers/schema.py)
schema from [`uk-property-scrapers`](https://pypi.org/project/uk-property-scrapers/).
Prices are in **pence**, not pounds (integer, no float drift).

Rightmove notes:

- `searchLocation` uses Rightmove's textual location box (the in-site
  autocomplete). For finer geo-targeting, delegate to the hosted
  `rightmove-listings` Apify actor (see below) which accepts location
  identifiers.
- Rightmove is more aggressive than Zoopla about anti-bot; when an IP is
  flagged you'll see `BlockedError` propagated as an MCP error. Either set
  `PROXY_URL` to route through a residential proxy, or enable Apify
  delegation so the hosted actor handles rotation for you.

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

If `APIFY_API_TOKEN` is set (and `UK_PROPERTY_APIFY_MODE` is not `off`), the
MCP's `search_listings` tool transparently delegates to the hosted
`rightmove-listings` Apify actor rather than running a local crawl. This lets
you sidestep home-IP blocks without running a proxy or browser locally. Set
`UK_PROPERTY_APIFY_MODE=force` to make delegation mandatory (raises
`DelegationError` instead of falling back) or `off` to disable even when a
token is present. `get_listing` and `extract_listing_urls` always run locally.

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
| `PROXY_URL` | Optional HTTP proxy (e.g. Apify residential) for the local `SimpleCrawler` |
| `DISCORD_WEBHOOK_URL` | Optional alert webhook for anti-bot escalations |
| `CRAWLER_RATE_PER_SEC` | Global requests-per-second ceiling for the local crawler |

## Example prompt

> *"Use the rightmove tools to list all 3-bed houses to rent in central
> Cambridge under £2,500/month, then summarize how rent per bedroom varies
> by postcode."*
