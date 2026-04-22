"""Input validation + defaults for listings-style Apify actors."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QueryInput(BaseModel):
    """One row of the ``queries`` array."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    location: str = Field(..., min_length=1)
    transaction: Literal["sale", "rent"] = "sale"
    min_price: int | None = Field(None, ge=0, alias="minPrice")
    max_price: int | None = Field(None, ge=0, alias="maxPrice")
    min_beds: int | None = Field(None, ge=0, le=20, alias="minBeds")
    max_beds: int | None = Field(None, ge=0, le=20, alias="maxBeds")

    @field_validator("location", mode="before")
    @classmethod
    def _strip_location(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("location must not be empty")
            return stripped
        return value

    @model_validator(mode="after")
    def _check_price_range(self) -> QueryInput:
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError(
                "minPrice must be <= maxPrice "
                f"(got minPrice={self.min_price}, maxPrice={self.max_price})"
            )
        if (
            self.min_beds is not None
            and self.max_beds is not None
            and self.min_beds > self.max_beds
        ):
            raise ValueError(
                "minBeds must be <= maxBeds "
                f"(got minBeds={self.min_beds}, maxBeds={self.max_beds})"
            )
        return self

    def dedupe_key(self) -> str:
        """Stable identity key for dedupe across a single run.

        Two queries that differ only in case / whitespace on ``location``
        should collapse. Ordering of optional fields doesn't matter since
        we emit them in a fixed sequence.
        """

        return "|".join(
            [
                self.location.strip().casefold(),
                self.transaction,
                str(self.min_price) if self.min_price is not None else "",
                str(self.max_price) if self.max_price is not None else "",
                str(self.min_beds) if self.min_beds is not None else "",
                str(self.max_beds) if self.max_beds is not None else "",
            ]
        )


class UrlInput(BaseModel):
    """One row of the ``listingUrls`` array.

    URL-list mode bypasses the per-site search pagination: the crawler
    fetches each URL directly, applies the per-site detail parser, and
    emits one :class:`~uk_property_scrapers.schema.Listing` per URL.
    ``transaction`` defaults to ``sale``; callers with a rental URL
    should set it explicitly so the parser picks the right price
    extraction path.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    url: str = Field(..., min_length=1)
    transaction: Literal["sale", "rent"] = "sale"

    @field_validator("url", mode="before")
    @classmethod
    def _strip_url(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("url must not be empty")
            return stripped
        return value

    @field_validator("url")
    @classmethod
    def _must_look_like_http(cls, value: str) -> str:
        lowered = value.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            raise ValueError(
                "url must be an absolute http(s) URL (got: "
                f"{value[:60]!r})"
            )
        return value

    def dedupe_key(self) -> str:
        """Casefolded URL used to collapse obvious duplicates."""

        return f"{self.url.strip().casefold()}|{self.transaction}"


class ProxyInput(BaseModel):
    """Apify proxy configuration object as provided by the platform."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    use_apify_proxy: bool = Field(True, alias="useApifyProxy")
    apify_proxy_groups: list[str] | None = Field(None, alias="apifyProxyGroups")


class ActorInput(BaseModel):
    """Top-level input schema shared by all listings actors."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    queries: list[QueryInput] = Field(default_factory=list)
    listing_urls: list[UrlInput] = Field(
        default_factory=list,
        alias="listingUrls",
        description=(
            "Optional list of specific listing URLs to fetch + parse. "
            "When supplied alongside queries, the actor runs queries "
            "first then fetches the URL list. At least one of "
            "``queries`` or ``listingUrls`` must be non-empty."
        ),
    )
    max_pages_per_query: int = Field(3, ge=1, le=40, alias="maxPagesPerQuery")
    hydrate_details: bool = Field(False, alias="hydrateDetails")
    proxy_configuration: ProxyInput = Field(
        default_factory=lambda: ProxyInput(
            use_apify_proxy=True, apify_proxy_groups=None
        ),
        alias="proxyConfiguration",
    )
    rate_per_second: float = Field(0.5, ge=0.05, le=5.0, alias="ratePerSecond")
    discord_webhook_url: str | None = Field(None, alias="discordWebhookUrl")

    max_attempts_per_query: int = Field(
        1,
        ge=1,
        le=5,
        alias="maxAttemptsPerQuery",
        description=(
            "Retry the whole query up to this many times on a hard "
            "failure (WAF block, 5xx, timeout). Between attempts the "
            "proxy URL is rotated and a capped exponential backoff is "
            "applied. Default of 1 keeps the existing behaviour."
        ),
    )
    query_timeout_s: float | None = Field(
        None,
        gt=0,
        le=14_400,
        alias="queryTimeoutSec",
        description=(
            "Hard per-query time budget in seconds. ``None`` disables "
            "the timeout and leans on the crawler's per-request "
            "timeouts instead."
        ),
    )
    query_concurrency: int = Field(
        1,
        ge=1,
        le=4,
        alias="queryConcurrency",
        description=(
            "Run up to this many queries in parallel. The crawler "
            "rate-limiter still throttles per-domain, so increasing "
            "concurrency mostly helps when queries target different "
            "locations with cheap parsing. Default 1 preserves legacy "
            "sequential behaviour."
        ),
    )
    dedupe_queries: bool = Field(
        True,
        alias="dedupeQueries",
        description=(
            "Collapse duplicate queries (case-insensitive location + "
            "identical filters) before crawling. On by default."
        ),
    )
    rotate_proxy_per_attempt: bool = Field(
        True,
        alias="rotateProxyPerAttempt",
        description=(
            "Mint a fresh Apify proxy URL (= exit IP) before every "
            "query attempt, not just once per run. Dramatically "
            "reduces WAF block rates on multi-query runs."
        ),
    )
    backoff_initial_s: float = Field(
        2.0,
        ge=0.1,
        le=60.0,
        alias="backoffInitialSec",
        description=(
            "Initial delay between query retries. Grows exponentially "
            "capped at ``backoffMaxSec``."
        ),
    )
    backoff_max_s: float = Field(
        60.0,
        ge=1.0,
        le=600.0,
        alias="backoffMaxSec",
        description=(
            "Cap on the exponential-backoff delay between query retries."
        ),
    )

    @model_validator(mode="after")
    def _coherent(self) -> ActorInput:
        if self.backoff_max_s < self.backoff_initial_s:
            raise ValueError(
                f"backoffMaxSec ({self.backoff_max_s}) must be >= "
                f"backoffInitialSec ({self.backoff_initial_s})"
            )
        if not self.queries and not self.listing_urls:
            raise ValueError(
                "At least one of 'queries' or 'listingUrls' must be "
                "non-empty. Supply search queries, a list of listing "
                "URLs, or both."
            )
        return self

    def deduped_queries(self) -> list[QueryInput]:
        """Return queries with duplicates collapsed.

        When :attr:`dedupe_queries` is ``False`` this returns the list
        unchanged (useful for intentional re-crawling). First occurrence
        wins so caller-supplied order is respected for the non-duplicate
        pass.
        """

        if not self.dedupe_queries:
            return list(self.queries)
        seen: set[str] = set()
        out: list[QueryInput] = []
        for q in self.queries:
            key = q.dedupe_key()
            if key in seen:
                continue
            seen.add(key)
            out.append(q)
        return out

    def deduped_listing_urls(self) -> list[UrlInput]:
        """Return listing URLs with duplicates collapsed.

        Respects :attr:`dedupe_queries` (same switch for both paths —
        URL-list mode is effectively a specialised query form).
        """

        if not self.dedupe_queries:
            return list(self.listing_urls)
        seen: set[str] = set()
        out: list[UrlInput] = []
        for entry in self.listing_urls:
            key = entry.dedupe_key()
            if key in seen:
                continue
            seen.add(key)
            out.append(entry)
        return out


def parse_input(raw: dict[str, Any] | None) -> ActorInput:
    """Validate + normalize a raw ``Actor.get_input()`` dict."""
    return ActorInput.model_validate(raw or {})
