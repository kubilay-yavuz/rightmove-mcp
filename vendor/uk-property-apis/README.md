# uk-property-apis

Typed async Python clients for every UK government / public API that matters for property intelligence. Designed to be composed inside Apify actors, MCP servers, or the LangGraph agent.

## Clients

### Statutory data

| Client | Source | Auth |
| --- | --- | --- |
| `EPCClient` | [Energy Performance Certificates](https://epc.opendatacommunities.org/) | Email + token (Basic) |
| `LandRegistryClient` | [HMLR Price Paid Data](https://landregistry.data.gov.uk/) | None |
| `CompaniesHouseClient` | [Companies House REST](https://developer.company-information.service.gov.uk/) | API key |
| `PlanningClient` | [planning.data.gov.uk](https://www.planning.data.gov.uk/) | None |
| `HTMLPlanningClient` / `ArcGISPlanningClient` | IDOX direct (HTML scrape + ArcGIS feature services) | None |
| `VOAClient` | Valuation Office Agency council-tax bands | None |

### Geography & demographics

| Client | Source | Auth |
| --- | --- | --- |
| `PostcodesClient` | [postcodes.io](https://postcodes.io/) | None |
| `ONSClient` | [ONS developer APIs](https://developer.ons.gov.uk/) | None |
| `NomisClient` | [ONS Nomis](https://www.nomisweb.co.uk/api/v01/help) | None |
| `ElevationClient` | Open-Elevation / UK DTM | None |
| `CoastalErosionClient` | Environment Agency coastal erosion layer | None |
| `NaturalEnglandClient` | Natural England ArcGIS feature services | None |

### Environment & risk

| Client | Source | Auth |
| --- | --- | --- |
| `FloodClient` | [Environment Agency Flood](https://environment.data.gov.uk/flood-monitoring/doc/reference) | None |
| `DefraAirQualityClient` | DEFRA AURN air-quality monitoring | None |
| `BGSClient` | British Geological Survey (landslides, subsidence, ground dissolution) | None |
| `BGSRadonClient` | BGS / UKHSA radon potential | None |

### Crime & procurement

| Client | Source | Auth |
| --- | --- | --- |
| `PoliceClient` | [data.police.uk](https://data.police.uk/) | None |
| `ContractsFinderClient` | Cabinet Office Contracts Finder | None |
| `FTSClient` | Find a Tender Service | None |

### Auction-house catalogues

| Client | Source | Auth |
| --- | --- | --- |
| `AllsopClient` | [allsop.co.uk](https://www.allsop.co.uk/) | None |
| `AuctionHouseClient` | [auctionhouse.co.uk](https://www.auctionhouse.co.uk/) | None |
| `SavillsAuctionsClient` | [savills.com/auctions](https://auctions.savills.com/) | None |
| `IamsoldClient` | [iamsold.co.uk](https://iam-sold.co.uk/) | None |

## Design principles

1. **Async-first** — every client is `httpx.AsyncClient`-based so actors can parallelise requests with minimal overhead.
2. **Typed responses** — Pydantic v2 models for every endpoint. No `dict[str, Any]` leaks across the public API.
3. **Polite by default** — built-in rate-limiting and exponential backoff via `tenacity`; clients accept an optional `semaphore` for cross-request concurrency control.
4. **Deterministic errors** — all transport and parsing failures surface as typed exceptions (`RateLimitError`, `AuthError`, `NotFoundError`, `TransportError`, `ParseError`).
5. **Shared base** — every client inherits `BaseAPIClient` (auth headers, retry policy, JSON/XML decoders, tracing hooks) so adding a new source is ~50 lines of adapter code plus Pydantic models.
