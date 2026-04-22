"""Generic run-loop for listings Apify actors.

``run_listings_actor`` takes a small :class:`ListingsActorHooks` object
describing *which* site to crawl; everything else (input validation,
proxy resolution, crawler wiring, dataset push, RUN_META) is shared.

Polished behaviour (vs. the original sequential loop):

* Queries are deduplicated before crawling (case-insensitive location +
  identical filters) so an accidental repeat in the actor input doesn't
  double the traffic.
* Each query is retried up to ``maxAttemptsPerQuery`` times on hard
  failure. Between attempts the Apify proxy URL is rotated (= fresh
  exit IP) and a capped exponential backoff is applied.
* A hard per-query timeout (``queryTimeoutSec``) stops a hung browser
  fetcher from blocking the whole run.
* Queries run through a :class:`asyncio.Semaphore` so
  ``queryConcurrency`` can be raised safely (the crawler's per-domain
  rate-limiter still enforces politeness).
* Errors are emitted in the same structured shape as the API-backed
  actors (:class:`uk_property_apify_shared.actor_support.ApiActorHooks`)
  — ``{source, query, error, error_type, attempt}`` — so downstream
  dashboards can treat both families uniformly.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from uk_property_listings import SearchQuery

from uk_property_apify_shared.actor_support.input import (
    ActorInput,
    QueryInput,
    UrlInput,
    parse_input,
)
from uk_property_apify_shared.crawler import (
    Crawler,
    CrawlerConfig,
    DomainRateLimiter,
)

if TYPE_CHECKING:
    from uk_property_listings import CrawlReport
    from uk_property_scrapers.schema import Listing

logger = logging.getLogger("uk_property_apify_shared.actor_support")

CrawlFn = Callable[..., Awaitable["CrawlReport"]]
CrawlUrlsFn = Callable[..., Awaitable["CrawlReport"]]


@dataclass(frozen=True)
class ListingsActorHooks:
    """Per-site configuration for :func:`run_listings_actor`."""

    source: str
    actor_version: str
    crawl_fn: CrawlFn
    crawl_urls_fn: CrawlUrlsFn | None = None
    """Optional URL-list crawler.

    Required when the actor accepts ``listingUrls``. Signature:
    ``async def fn(crawler, urls: list[str], *, transaction: 'sale' | 'rent') -> CrawlReport``.
    """


async def run_listings_actor(hooks: ListingsActorHooks) -> None:
    """Apify entry point - wires Actor I/O to the chosen crawl function."""

    try:
        from apify import Actor
    except ImportError as exc:  # pragma: no cover - runtime-only dep
        raise RuntimeError(
            "apify SDK not installed. Run with `pip install apify` or via "
            "the Apify Docker image."
        ) from exc

    async with Actor:
        raw_input = await Actor.get_input() or {}
        actor_input = parse_input(raw_input)
        queries = actor_input.deduped_queries()
        url_inputs = actor_input.deduped_listing_urls()
        duplicates_collapsed = (
            len(actor_input.queries)
            - len(queries)
            + len(actor_input.listing_urls)
            - len(url_inputs)
        )

        if url_inputs and hooks.crawl_urls_fn is None:
            raise RuntimeError(
                f"Actor '{hooks.source}' received listingUrls input but "
                "the hooks don't define a crawl_urls_fn. Wire one up in "
                "the actor's run.py to enable URL-list mode."
            )

        proxy_configuration = await _build_proxy_configuration(
            Actor, actor_input
        )

        totals = {
            "items": 0,
            "pages": 0,
            "detail_pages": 0,
            "errors": 0,
            "attempts": 0,
            "queries_failed": 0,
            "url_batches_failed": 0,
            "urls_submitted": len(url_inputs),
            "duplicates_collapsed": duplicates_collapsed,
        }
        per_query_results: list[dict[str, Any]] = []
        per_url_batch_results: list[dict[str, Any]] = []
        error_log: list[dict[str, Any]] = []

        semaphore = asyncio.Semaphore(actor_input.query_concurrency)

        async def _process_query(idx: int, query: QueryInput) -> None:
            async with semaphore:
                await _run_one_query(
                    idx=idx,
                    total=len(queries),
                    query=query,
                    actor=Actor,
                    actor_input=actor_input,
                    hooks=hooks,
                    proxy_configuration=proxy_configuration,
                    totals=totals,
                    per_query_results=per_query_results,
                    error_log=error_log,
                )

        await asyncio.gather(
            *(_process_query(i, q) for i, q in enumerate(queries)),
            return_exceptions=False,
        )

        key_order = {q.dedupe_key(): i for i, q in enumerate(queries)}
        per_query_results.sort(
            key=lambda row: key_order.get(row.get("_dedupe_key", ""), 0)
        )
        for row in per_query_results:
            row.pop("_dedupe_key", None)

        if url_inputs:
            await _run_url_batches(
                url_inputs=url_inputs,
                actor=Actor,
                actor_input=actor_input,
                hooks=hooks,
                proxy_configuration=proxy_configuration,
                totals=totals,
                per_url_batch_results=per_url_batch_results,
                error_log=error_log,
                semaphore=semaphore,
            )

        meta = {
            "started_at": datetime.now(UTC).isoformat(),
            "source": hooks.source,
            "actor_version": hooks.actor_version,
            "totals": totals,
            "parameters": {
                "max_pages_per_query": actor_input.max_pages_per_query,
                "hydrate_details": actor_input.hydrate_details,
                "rate_per_second": actor_input.rate_per_second,
                "query_concurrency": actor_input.query_concurrency,
                "max_attempts_per_query": actor_input.max_attempts_per_query,
                "query_timeout_s": actor_input.query_timeout_s,
                "rotate_proxy_per_attempt": actor_input.rotate_proxy_per_attempt,
                "dedupe_queries": actor_input.dedupe_queries,
            },
            "per_query": per_query_results,
        }
        if per_url_batch_results:
            meta["per_url_batch"] = per_url_batch_results
        await Actor.set_value("RUN_META", meta)
        if error_log:
            await Actor.set_value("ERRORS", error_log)
        logger.info("[%s] run complete: %s", hooks.source, totals)


async def _run_one_query(
    *,
    idx: int,
    total: int,
    query: QueryInput,
    actor: Any,
    actor_input: ActorInput,
    hooks: ListingsActorHooks,
    proxy_configuration: Any,
    totals: dict[str, int],
    per_query_results: list[dict[str, Any]],
    error_log: list[dict[str, Any]],
) -> None:
    """Run one query with retry + proxy rotation + timeout."""

    search_query = SearchQuery(
        location=query.location,
        transaction=query.transaction,
        min_price=query.min_price,
        max_price=query.max_price,
        min_beds=query.min_beds,
        max_beds=query.max_beds,
        max_pages=actor_input.max_pages_per_query,
    )
    attempts = actor_input.max_attempts_per_query

    final_report: CrawlReport | None = None
    final_error: str | None = None
    final_error_type: str | None = None
    actual_attempts = 0

    for attempt_idx in range(1, attempts + 1):
        actual_attempts = attempt_idx
        totals["attempts"] += 1

        proxy_url = await _mint_proxy_url(
            proxy_configuration,
            rotate=actor_input.rotate_proxy_per_attempt
            or attempt_idx > 1,
        )
        crawler = _build_crawler(actor_input, proxy_url=proxy_url)

        logger.info(
            "[%s] query %d/%d attempt %d/%d: %s (%s)",
            hooks.source,
            idx + 1,
            total,
            attempt_idx,
            attempts,
            query.location,
            query.transaction,
        )
        try:
            async with crawler:
                if actor_input.query_timeout_s is not None:
                    attempt_report = await asyncio.wait_for(
                        hooks.crawl_fn(
                            crawler,
                            search_query,
                            hydrate_details=actor_input.hydrate_details,
                        ),
                        timeout=actor_input.query_timeout_s,
                    )
                else:
                    attempt_report = await hooks.crawl_fn(
                        crawler,
                        search_query,
                        hydrate_details=actor_input.hydrate_details,
                    )
        except TimeoutError as exc:
            final_error = (
                f"query exceeded queryTimeoutSec="
                f"{actor_input.query_timeout_s}s"
            )
            final_error_type = type(exc).__name__
            logger.warning(
                "[%s] query %s timed out on attempt %d/%d",
                hooks.source,
                query.location,
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
                "[%s] query %s failed on attempt %d/%d: %s",
                hooks.source,
                query.location,
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

        # The crawler returned a CrawlReport. A report with zero
        # listings + any errors usually means every page was blocked
        # (WAF, 5xx, …) - treat it as a retriable failure so the next
        # attempt gets a fresh proxy IP. A report with at least one
        # listing is accepted even if some pages errored: partial data
        # is still useful downstream.
        final_report = attempt_report
        if (
            attempt_idx < attempts
            and not attempt_report.listings
            and attempt_report.errors
        ):
            final_error = attempt_report.errors[0]
            final_error_type = "CrawlerBlocked"
            logger.warning(
                "[%s] query %s returned zero listings with %d errors on "
                "attempt %d/%d - retrying on fresh proxy",
                hooks.source,
                query.location,
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
        totals["pages"] += final_report.pages_fetched
        totals["detail_pages"] += final_report.detail_pages_fetched
        totals["errors"] += len(final_report.errors)

        per_query_results.append(
            {
                "_dedupe_key": query.dedupe_key(),
                "query": query.model_dump(by_alias=True),
                "pages_fetched": final_report.pages_fetched,
                "detail_pages_fetched": final_report.detail_pages_fetched,
                "listings": len(final_report.listings),
                "attempts": actual_attempts,
                "errors": final_report.errors,
            }
        )
        for err in final_report.errors:
            error_log.append(
                {
                    "source": hooks.source,
                    "query": query.model_dump(by_alias=True),
                    "error": err,
                    "error_type": "CrawlerReport",
                    "attempt": actual_attempts,
                }
            )
    else:
        totals["errors"] += 1
        totals["queries_failed"] += 1
        error_log.append(
            {
                "source": hooks.source,
                "query": query.model_dump(by_alias=True),
                "error": final_error or "unknown failure",
                "error_type": final_error_type or "UnknownError",
                "attempt": actual_attempts,
            }
        )
        per_query_results.append(
            {
                "_dedupe_key": query.dedupe_key(),
                "query": query.model_dump(by_alias=True),
                "pages_fetched": 0,
                "detail_pages_fetched": 0,
                "listings": 0,
                "attempts": actual_attempts,
                "errors": [final_error or "unknown failure"],
            }
        )


async def _run_url_batches(
    *,
    url_inputs: list[UrlInput],
    actor: Any,
    actor_input: ActorInput,
    hooks: ListingsActorHooks,
    proxy_configuration: Any,
    totals: dict[str, int],
    per_url_batch_results: list[dict[str, Any]],
    error_log: list[dict[str, Any]],
    semaphore: asyncio.Semaphore,
) -> None:
    """Group URLs by transaction type and run each bucket as one unit.

    Each transaction bucket is one batch: the crawler is built once,
    :func:`hooks.crawl_urls_fn` fan-outs internally with its own
    per-URL concurrency, and the batch retries on hard failure with
    proxy rotation (same semantics as queries). This amortises
    crawler + proxy setup across the whole URL list while keeping
    batches small enough that a single hung URL doesn't block the
    other bucket.
    """

    buckets: dict[str, list[UrlInput]] = {}
    for entry in url_inputs:
        buckets.setdefault(entry.transaction, []).append(entry)

    async def _process_bucket(idx: int, transaction: str) -> None:
        bucket = buckets[transaction]
        async with semaphore:
            await _run_one_url_batch(
                idx=idx,
                total=len(buckets),
                transaction=transaction,
                urls=bucket,
                actor=actor,
                actor_input=actor_input,
                hooks=hooks,
                proxy_configuration=proxy_configuration,
                totals=totals,
                per_url_batch_results=per_url_batch_results,
                error_log=error_log,
            )

    await asyncio.gather(
        *(
            _process_bucket(idx, txn)
            for idx, txn in enumerate(sorted(buckets))
        ),
        return_exceptions=False,
    )


async def _run_one_url_batch(
    *,
    idx: int,
    total: int,
    transaction: str,
    urls: list[UrlInput],
    actor: Any,
    actor_input: ActorInput,
    hooks: ListingsActorHooks,
    proxy_configuration: Any,
    totals: dict[str, int],
    per_url_batch_results: list[dict[str, Any]],
    error_log: list[dict[str, Any]],
) -> None:
    """Fetch + parse one transaction bucket of URLs with retry + backoff."""

    if hooks.crawl_urls_fn is None:
        raise RuntimeError(  # pragma: no cover - validated earlier
            "crawl_urls_fn missing at batch dispatch time"
        )
    attempts = actor_input.max_attempts_per_query
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
        crawler = _build_crawler(actor_input, proxy_url=proxy_url)
        logger.info(
            "[%s] url batch %d/%d attempt %d/%d: %d URLs (%s)",
            hooks.source,
            idx + 1,
            total,
            attempt_idx,
            attempts,
            len(raw_urls),
            transaction,
        )
        try:
            async with crawler:
                if actor_input.query_timeout_s is not None:
                    attempt_report = await asyncio.wait_for(
                        hooks.crawl_urls_fn(
                            crawler,
                            raw_urls,
                            transaction=transaction,
                        ),
                        timeout=actor_input.query_timeout_s,
                    )
                else:
                    attempt_report = await hooks.crawl_urls_fn(
                        crawler,
                        raw_urls,
                        transaction=transaction,
                    )
        except TimeoutError as exc:
            final_error = (
                f"url batch exceeded queryTimeoutSec="
                f"{actor_input.query_timeout_s}s"
            )
            final_error_type = type(exc).__name__
            logger.warning(
                "[%s] url batch %s timed out on attempt %d/%d",
                hooks.source,
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
                "[%s] url batch %s failed on attempt %d/%d: %s",
                hooks.source,
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
                "[%s] url batch %s returned zero listings with %d errors "
                "on attempt %d/%d - retrying on fresh proxy",
                hooks.source,
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

        per_url_batch_results.append(
            {
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
                    "source": hooks.source,
                    "url_batch_transaction": transaction,
                    "error": err,
                    "error_type": "CrawlerReport",
                    "attempt": actual_attempts,
                }
            )
    else:
        totals["errors"] += 1
        totals["url_batches_failed"] += 1
        error_log.append(
            {
                "source": hooks.source,
                "url_batch_transaction": transaction,
                "error": final_error or "unknown failure",
                "error_type": final_error_type or "UnknownError",
                "attempt": actual_attempts,
            }
        )
        per_url_batch_results.append(
            {
                "transaction": transaction,
                "urls_submitted": len(raw_urls),
                "detail_pages_fetched": 0,
                "listings": 0,
                "attempts": actual_attempts,
                "errors": [final_error or "unknown failure"],
            }
        )


async def _sleep_with_backoff(
    *, attempt_idx: int, initial_s: float, max_s: float
) -> None:
    """Sleep with capped exponential backoff + jitter before next retry.

    Uses ``attempt_idx`` (1-based) so the first sleep = ``initial_s``,
    the second = ``2 * initial_s``, …, up to ``max_s``. Small +/-25 %
    jitter avoids thundering-herd on shared proxy pools.
    """

    delay = min(max_s, initial_s * (2 ** (attempt_idx - 1)))
    jitter = delay * (0.75 + random.random() * 0.5)
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(jitter)


async def _build_proxy_configuration(actor: Any, actor_input: ActorInput) -> Any:
    """Build the Apify proxy configuration once per run.

    Returns ``None`` if the caller disabled the Apify proxy or if the
    SDK call fails (we log and continue without a proxy).
    """

    cfg = actor_input.proxy_configuration
    if not cfg.use_apify_proxy:
        return None
    try:
        proxy_configuration = await actor.create_proxy_configuration(
            groups=cfg.apify_proxy_groups or [],
        )
    except Exception:  # pragma: no cover - varies by SDK version
        logger.exception("could not build Apify proxy configuration")
        return None
    return proxy_configuration


async def _mint_proxy_url(proxy_configuration: Any, *, rotate: bool) -> str | None:
    """Mint a (possibly fresh) proxy URL from the configuration."""

    if proxy_configuration is None:
        return None
    try:
        if rotate:
            return await proxy_configuration.new_url()
        url = getattr(proxy_configuration, "_cached_url", None)
        if url is None:
            url = await proxy_configuration.new_url()
            # Cache the first-shot URL on the config object so we don't
            # spin up a fresh session per query when rotation is
            # disabled. The SDK doesn't expose a "keep current" API.
            with contextlib.suppress(AttributeError):
                proxy_configuration._cached_url = url  # type: ignore[attr-defined]
        return url
    except Exception:  # pragma: no cover - SDK variance
        logger.exception("could not mint Apify proxy URL")
        return None


def _build_crawler(actor_input: ActorInput, *, proxy_url: str | None) -> Crawler:
    base = CrawlerConfig.from_env()
    overrides: dict[str, Any] = {
        "default_rate_per_sec": actor_input.rate_per_second,
    }
    if proxy_url:
        overrides["proxy_url"] = proxy_url
    if actor_input.discord_webhook_url:
        overrides["discord_webhook_url"] = actor_input.discord_webhook_url
    config = replace(base, **overrides)
    limiter = DomainRateLimiter(
        default_rate_per_sec=config.default_rate_per_sec,
        per_domain_rate_per_sec=config.per_domain_rate_per_sec,
    )
    return Crawler(config=config, rate_limiter=limiter)


async def _push_listings(actor: Any, listings: list[Listing]) -> None:
    if not listings:
        return
    batch = [_listing_to_record(listing) for listing in listings]
    await actor.push_data(batch)


def _listing_to_record(listing: Listing) -> dict[str, Any]:
    record = listing.model_dump(mode="json")
    return _ensure_plain(record)


def _ensure_plain(value: Any) -> Any:
    """Convert dataclasses and unexpected types into pure JSON primitives."""

    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return {k: _ensure_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ensure_plain(v) for v in value]
    return value


__all__ = ["CrawlUrlsFn", "ListingsActorHooks", "run_listings_actor"]
