"""Generic run-loop for the multi-source listings hydrate actor (A13).

:func:`run_hydrate_actor` takes a :class:`HydrateActorHooks` object that
maps a source label (``"zoopla"``, ``"rightmove"``, ``"onthemarket"``)
to its per-site ``crawl_urls_fn`` + a ``detect_source`` callable that
classifies each URL's hostname.

The runner:

1. Validates input via :class:`HydrateInput` (URL-list-only; no queries).
2. Routes each URL to a bucket keyed by ``(source, transaction)``
   using the supplied ``detect_source`` function. URLs whose hostname
   isn't recognised go to an ``unrecognized`` error bucket and never
   trigger a network call.
3. Processes each bucket with the same retry / proxy-rotation /
   per-attempt-timeout / exponential-backoff semantics as
   :func:`run_listings_actor._run_one_url_batch` — reusing the private
   helpers (``_build_crawler``, ``_mint_proxy_url``, etc.) so the two
   runners can't drift out of sync.
4. Pushes canonical listings and writes a structured ``RUN_META`` /
   ``ERRORS`` key-value-store entry with per-bucket counters.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from uk_property_apify_shared.actor_support.input import (
    ProxyInput,
    UrlInput,
)
from uk_property_apify_shared.actor_support.run_listings import (
    CrawlUrlsFn,
    _build_crawler,
    _build_proxy_configuration,
    _mint_proxy_url,
    _push_listings,
    _sleep_with_backoff,
)

if TYPE_CHECKING:
    from uk_property_listings import CrawlReport

logger = logging.getLogger("uk_property_apify_shared.actor_support")

DetectSourceFn = Callable[[str], str | None]


class HydrateInput(BaseModel):
    """Top-level input for the hydrate actor.

    The hydrate actor is URL-only: there's no ``queries`` list, no
    pagination knob, and no ``hydrate_details`` toggle (hydration is
    the entire purpose of the actor). It does share the shared
    listings actor's proxy / rate / retry / backoff knobs so operators
    can tune resilience the same way.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    listing_urls: list[UrlInput] = Field(
        default_factory=list,
        alias="listingUrls",
        description=(
            "List of Zoopla / Rightmove / OnTheMarket listing URLs. "
            "Source is auto-detected from each URL's hostname; URLs "
            "whose hostname doesn't match one of the supported portals "
            "are logged to ERRORS and skipped."
        ),
    )
    proxy_configuration: ProxyInput = Field(
        default_factory=lambda: ProxyInput(
            use_apify_proxy=True, apify_proxy_groups=None
        ),
        alias="proxyConfiguration",
    )
    rate_per_second: float = Field(0.5, ge=0.05, le=5.0, alias="ratePerSecond")
    discord_webhook_url: str | None = Field(None, alias="discordWebhookUrl")
    max_attempts_per_batch: int = Field(
        1,
        ge=1,
        le=5,
        alias="maxAttemptsPerBatch",
        description=(
            "Retry each (source, transaction) bucket up to this many "
            "times on a hard failure. Between attempts the proxy URL "
            "is rotated and a capped exponential backoff is applied."
        ),
    )
    batch_timeout_s: float | None = Field(
        None,
        gt=0,
        le=14_400,
        alias="batchTimeoutSec",
        description=(
            "Hard per-bucket time budget in seconds. ``None`` disables "
            "the timeout and leans on the crawler's per-request "
            "timeouts instead."
        ),
    )
    batch_concurrency: int = Field(
        1,
        ge=1,
        le=4,
        alias="batchConcurrency",
        description=(
            "Run up to this many (source, transaction) buckets in "
            "parallel. Buckets target different portals so concurrent "
            "buckets don't share a domain rate-limiter."
        ),
    )
    dedupe_urls: bool = Field(
        True,
        alias="dedupeUrls",
        description=(
            "Collapse duplicate URLs (case-insensitive) before "
            "fetching. On by default."
        ),
    )
    rotate_proxy_per_attempt: bool = Field(
        True,
        alias="rotateProxyPerAttempt",
        description=(
            "Mint a fresh Apify proxy URL (= exit IP) before every "
            "attempt, not just once per run."
        ),
    )
    backoff_initial_s: float = Field(
        2.0,
        ge=0.1,
        le=60.0,
        alias="backoffInitialSec",
    )
    backoff_max_s: float = Field(
        60.0,
        ge=1.0,
        le=600.0,
        alias="backoffMaxSec",
    )

    @model_validator(mode="after")
    def _coherent(self) -> HydrateInput:
        if self.backoff_max_s < self.backoff_initial_s:
            raise ValueError(
                f"backoffMaxSec ({self.backoff_max_s}) must be >= "
                f"backoffInitialSec ({self.backoff_initial_s})"
            )
        if not self.listing_urls:
            raise ValueError(
                "listingUrls must contain at least one URL. Supply one "
                "or more Zoopla / Rightmove / OnTheMarket listing URLs."
            )
        return self

    def deduped_listing_urls(self) -> list[UrlInput]:
        if not self.dedupe_urls:
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


def parse_hydrate_input(raw: dict[str, Any] | None) -> HydrateInput:
    """Validate + normalize a raw ``Actor.get_input()`` dict."""
    return HydrateInput.model_validate(raw or {})


@dataclass(frozen=True)
class HydrateActorHooks:
    """Per-deploy configuration for :func:`run_hydrate_actor`."""

    actor_version: str
    crawl_urls_by_source: dict[str, CrawlUrlsFn]
    """Map source label -> per-site ``crawl_urls_fn``.

    Expected keys match the output of ``detect_source``. The values
    must all share the shared ``CrawlUrlsFn`` signature:
    ``async def fn(crawler, urls, *, transaction) -> CrawlReport``.
    """

    detect_source: DetectSourceFn
    """Hostname -> source-label classifier. Return ``None`` for
    unrecognised hostnames so they're logged + skipped rather than
    misrouted."""


async def run_hydrate_actor(hooks: HydrateActorHooks) -> None:
    """Apify entry point for the multi-source hydrate actor."""

    try:
        from apify import Actor
    except ImportError as exc:  # pragma: no cover - runtime-only dep
        raise RuntimeError(
            "apify SDK not installed. Run with `pip install apify` or via "
            "the Apify Docker image."
        ) from exc

    async with Actor:
        raw_input = await Actor.get_input() or {}
        actor_input = parse_hydrate_input(raw_input)
        url_inputs = actor_input.deduped_listing_urls()
        duplicates_collapsed = (
            len(actor_input.listing_urls) - len(url_inputs)
        )

        buckets, unknown = _route_urls(url_inputs, hooks.detect_source)

        totals: dict[str, int] = {
            "items": 0,
            "detail_pages": 0,
            "errors": len(unknown),
            "attempts": 0,
            "batches_failed": 0,
            "urls_submitted": len(url_inputs),
            "urls_routed": sum(len(v) for v in buckets.values()),
            "urls_unrouted": len(unknown),
            "duplicates_collapsed": duplicates_collapsed,
        }
        per_batch_results: list[dict[str, Any]] = []
        error_log: list[dict[str, Any]] = []

        for entry in unknown:
            error_log.append(
                {
                    "source": "unrecognized",
                    "url": entry.url,
                    "transaction": entry.transaction,
                    "error": (
                        "hostname did not match any of the supported "
                        "portals (zoopla.co.uk / rightmove.co.uk / "
                        "onthemarket.com)"
                    ),
                    "error_type": "UnsupportedSource",
                    "attempt": 0,
                }
            )

        proxy_configuration = await _build_proxy_configuration(
            Actor,
            _ProxyShim(actor_input),
        )

        semaphore = asyncio.Semaphore(actor_input.batch_concurrency)

        bucket_keys = sorted(buckets)

        async def _process(idx: int, key: tuple[str, str]) -> None:
            source, transaction = key
            bucket_urls = buckets[key]
            async with semaphore:
                await _run_one_hydrate_batch(
                    idx=idx,
                    total=len(bucket_keys),
                    source=source,
                    transaction=transaction,
                    urls=bucket_urls,
                    actor=Actor,
                    actor_input=actor_input,
                    hooks=hooks,
                    proxy_configuration=proxy_configuration,
                    totals=totals,
                    per_batch_results=per_batch_results,
                    error_log=error_log,
                )

        await asyncio.gather(
            *(_process(i, k) for i, k in enumerate(bucket_keys)),
            return_exceptions=False,
        )

        meta = {
            "started_at": datetime.now(UTC).isoformat(),
            "actor_version": hooks.actor_version,
            "totals": totals,
            "parameters": {
                "rate_per_second": actor_input.rate_per_second,
                "batch_concurrency": actor_input.batch_concurrency,
                "max_attempts_per_batch": actor_input.max_attempts_per_batch,
                "batch_timeout_s": actor_input.batch_timeout_s,
                "rotate_proxy_per_attempt": actor_input.rotate_proxy_per_attempt,
                "dedupe_urls": actor_input.dedupe_urls,
            },
            "per_batch": per_batch_results,
        }
        await Actor.set_value("RUN_META", meta)
        if error_log:
            await Actor.set_value("ERRORS", error_log)
        logger.info("[hydrate] run complete: %s", totals)


def _route_urls(
    url_inputs: list[UrlInput],
    detect: DetectSourceFn,
) -> tuple[dict[tuple[str, str], list[UrlInput]], list[UrlInput]]:
    """Route URL inputs into ``(source, transaction)`` buckets.

    Returns a tuple ``(buckets, unknown)`` where ``unknown`` is the
    subset whose hostname did not classify. Ordering within each bucket
    is preserved from the input list so downstream output is
    deterministic.
    """

    buckets: dict[tuple[str, str], list[UrlInput]] = {}
    unknown: list[UrlInput] = []
    for entry in url_inputs:
        source = detect(entry.url)
        if source is None:
            unknown.append(entry)
            continue
        buckets.setdefault((source, entry.transaction), []).append(entry)
    return buckets, unknown


async def _run_one_hydrate_batch(
    *,
    idx: int,
    total: int,
    source: str,
    transaction: str,
    urls: list[UrlInput],
    actor: Any,
    actor_input: HydrateInput,
    hooks: HydrateActorHooks,
    proxy_configuration: Any,
    totals: dict[str, int],
    per_batch_results: list[dict[str, Any]],
    error_log: list[dict[str, Any]],
) -> None:
    """Fetch + parse one ``(source, transaction)`` bucket with retry."""

    crawl_fn = hooks.crawl_urls_by_source.get(source)
    if crawl_fn is None:
        # detect_source guarantees the key is in the map; this branch
        # is a defensive no-op in case a caller misconfigures hooks.
        error_log.append(
            {
                "source": source,
                "transaction": transaction,
                "urls_submitted": len(urls),
                "error": (
                    f"no crawl_urls_fn registered for source {source!r}"
                ),
                "error_type": "HooksMisconfigured",
                "attempt": 0,
            }
        )
        totals["errors"] += 1
        totals["batches_failed"] += 1
        return

    attempts = actor_input.max_attempts_per_batch
    raw_urls = [u.url for u in urls]

    final_report: CrawlReport | None = None
    final_error: str | None = None
    final_error_type: str | None = None
    actual_attempts = 0

    for attempt_idx in range(1, attempts + 1):
        actual_attempts = attempt_idx
        totals["attempts"] += 1

        proxy_url = await _mint_proxy_url(
            proxy_configuration,
            rotate=actor_input.rotate_proxy_per_attempt or attempt_idx > 1,
        )
        crawler = _build_crawler(_ProxyShim(actor_input), proxy_url=proxy_url)

        logger.info(
            "[hydrate] %s/%s batch %d/%d attempt %d/%d: %d URLs",
            source,
            transaction,
            idx + 1,
            total,
            attempt_idx,
            attempts,
            len(raw_urls),
        )
        try:
            async with crawler:
                if actor_input.batch_timeout_s is not None:
                    attempt_report = await asyncio.wait_for(
                        crawl_fn(
                            crawler,
                            raw_urls,
                            transaction=transaction,
                        ),
                        timeout=actor_input.batch_timeout_s,
                    )
                else:
                    attempt_report = await crawl_fn(
                        crawler,
                        raw_urls,
                        transaction=transaction,
                    )
        except TimeoutError as exc:
            final_error = (
                f"batch exceeded batchTimeoutSec="
                f"{actor_input.batch_timeout_s}s"
            )
            final_error_type = type(exc).__name__
            logger.warning(
                "[hydrate] %s/%s batch timed out on attempt %d/%d",
                source,
                transaction,
                attempt_idx,
                attempts,
            )
            if attempt_idx < attempts:
                await _sleep_with_backoff(
                    attempt_idx=attempt_idx,
                    initial_s=actor_input.backoff_initial_s,
                    max_s=actor_input.backoff_max_s,
                )
            continue
        except Exception as exc:
            final_error = str(exc)
            final_error_type = type(exc).__name__
            logger.exception(
                "[hydrate] %s/%s batch failed on attempt %d/%d: %s",
                source,
                transaction,
                attempt_idx,
                attempts,
                exc,
            )
            if attempt_idx < attempts:
                await _sleep_with_backoff(
                    attempt_idx=attempt_idx,
                    initial_s=actor_input.backoff_initial_s,
                    max_s=actor_input.backoff_max_s,
                )
            continue

        final_report = attempt_report
        if (
            attempt_idx < attempts
            and not attempt_report.listings
            and attempt_report.errors
        ):
            final_error = attempt_report.errors[0]
            final_error_type = "CrawlerBlocked"
            logger.warning(
                "[hydrate] %s/%s returned zero listings with %d errors "
                "on attempt %d/%d - retrying on fresh proxy",
                source,
                transaction,
                len(attempt_report.errors),
                attempt_idx,
                attempts,
            )
            final_report = None
            await _sleep_with_backoff(
                attempt_idx=attempt_idx,
                initial_s=actor_input.backoff_initial_s,
                max_s=actor_input.backoff_max_s,
            )
            continue

        final_error = None
        final_error_type = None
        break

    if final_report is not None:
        await _push_listings(actor, final_report.listings)
        totals["items"] += len(final_report.listings)
        totals["detail_pages"] += final_report.detail_pages_fetched
        totals["errors"] += len(final_report.errors)

        per_batch_results.append(
            {
                "source": source,
                "transaction": transaction,
                "urls_submitted": len(raw_urls),
                "detail_pages_fetched": final_report.detail_pages_fetched,
                "listings": len(final_report.listings),
                "attempts": actual_attempts,
                "errors": final_report.errors,
            }
        )
        for err in final_report.errors:
            error_log.append(
                {
                    "source": source,
                    "transaction": transaction,
                    "error": err,
                    "error_type": "CrawlerReport",
                    "attempt": actual_attempts,
                }
            )
    else:
        totals["errors"] += 1
        totals["batches_failed"] += 1
        error_log.append(
            {
                "source": source,
                "transaction": transaction,
                "urls_submitted": len(raw_urls),
                "error": final_error or "unknown failure",
                "error_type": final_error_type or "UnknownError",
                "attempt": actual_attempts,
            }
        )
        per_batch_results.append(
            {
                "source": source,
                "transaction": transaction,
                "urls_submitted": len(raw_urls),
                "detail_pages_fetched": 0,
                "listings": 0,
                "attempts": actual_attempts,
                "errors": [final_error or "unknown failure"],
            }
        )


class _ProxyShim:
    """Adapter exposing the fields the private ``_build_*`` helpers need.

    :func:`run_listings.{_build_crawler,_build_proxy_configuration}`
    were written against :class:`ActorInput`, but they only touch a
    handful of attributes (``proxy_configuration``, ``rate_per_second``,
    ``discord_webhook_url``). The hydrate :class:`HydrateInput`
    exposes the same attributes by design, so this thin shim avoids
    refactoring the private helpers while keeping the two run-loops
    in lockstep.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: HydrateInput) -> None:
        self._inner = inner

    @property
    def proxy_configuration(self) -> ProxyInput:
        return self._inner.proxy_configuration

    @property
    def rate_per_second(self) -> float:
        return self._inner.rate_per_second

    @property
    def discord_webhook_url(self) -> str | None:
        return self._inner.discord_webhook_url


__all__ = [
    "DetectSourceFn",
    "HydrateActorHooks",
    "HydrateInput",
    "parse_hydrate_input",
    "run_hydrate_actor",
]
