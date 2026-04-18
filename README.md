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
[`Listing`](https://github.com/.../uk-property-intel/blob/main/packages/scrapers/src/uk_property_scrapers/schema.py)
schema. Prices are in **pence**, not pounds (integer, no float drift).

Rightmove notes:

- `searchLocation` uses Rightmove's textual location box (the in-site
  autocomplete). For finer geo-targeting, use the Apify actor with location
  identifiers.
- Rightmove is more aggressive than Zoopla about anti-bot; when an IP is
  flagged you'll see `BlockedError` propagated as an MCP error. Set
  `PROXY_URL` to route through a residential proxy.

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

## Environment variables

| Variable | Meaning |
| --- | --- |
| `PROXY_URL` | Optional HTTP proxy (e.g. Apify residential) |
| `DISCORD_WEBHOOK_URL` | Optional alert webhook for anti-bot escalations |
| `CRAWLER_RATE_PER_SEC` | Global requests-per-second ceiling |

## Example prompt

> *"Use the rightmove tools to list all 3-bed houses to rent in central
> Cambridge under £2,500/month, then summarize how rent per bedroom varies
> by postcode."*
