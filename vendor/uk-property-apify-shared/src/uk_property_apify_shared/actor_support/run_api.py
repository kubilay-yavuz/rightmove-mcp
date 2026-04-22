"""Generic run-loop for API-backed Apify actors (A4/A5/A7 pattern).

Three API-only actors (``epc-ct-ppd-unified``, ``planning-aggregator``,
``landlord-network``) all follow the same control flow:

1. Lazy-import :class:`apify.Actor` so tests can stub it.
2. Parse + validate input via a Pydantic model.
3. Derive a deduped list of units of work (postcodes / councils / seeds).
4. Fan out unit execution with ``asyncio.gather(..., return_exceptions=True)``,
   gated by a semaphore whose size the actor picks.
5. Classify each outcome as *success*, *soft skip*, or *hard error*.
6. Push successful rows to the dataset; accumulate per-item stats and
   error log entries.
7. Write ``RUN_META`` and, when any unit fails, ``ERRORS`` to the
   default key-value store.

Rather than re-implement that loop three times, each actor now declares
a small :class:`ApiActorHooks` dataclass — name + version + ``parse_input``
+ ``plan_units`` + ``run_unit`` plus any lifecycle hooks it needs —
and calls :func:`run_api_actor`. The hooks mutate the shared
:class:`RunContext` so each actor can pick its own totals shape,
per-item shape, and RUN_META top-level extras without the wrapper
needing to know.

See the A7 ``landlord-network`` actor for the canonical minimal usage
and A5 ``planning-aggregator`` for the full lifecycle-hook set.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable


def _default_concurrency(_: Any) -> int:
    return 1


def _default_describe_unit(_: Any) -> dict[str, Any]:
    return {}


def _default_max_attempts(_: Any) -> int:
    return 1


def _default_unit_timeout_s(_: Any) -> float | None:
    return None


def _default_backoff_initial_s(_: Any) -> float:
    return 1.0


def _default_backoff_max_s(_: Any) -> float:
    return 30.0


def _default_retriable(_: BaseException) -> bool:
    """Any exception that isn't a ``soft_skip_types`` match is retriable.

    Actors that want to distinguish "permanent 404 — don't bother retrying"
    from "transient 500 — retry" can override this with e.g.
    ``lambda exc: not isinstance(exc, PermanentNotFound)``.
    """

    return True


async def _sleep_with_backoff(
    *, attempt_idx: int, initial_s: float, max_s: float
) -> None:
    """Sleep with capped exponential backoff + jitter before next retry.

    ``attempt_idx`` is 1-based so the first sleep = ``initial_s``, the
    second = ``2 * initial_s``, …, up to ``max_s``. ±25% jitter avoids
    thundering-herd on shared upstreams.
    """

    delay = min(max_s, initial_s * (2 ** (attempt_idx - 1)))
    jitter = delay * (0.75 + random.random() * 0.5)
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.sleep(jitter)


@dataclass
class RunContext[Input, Unit]:
    """Mutable accumulator threaded through every lifecycle hook.

    Hooks update fields in place; the wrapper never inspects individual
    counts or entries. At run end, :attr:`totals`, :attr:`per_item`,
    :attr:`errors`, and :attr:`extra_meta` are all shipped into
    ``RUN_META`` / ``ERRORS`` exactly as left by the last hook.
    """

    input: Input
    units: list[Unit]
    totals: dict[str, int] = field(default_factory=dict)
    per_item: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    extra_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApiActorHooks[Input, Unit, Outcome]:
    """Per-actor configuration + DI seam for :func:`run_api_actor`.

    Required: ``source``, ``actor_version``, ``parse_input``,
    ``plan_units``, ``run_unit``. Everything else has a sensible default;
    override the lifecycle hooks to shape custom RUN_META / ERRORS /
    per-item output.
    """

    source: str
    """Logical actor name; populates ``RUN_META.source`` and the logger name."""

    actor_version: str
    """Version tag from the actor package; populates ``RUN_META.actor_version``."""

    parse_input: Callable[[dict[str, Any]], Input]
    """Convert the raw Apify input blob into a validated :class:`Input`.
    Typically ``lambda raw: MyActorInput.model_validate(raw)``."""

    plan_units: Callable[[Input], Iterable[Unit] | Awaitable[Iterable[Unit]]]
    """Return the units of work to process (deduped / sorted as the
    actor sees fit). Called once per run. May be sync (returning a
    plain iterable) or async (returning an awaitable iterable) — useful
    when the planning step needs to do an async pre-flight (e.g.
    A5's ``dispatch_councils``)."""

    run_unit: Callable[[Unit, Input], Awaitable[Outcome]]
    """Execute one unit. Raising any exception in
    :attr:`soft_skip_types` marks the unit *skipped* (tracked separately
    from failures); raising any other exception marks it *failed*."""

    describe_unit: Callable[[Unit], dict[str, Any]] = field(
        default=_default_describe_unit
    )
    """Produce identifying fields for a unit — used by the default
    skip/error handlers to tag entries in ``ERRORS`` and
    :attr:`RunContext.per_item`. Typical implementations return
    ``{"council": unit.slug}`` or ``{"postcode": unit}``."""

    concurrency: Callable[[Input], int] = field(default=_default_concurrency)
    """How many units to run in parallel. Called once per run.
    Default is 1 (effectively sequential), matching the A4 pattern.
    Return ``len(units)`` (or higher) for the A5 unbounded-fan-out
    shape."""

    soft_skip_types: tuple[type[Exception], ...] = ()
    """Exception types treated as a *soft skip* rather than a hard
    error. Typically a single dispatch-level marker like
    ``CouncilSkippedError``. The default tuple is empty (no skips)."""

    init_totals: Callable[[RunContext[Input, Unit]], None] | None = None
    """Populate ``ctx.totals`` at run start. Default seeds ``units`` and
    ``errors`` counters."""

    on_success: (
        Callable[[Any, RunContext[Input, Unit], Unit, Outcome], Awaitable[None]]
        | None
    ) = None
    """Handle a successful outcome. First argument is the Apify Actor
    module (lazy-imported) so the hook can call ``Actor.push_data(...)``
    directly. If ``None``, successes are counted but no dataset rows are
    pushed — suitable only for "side-effect only" actors."""

    on_skip: (
        Callable[[RunContext[Input, Unit], Unit, Exception], None] | None
    ) = None
    """Handle a soft skip. Default increments ``totals.skipped``, adds a
    ``{**describe_unit(unit), "skipped": True, "reason": str(exc)}``
    entry to both ``per_item`` and ``errors``."""

    on_error: (
        Callable[[RunContext[Input, Unit], Unit, Exception], None] | None
    ) = None
    """Handle a hard error. Default increments ``totals.errors``, adds a
    ``{**describe_unit(unit), "error": str(exc), "error_type":
    type(exc).__name__}`` entry to both ``per_item`` and ``errors``."""

    finalise_meta: (
        Callable[[RunContext[Input, Unit], dict[str, Any]], None] | None
    ) = None
    """Last chance to mutate the ``RUN_META`` dict before it's uploaded.
    Use this for actor-specific top-level fields (e.g. A5's ``mode``,
    A7's ``parameters``). Called after ``extra_meta`` merges in."""

    per_item_key: str = "per_item"
    """Key name under which ``ctx.per_item`` appears in ``RUN_META``.
    Defaults to ``per_item``; set to ``per_council`` / ``per_postcode``
    / ``per_seed`` to match legacy consumer expectations."""

    max_attempts_per_unit: Callable[[Input], int] = field(
        default=_default_max_attempts
    )
    """How many times to retry each unit on a retriable exception or
    timeout. Default 1 (no retries). Between attempts we sleep using
    capped exponential backoff + jitter. Return value is clamped to
    ``>= 1``."""

    unit_timeout_s: Callable[[Input], float | None] = field(
        default=_default_unit_timeout_s
    )
    """Hard per-unit time budget in seconds. ``None`` disables the
    timeout and leans on each client's native per-request budget.
    TimeoutError counts as a retriable failure (consistent with
    listings run-loop)."""

    backoff_initial_s: Callable[[Input], float] = field(
        default=_default_backoff_initial_s
    )
    """Initial backoff delay between retries (first retry). Grows
    exponentially capped at :attr:`backoff_max_s`."""

    backoff_max_s: Callable[[Input], float] = field(
        default=_default_backoff_max_s
    )
    """Cap on exponential backoff between retries."""

    is_retriable: Callable[[BaseException], bool] = field(
        default=_default_retriable
    )
    """Classify an exception as retriable. ``TimeoutError`` is always
    retriable regardless of this callback. ``soft_skip_types`` matches
    are never retried (they short-circuit to the skip path)."""


async def run_api_actor[Input, Unit, Outcome](
    hooks: ApiActorHooks[Input, Unit, Outcome],
) -> None:
    """Run the shared API-actor flow described by :mod:`run_api` module doc.

    The :class:`ApiActorHooks` argument is the only per-actor surface;
    every other moving part (Actor lifecycle, input parse, fan-out,
    gather with ``return_exceptions=True``, RUN_META / ERRORS upload)
    is owned here.
    """

    try:
        from apify import Actor
    except ImportError as exc:  # pragma: no cover - runtime-only dep
        raise RuntimeError(
            "apify SDK not installed. Run with `pip install apify` or via "
            "the Apify Docker image."
        ) from exc

    logger = logging.getLogger(f"{hooks.source.replace('-', '_')}_actor")

    async with Actor:
        raw_input = await Actor.get_input() or {}
        parsed: Input = hooks.parse_input(raw_input)
        planned = hooks.plan_units(parsed)
        # Accept both sync iterables and async (awaitable) returns from
        # plan_units so actors with async pre-flight (A5's
        # dispatch_councils) can still use this scaffold.
        if inspect.isawaitable(planned):
            planned = await planned
        units: list[Unit] = list(planned)

        ctx: RunContext[Input, Unit] = RunContext(input=parsed, units=units)
        if hooks.init_totals is not None:
            hooks.init_totals(ctx)
        else:
            ctx.totals.setdefault("units", len(units))
            ctx.totals.setdefault("errors", 0)

        concurrency = max(1, hooks.concurrency(parsed))
        semaphore = asyncio.Semaphore(concurrency)

        max_attempts = max(1, hooks.max_attempts_per_unit(parsed))
        timeout_s = hooks.unit_timeout_s(parsed)
        backoff_initial = hooks.backoff_initial_s(parsed)
        backoff_max = hooks.backoff_max_s(parsed)
        total_attempts = 0

        async def _one(unit: Unit) -> Outcome:
            """Run one unit with optional timeout + retry + backoff.

            We surface the final exception (including ``TimeoutError``)
            to the outer ``gather`` so classification + ``on_error`` /
            ``on_skip`` stay exactly as before. Soft-skip markers are
            raised on the first occurrence (never retried). All other
            exceptions go through ``is_retriable``.
            """

            nonlocal total_attempts
            last_exc: BaseException | None = None
            async with semaphore:
                for attempt_idx in range(1, max_attempts + 1):
                    total_attempts += 1
                    try:
                        if timeout_s is not None:
                            return await asyncio.wait_for(
                                hooks.run_unit(unit, parsed),
                                timeout=timeout_s,
                            )
                        return await hooks.run_unit(unit, parsed)
                    except TimeoutError as exc:
                        last_exc = exc
                        logger.warning(
                            "[%s] unit timed out (%.1fs) attempt %d/%d",
                            hooks.source,
                            timeout_s or 0.0,
                            attempt_idx,
                            max_attempts,
                        )
                    except BaseException as exc:
                        if hooks.soft_skip_types and isinstance(
                            exc, hooks.soft_skip_types
                        ):
                            raise
                        if not hooks.is_retriable(exc):
                            raise
                        last_exc = exc
                        logger.warning(
                            "[%s] unit retriable failure attempt %d/%d: "
                            "%s (%s)",
                            hooks.source,
                            attempt_idx,
                            max_attempts,
                            exc,
                            type(exc).__name__,
                        )

                    if attempt_idx < max_attempts:
                        await _sleep_with_backoff(
                            attempt_idx=attempt_idx,
                            initial_s=backoff_initial,
                            max_s=backoff_max,
                        )
                assert last_exc is not None
                raise last_exc

        outcomes = await asyncio.gather(
            *(_one(unit) for unit in units),
            return_exceptions=True,
        )

        for unit, outcome in zip(units, outcomes, strict=True):
            if hooks.soft_skip_types and isinstance(outcome, hooks.soft_skip_types):
                if hooks.on_skip is not None:
                    hooks.on_skip(ctx, unit, outcome)
                else:
                    _default_on_skip(ctx, unit, outcome, hooks.describe_unit)
                continue
            if isinstance(outcome, BaseException):
                logger.warning(
                    "[%s] unit failed: %s (%s)",
                    hooks.source,
                    outcome,
                    type(outcome).__name__,
                )
                if hooks.on_error is not None:
                    hooks.on_error(ctx, unit, outcome)
                else:
                    _default_on_error(ctx, unit, outcome, hooks.describe_unit)
                continue

            if hooks.on_success is not None:
                await hooks.on_success(Actor, ctx, unit, outcome)

        if max_attempts > 1:
            ctx.totals.setdefault("attempts", total_attempts)

        meta: dict[str, Any] = {
            "started_at": datetime.now(UTC).isoformat(),
            "actor_version": hooks.actor_version,
            "source": hooks.source,
            "totals": ctx.totals,
            hooks.per_item_key: ctx.per_item,
        }
        meta.update(ctx.extra_meta)
        if hooks.finalise_meta is not None:
            hooks.finalise_meta(ctx, meta)

        await Actor.set_value("RUN_META", meta)
        if ctx.errors:
            await Actor.set_value("ERRORS", ctx.errors)

        logger.info("[%s] run complete: %s", hooks.source, ctx.totals)


def _default_on_skip(
    ctx: RunContext[Any, Any],
    unit: Any,
    exc: BaseException,
    describe: Callable[[Any], dict[str, Any]],
) -> None:
    ctx.totals["skipped"] = ctx.totals.get("skipped", 0) + 1
    entry = {
        **describe(unit),
        "skipped": True,
        "reason": str(exc),
    }
    ctx.per_item.append(entry)
    ctx.errors.append(dict(entry))


def _default_on_error(
    ctx: RunContext[Any, Any],
    unit: Any,
    exc: BaseException,
    describe: Callable[[Any], dict[str, Any]],
) -> None:
    ctx.totals["errors"] = ctx.totals.get("errors", 0) + 1
    entry = {
        **describe(unit),
        "error": str(exc),
        "error_type": type(exc).__name__,
    }
    ctx.per_item.append(entry)
    ctx.errors.append(dict(entry))


__all__ = [
    "ApiActorHooks",
    "RunContext",
    "run_api_actor",
]
