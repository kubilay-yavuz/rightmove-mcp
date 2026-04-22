"""SQLite-backed :class:`SnapshotStore` implementation.

SQLite is the sweet spot for local watchers: single file, zero config,
good-enough concurrency via WAL for the modest write volume (~1 write
per listing per poll). This module wraps the stdlib :mod:`sqlite3`
module in an async-friendly facade — we offload the blocking I/O to a
thread pool so an MCP's event loop doesn't stall during a commit.

Schema:

    CREATE TABLE snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source          TEXT NOT NULL,
        source_id       TEXT NOT NULL,
        captured_at     TEXT NOT NULL,   -- ISO-8601, UTC
        fingerprint     TEXT NOT NULL,
        payload_json    TEXT NOT NULL    -- model_dump(mode="json")
    )
    CREATE INDEX ix_snapshots_listing
      ON snapshots (source, source_id, captured_at DESC)

    CREATE TABLE events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source          TEXT NOT NULL,
        source_id       TEXT NOT NULL,
        detected_at     TEXT NOT NULL,
        kind            TEXT NOT NULL,
        payload_json    TEXT NOT NULL
    )
    CREATE INDEX ix_events_detected ON events (detected_at DESC)
    CREATE INDEX ix_events_kind     ON events (kind, detected_at DESC)
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from uk_property_scrapers.schema import (
    ListingChangeEvent,
    ListingChangeKind,
    ListingSnapshot,
    Source,
)

from uk_property_apify_shared.delta.diff import compute_events

__all__ = ["SqliteSnapshotStore"]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    captured_at     TEXT NOT NULL,
    fingerprint     TEXT NOT NULL,
    payload_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snapshots_listing
  ON snapshots (source, source_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    detected_at     TEXT NOT NULL,
    kind            TEXT NOT NULL,
    payload_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_detected
  ON events (detected_at DESC);
CREATE INDEX IF NOT EXISTS ix_events_kind
  ON events (kind, detected_at DESC);
"""


def _snapshot_to_row(snapshot: ListingSnapshot) -> tuple[str, str, str, str, str]:
    payload = snapshot.model_dump(mode="json")
    return (
        snapshot.source.value,
        snapshot.source_id,
        snapshot.captured_at.isoformat(),
        snapshot.fingerprint,
        json.dumps(payload, separators=(",", ":")),
    )


def _row_to_snapshot(payload_json: str) -> ListingSnapshot:
    return ListingSnapshot.model_validate_json(payload_json)


def _event_to_row(event: ListingChangeEvent) -> tuple[str, str, str, str, str]:
    payload = event.model_dump(mode="json")
    return (
        event.source.value,
        event.source_id,
        event.detected_at.isoformat(),
        event.kind.value,
        json.dumps(payload, separators=(",", ":")),
    )


def _row_to_event(payload_json: str) -> ListingChangeEvent:
    return ListingChangeEvent.model_validate_json(payload_json)


class SqliteSnapshotStore:
    """Durable snapshot store backed by SQLite (WAL mode).

    Pass ``":memory:"`` for ephemeral test use or a :class:`Path` for a
    persistent store. The connection is held for the lifetime of the
    instance; call :meth:`close` (or use the :func:`open_sqlite_store`
    async context manager) to release it.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None

    # ── lifecycle ────────────────────────────────────────────────────
    async def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        def _open() -> sqlite3.Connection:
            conn = sqlite3.connect(
                self._path,
                isolation_level=None,  # autocommit; we manage transactions manually.
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            if self._path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)
            return conn

        self._conn = await asyncio.to_thread(_open)
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            conn = self._conn
            self._conn = None
            await asyncio.to_thread(conn.close)

    # ── writes ───────────────────────────────────────────────────────
    async def put(self, snapshot: ListingSnapshot) -> list[ListingChangeEvent]:
        async with self._lock:
            conn = await self._ensure_open()
            prior = await asyncio.to_thread(
                self._get_latest_sync, conn, snapshot.source.value, snapshot.source_id
            )
            events = compute_events(prior, snapshot)
            if prior is None or prior.fingerprint != snapshot.fingerprint:
                await asyncio.to_thread(self._insert_snapshot_sync, conn, snapshot)
            if events:
                await asyncio.to_thread(self._insert_events_sync, conn, events)
            return events

    async def mark_deleted(
        self,
        source: Source,
        source_id: str,
        *,
        detected_at: datetime | None = None,
    ) -> list[ListingChangeEvent]:
        async with self._lock:
            conn = await self._ensure_open()
            prior = await asyncio.to_thread(
                self._get_latest_sync, conn, source.value, source_id
            )
            if prior is None:
                return []
            events = compute_events(prior, None, detected_at=detected_at)
            await asyncio.to_thread(self._insert_events_sync, conn, events)
            return events

    # ── reads ────────────────────────────────────────────────────────
    async def get_latest(
        self, source: Source, source_id: str
    ) -> ListingSnapshot | None:
        conn = await self._ensure_open()
        return await asyncio.to_thread(
            self._get_latest_sync, conn, source.value, source_id
        )

    async def iter_snapshots(
        self,
        source: Source,
        source_id: str,
        *,
        limit: int | None = None,
    ) -> list[ListingSnapshot]:
        conn = await self._ensure_open()
        return await asyncio.to_thread(
            self._iter_snapshots_sync, conn, source.value, source_id, limit
        )

    async def list_events(
        self,
        *,
        since: datetime | None = None,
        source: Source | None = None,
        kinds: Iterable[ListingChangeKind] | None = None,
        limit: int | None = None,
    ) -> list[ListingChangeEvent]:
        conn = await self._ensure_open()
        kinds_list = [k.value for k in kinds] if kinds is not None else None
        return await asyncio.to_thread(
            self._list_events_sync,
            conn,
            since,
            source.value if source is not None else None,
            kinds_list,
            limit,
        )

    # ── sync helpers (run on thread pool) ───────────────────────────
    @staticmethod
    def _get_latest_sync(
        conn: sqlite3.Connection, source: str, source_id: str
    ) -> ListingSnapshot | None:
        row = conn.execute(
            "SELECT payload_json FROM snapshots "
            "WHERE source = ? AND source_id = ? "
            "ORDER BY captured_at DESC, id DESC LIMIT 1",
            (source, source_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_snapshot(row["payload_json"])

    @staticmethod
    def _insert_snapshot_sync(
        conn: sqlite3.Connection, snapshot: ListingSnapshot
    ) -> None:
        conn.execute(
            "INSERT INTO snapshots "
            "(source, source_id, captured_at, fingerprint, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            _snapshot_to_row(snapshot),
        )

    @staticmethod
    def _insert_events_sync(
        conn: sqlite3.Connection, events: list[ListingChangeEvent]
    ) -> None:
        conn.executemany(
            "INSERT INTO events "
            "(source, source_id, detected_at, kind, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            [_event_to_row(e) for e in events],
        )

    @staticmethod
    def _iter_snapshots_sync(
        conn: sqlite3.Connection,
        source: str,
        source_id: str,
        limit: int | None,
    ) -> list[ListingSnapshot]:
        sql = (
            "SELECT payload_json FROM snapshots "
            "WHERE source = ? AND source_id = ? "
            "ORDER BY captured_at DESC, id DESC"
        )
        params: list[Any] = [source, source_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row_to_snapshot(r["payload_json"]) for r in conn.execute(sql, params)]

    @staticmethod
    def _list_events_sync(
        conn: sqlite3.Connection,
        since: datetime | None,
        source: str | None,
        kinds: list[str] | None,
        limit: int | None,
    ) -> list[ListingChangeEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("detected_at >= ?")
            params.append(since.isoformat())
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)

        sql = "SELECT payload_json FROM events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY detected_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        return [_row_to_event(r["payload_json"]) for r in conn.execute(sql, params)]


@asynccontextmanager
async def open_sqlite_store(path: str | Path):
    """Async context manager that opens + closes a :class:`SqliteSnapshotStore`."""
    store = SqliteSnapshotStore(path)
    try:
        yield store
    finally:
        await store.close()
