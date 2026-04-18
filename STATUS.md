# Status — `rightmove-mcp`

Thin [Model Context Protocol](https://modelcontextprotocol.io) server exposing
the Rightmove HTML parsers from `uk-property-intel` as AI tools.

## Headline numbers

| Metric | Value |
|---|---|
| MCP tools exposed | 3 (`search_listings`, `get_listing`, `extract_listing_urls`) |
| Tests (mocked) | 9 green |
| Core parser | Lives in `uk-property-intel/packages/scrapers/src/uk_property_scrapers/rightmove` |

## Status

- [x] MCP server scaffold (`mcp.server` + stdio transport)
- [x] 3 tools defined + typed inputs via `mcp.Tool`
- [x] Search results + detail page parsing
- [x] URL extractor
- [x] `BlockedError` → MCP error propagation for Cloudflare 403s
- [x] Tests stub the `httpx` layer via `respx`
- [x] README with Claude Desktop / Cursor config

## What's left

- [ ] Switch from `[tool.uv.sources]` path dep on `uk-property-intel/packages/scrapers` → PyPI pin once that package releases
- [ ] Document residential-proxy (`PROXY_URL`) setup more prominently
- [ ] Publish to PyPI under `kubilay-yavuz` namespace
- [ ] Fixture-drift alarm: auto-recrawl + retest against live Rightmove HTML on a schedule
