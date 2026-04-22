"""Portal-agnostic MCP tool helpers for the delta pipeline.

Each portal MCP ships five delta tools with identical input / output
shapes — only the underlying fetcher differs:

* ``watch_listing`` — fetch a single listing, snapshot it, emit any
  change events that fire vs. the previous snapshot stored locally.
* ``watch_query`` — fetch a full search result page and ingest every
  listing; useful as a periodic cron to keep the snapshot store fresh
  for downstream firehoses.
* ``reductions_firehose`` — query the store for recent
  ``PRICE_REDUCED`` events.
* ``new_listings_firehose`` — query the store for recent ``NEW`` events.
* ``back_on_market`` — query the store for recent ``BACK_ON_MARKET``
  events.

The heavy lifting (snapshot, diff, store) lives in the shared package;
per-portal MCPs just bind their existing fetcher to :func:`ingest_listings`
and expose the store configuration via env vars.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from uk_property_scrapers.schema import (  # noqa: TC002 (Pydantic field types)
    Listing,
    ListingChangeEvent,
    ListingChangeKind,
    ListingSnapshot,
    Source,
)

from uk_property_apify_shared.delta.snapshot import snapshot_from_listing
from uk_property_apify_shared.delta.sqlite_store import SqliteSnapshotStore
from uk_property_apify_shared.delta.store import SnapshotStore

__all__ = [
    "DEFAULT_FIREHOSE_LIMIT",
    "DELTA_STORE_PATH_ENV",
    "FirehoseInput",
    "FirehoseOutput",
    "WatchListingOutput",
    "WatchQueryOutput",
    "default_store_path",
    "ingest_listings",
    "load_firehose",
    "open_store",
]


DEFAULT_FIREHOSE_LIMIT = 100

DELTA_STORE_PATH_ENV = "UK_PROPERTY_DELTA_STORE_PATH"
"""Env var callers can set to override :func:`default_store_path` globally.

Useful for deployment targets (Docker, Smithery) where the default
``~/.uk-property-mcp`` location isn't writable or would be wiped on
container restart. Per-call ``store_path`` arguments always take
precedence over this env var.
"""


StatusTextFn = Callable[[Listing], str | None]


def default_store_path(source: Source) -> Path:
    """Return the default on-disk location for a portal's snapshot store.

    If :data:`DELTA_STORE_PATH_ENV` (``UK_PROPERTY_DELTA_STORE_PATH``) is
    set, it is used verbatim as the SQLite path — useful for deployment
    targets that need a writable scratch volume or a cross-portal shared
    store. Otherwise the default is ``~/.uk-property-mcp/<portal>.sqlite``.
    """
    override = os.environ.get(DELTA_STORE_PATH_ENV)
    if override:
        return Path(override)
    base = Path.home() / ".uk-property-mcp"
    return base / f"{source.value}.sqlite"


def open_store(store_path: str | None, source: Source) -> SnapshotStore:
    """Open a :class:`SnapshotStore` at the requested path (or the default)."""
    path = Path(store_path) if store_path else default_store_path(source)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSnapshotStore(path)


# ── Ingest (used by watch_listing + watch_query) ─────────────────────


async def ingest_listings(
    listings: Iterable[Listing],
    *,
    store: SnapshotStore,
    status_text_fn: StatusTextFn | None = None,
) -> tuple[list[ListingSnapshot], list[ListingChangeEvent]]:
    """Project each listing onto a snapshot and persist it.

    Returns ``(snapshots, events)`` so callers can surface both the
    canonical snapshot (what the listing looks like right now) and the
    events that fired relative to the prior snapshot.
    """
    snapshots: list[ListingSnapshot] = []
    events: list[ListingChangeEvent] = []
    for listing in listings:
        status = status_text_fn(listing) if status_text_fn else None
        snap = snapshot_from_listing(listing, status_text=status)
        snapshots.append(snap)
        events.extend(await store.put(snap))
    return snapshots, events


# ── Output models ────────────────────────────────────────────────────


class WatchListingOutput(BaseModel):
    """Output of ``watch_listing``."""

    model_config = ConfigDict(extra="forbid")

    source: Source
    source_id: str
    snapshot: ListingSnapshot
    events: list[ListingChangeEvent] = Field(
        default_factory=list,
        description="Change events fired by this ingest vs. the prior stored snapshot.",
    )


class WatchQueryOutput(BaseModel):
    """Output of ``watch_query``."""

    model_config = ConfigDict(extra="forbid")

    ingested: int = Field(..., description="Number of listings ingested in this run.")
    events: list[ListingChangeEvent] = Field(default_factory=list)
    kinds: dict[str, int] = Field(
        default_factory=dict,
        description="Aggregate count of emitted events keyed by ListingChangeKind value.",
    )


class FirehoseInput(BaseModel):
    """Shared input for the three firehose tools."""

    model_config = ConfigDict(extra="forbid")

    since: datetime | None = Field(
        None,
        description="ISO-8601 cutoff; only events detected at-or-after this time are returned.",
    )
    limit: int = Field(
        DEFAULT_FIREHOSE_LIMIT,
        ge=1,
        le=1000,
        description="Max number of events to return. Capped at 1000.",
    )
    store_path: str | None = Field(
        None,
        description=(
            "Override the snapshot store location. Defaults to "
            "``~/.uk-property-mcp/<portal>.sqlite``."
        ),
    )


class FirehoseOutput(BaseModel):
    """Shared output for the three firehose tools."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "price_reduced",
        "new",
        "back_on_market",
    ]
    events: list[ListingChangeEvent] = Field(default_factory=list)


# ── Firehose implementation ─────────────────────────────────────────


async def load_firehose(
    inp: FirehoseInput,
    *,
    source: Source,
    kinds: Iterable[ListingChangeKind],
) -> list[ListingChangeEvent]:
    """Read filtered events from the per-portal store."""
    store = open_store(inp.store_path, source)
    try:
        return await store.list_events(
            since=inp.since,
            source=source,
            kinds=kinds,
            limit=inp.limit,
        )
    finally:
        await store.close()
