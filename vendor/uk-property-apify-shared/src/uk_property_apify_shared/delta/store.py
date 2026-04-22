"""Pluggable snapshot-store backend for the delta pipeline.

The concrete :mod:`~uk_property_apify_shared.delta.sqlite_store` is the
shipped default, but watchers are interface-agnostic: anything matching
the :class:`SnapshotStore` protocol (Postgres, DuckDB, an in-memory dict
for tests) plugs in without changes on the MCP side. Keeping the
protocol tight — six async methods, no pagination iterator
abstractions — keeps that portability cheap.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, runtime_checkable

from uk_property_scrapers.schema import (
    ListingChangeEvent,
    ListingChangeKind,
    ListingSnapshot,
    Source,
)

__all__ = [
    "InMemorySnapshotStore",
    "SnapshotStore",
]


@runtime_checkable
class SnapshotStore(Protocol):
    """Durable per-listing snapshot history + derived change-event log.

    Implementations MUST be idempotent on ``put``: inserting a snapshot
    whose fingerprint matches the most recent stored snapshot for the
    same listing is a no-op and returns an ``[UNCHANGED]`` event
    (caller can still record a "last-seen" tick).
    """

    async def put(self, snapshot: ListingSnapshot) -> list[ListingChangeEvent]:
        """Persist ``snapshot``; return change events vs. the prior snapshot.

        Returned events must also be persisted so that
        :meth:`list_events` can replay them later.
        """

    async def mark_deleted(
        self,
        source: Source,
        source_id: str,
        *,
        detected_at: datetime | None = None,
    ) -> list[ListingChangeEvent]:
        """Record that a previously-seen listing is no longer on the source.

        If there is no prior snapshot this is a no-op.
        """

    async def get_latest(
        self, source: Source, source_id: str
    ) -> ListingSnapshot | None:
        """Return the most recent snapshot for a listing, or ``None``."""

    async def iter_snapshots(
        self,
        source: Source,
        source_id: str,
        *,
        limit: int | None = None,
    ) -> list[ListingSnapshot]:
        """Return snapshots for a single listing, most-recent-first."""

    async def list_events(
        self,
        *,
        since: datetime | None = None,
        source: Source | None = None,
        kinds: Iterable[ListingChangeKind] | None = None,
        limit: int | None = None,
    ) -> list[ListingChangeEvent]:
        """Return persisted change events filtered by cutoff / source / kind."""

    async def close(self) -> None:
        """Release underlying resources (file handles, connections)."""


class InMemorySnapshotStore:
    """Tiny in-process :class:`SnapshotStore` for tests and small watchers.

    No persistence, no concurrency story — just enough to exercise the
    diff engine end-to-end without spinning up SQLite. The
    :mod:`~uk_property_apify_shared.delta.sqlite_store` is what you
    want for anything durable.
    """

    def __init__(self) -> None:
        self._snapshots: dict[tuple[Source, str], list[ListingSnapshot]] = {}
        self._events: list[ListingChangeEvent] = []

    async def put(self, snapshot: ListingSnapshot) -> list[ListingChangeEvent]:
        from uk_property_apify_shared.delta.diff import compute_events

        key = (snapshot.source, snapshot.source_id)
        prior = self._snapshots.get(key, [])
        before = prior[-1] if prior else None
        events = compute_events(before, snapshot)
        if before is None or before.fingerprint != snapshot.fingerprint:
            self._snapshots.setdefault(key, []).append(snapshot)
        self._events.extend(events)
        return events

    async def mark_deleted(
        self,
        source: Source,
        source_id: str,
        *,
        detected_at: datetime | None = None,
    ) -> list[ListingChangeEvent]:
        from uk_property_apify_shared.delta.diff import compute_events

        key = (source, source_id)
        prior = self._snapshots.get(key, [])
        if not prior:
            return []
        events = compute_events(prior[-1], None, detected_at=detected_at)
        self._events.extend(events)
        return events

    async def get_latest(
        self, source: Source, source_id: str
    ) -> ListingSnapshot | None:
        history = self._snapshots.get((source, source_id))
        if not history:
            return None
        return history[-1]

    async def iter_snapshots(
        self,
        source: Source,
        source_id: str,
        *,
        limit: int | None = None,
    ) -> list[ListingSnapshot]:
        history = list(reversed(self._snapshots.get((source, source_id), [])))
        if limit is not None:
            history = history[:limit]
        return history

    async def list_events(
        self,
        *,
        since: datetime | None = None,
        source: Source | None = None,
        kinds: Iterable[ListingChangeKind] | None = None,
        limit: int | None = None,
    ) -> list[ListingChangeEvent]:
        wanted_kinds = set(kinds) if kinds is not None else None
        filtered = [
            e
            for e in self._events
            if (since is None or e.detected_at >= since)
            and (source is None or e.source == source)
            and (wanted_kinds is None or e.kind in wanted_kinds)
        ]
        filtered.sort(key=lambda e: e.detected_at, reverse=True)
        if limit is not None:
            filtered = filtered[:limit]
        return filtered

    async def close(self) -> None:  # pragma: no cover - trivial
        self._snapshots.clear()
        self._events.clear()
