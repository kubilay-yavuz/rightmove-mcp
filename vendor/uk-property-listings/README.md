# uk-property-listings

Public listings-search utility package for UK property portals.

## What's in here

Everything you need to turn a structured query into a list of canonical `Listing`
records *without* any of the production "reliability moat" (curl-cffi TLS
impersonation, Playwright stealth, anti-bot classification, residential
proxies). Those live in the private `uk-property-apify` repo and power the
hosted Apify actors.

- `SearchQuery`, `TransactionKind`, `CrawlReport` — pure data structures.
- `build_zoopla_search_url`, `build_rightmove_search_url`,
  `build_onthemarket_search_url` — pure URL builders (pagination-aware).
- `FetchResult`, `FetcherError`, `CrawlerProtocol` — minimal interfaces.
- `SimpleCrawler` — httpx-only crawler suitable for local development, the
  three Z/RM/OTM MCPs, and the OSS `uk-property-agent`. Will get blocked by
  Cloudflare on Zoopla fairly often; that's the funnel into the paid
  Apify-hosted tier.
- `crawl_zoopla_search`, `crawl_rightmove_search`, `crawl_onthemarket_search` —
  pagination + parse loops typed against `CrawlerProtocol`, so they work with
  both the public `SimpleCrawler` and the private production `Crawler`.

## When to use this package

- **You're writing a public Z/RM/OTM MCP** — use `SimpleCrawler` +
  `crawl_*_search`.
- **You're writing the OSS agent** — same.
- **You're writing an Apify actor** — use `crawl_*_search` for pagination, but
  pass in the private `Crawler` from `uk_property_apify_shared.crawler` as the
  crawler argument. That's where the moat is.

## License

MIT. The "reliability moat" (anti-bot, proxy integration, Playwright stealth,
run-loop for Apify actors) is deliberately *not* in this package.
