# uk-property-scrapers

Pure-Python parsers and canonical Pydantic schemas for UK property listing portals. **No browser. No network.** Takes HTML in, returns Pydantic models out.

This design makes parsers fast, testable against saved fixtures, and re-usable across:

- The `zoopla-mcp` / `rightmove-mcp` / `onthemarket-mcp` OSS MCP servers.
- The `uk-property-apify` production Apify actor fleet.
- The `uk-property-agent` OSS natural-language agent.

## Install

```bash
pip install uk-property-scrapers
```

## Usage

### Search + detail pages

```python
from uk_property_scrapers.zoopla import parse_search_results, parse_detail_page

for listing in parse_search_results(search_html):
    # Prices live in `sale_price` or `rent_price` depending on `transaction_type`.
    price = listing.sale_price or listing.rent_price
    print(listing.source_url, price.raw if price else "POA", listing.address.raw)

detail = parse_detail_page(detail_html, source_url="https://www.zoopla.co.uk/for-sale/details/72228361/")
assert detail is not None
print(detail.coords, len(detail.image_urls))
```

### Agent-branch pages

Each portal has a `.agent` module with two parsers: `parse_branch_page` (returns an `AgentProfile`) and `parse_branch_stock` (returns the current listings on the branch page).

```python
from uk_property_scrapers.zoopla.agent import parse_branch_page, parse_branch_stock

profile = parse_branch_page(branch_html, source_url=branch_url)
print(profile.name, profile.phone, profile.email)
for member in profile.team:          # list[BranchTeamMember]
    print(member.name, member.role)

stock = parse_branch_stock(branch_html, source_url=branch_url)
print(len(stock), "listings on the branch page")
```

The same API is available from `uk_property_scrapers.rightmove.agent` and `uk_property_scrapers.onthemarket.agent`.

## Canonical schemas

All parsers return objects from `uk_property_scrapers.schema`:

### Listings

- `Listing` — one listing in a uniform shape across all three portals. Key fields: `source` / `source_id` / `source_url`, `transaction_type`, `sale_price` / `rent_price` (only one populated), `address`, `coords`, `bedrooms` / `bathrooms`, `image_urls`, `features`, `agent`.
- Supporting types: `Source`, `TransactionType`, `PropertyType`, `Tenure`, `ListingFeature`, `LatLng`, `Address`, `Price` / `RentPrice` (pence), `Agent`, `Image`, `EnergyRating`, `PropertyTimelineEvent`, `LeaseTerms`, `BroadbandSpeed`, `MobileSignal`, `MaterialInformation`.

### Auction lots

`AuctionLot`, `AuctionHouse`, `AuctionSaleMethod`, `AuctionLotStatus`, `AuctionGuidePrice` — one unified schema across Allsop, Auction House, Savills, and iamsold.

### Agent branches

`AgentProfile` (name, phone, email, website, address, coords, logo, opening hours, services, typed `team: list[BranchTeamMember]`, optional `stock: list[Listing]` and `stock_summary: AgentStockSummary`).

### Inquiry actions

Typed request / result models used by the MCP action tools and the `uk_property_apify_shared.actions` orchestrator:

- `BuyerIdentity`, `BuyerInterest`, `BuyerPosition`, `BuyerMortgageStatus`.
- `InquiryRequest`, `ViewingRequest`, `FreeValuationRequest` — inputs.
- `InquiryChannel`, `InquiryOutcome`, `InquiryResult` — outputs.

All action schemas carry built-in safety defaults (`dry_run=True`, `consent_to_portal_tcs=False`, `opt_in=False`) and raise if invoked without consent.

### Delta / change-tracking

Schemas for the per-portal delta pipelines in `uk_property_apify_shared.delta`:

- `ListingSnapshot` — the watch-relevant subset of a `Listing`, serialised to a deterministic fingerprint via `fingerprint_payload`.
- `SnapshotDiff` — structural diff between two `ListingSnapshot`s.
- `ListingChangeKind` / `ListingChangeEvent` — typed events (`NEW`, `PRICE_REDUCED`, `PRICE_INCREASED`, `PHOTOS_CHANGED`, `STATUS_CHANGED`, `BACK_ON_MARKET`, `REMOVED`, …).

## Architecture

Parsers are deliberately decoupled from crawlers. The caller (MCP, Apify actor, or agent) owns the browser / proxy / retry logic. The parser's job is to turn a given `str` of HTML into a list of typed models with no IO.

## Supported sources

| Source        | Search | Detail | Agent branch | Branch stock | Status  |
|---------------|--------|--------|--------------|--------------|---------|
| Zoopla        | yes    | yes    | yes          | yes          | stable  |
| Rightmove     | yes    | yes    | yes          | yes          | stable  |
| OnTheMarket   | yes    | yes    | yes          | yes          | stable  |
| Allsop / Auction House / Savills / iamsold | yes | yes | — | — | stable |

All three portal parsers are exercised by the shared production crawler (`uk_property_scrapers.crawler`) which provides a two-tier HTTP + Playwright fallback, domain-aware rate limiting, curl-cffi TLS impersonation, Discord maintenance alerts, and anti-bot detection.

## Optional extras

```bash
pip install "uk-property-scrapers[crawler]"    # full production crawler
pip install "uk-property-scrapers[playwright]" # browser fallback only
```
