"""Delta pipeline primitives: snapshot → diff → event + pluggable store.

The delta layer powers the MCP firehose tools (``watch_query``,
``watch_listing``, ``reductions_firehose``, ``new_listings_firehose``,
``back_on_market``). Architecturally it has three layers:

1. **Fingerprint helpers** (:mod:`.fingerprint`) — stable content
   hashing that ignores cache-busting query strings and whitespace.
2. **Snapshot + diff** (:mod:`.snapshot`, :mod:`.diff`) — pure
   functions that convert :class:`~uk_property_scrapers.schema.Listing`
   into :class:`~uk_property_scrapers.schema.ListingSnapshot`, and
   compare two snapshots into one or more
   :class:`~uk_property_scrapers.schema.ListingChangeEvent` objects.
3. **Store** (:mod:`.store`, :mod:`.sqlite_store`) — a pluggable async
   :class:`SnapshotStore` protocol with an :class:`InMemorySnapshotStore`
   for tests and a :class:`SqliteSnapshotStore` backed by a WAL-mode
   SQLite file for production watchers.

Typical MCP flow::

    store = SqliteSnapshotStore("~/.uk-property-intel/watch.sqlite")
    snapshot = snapshot_from_listing(listing, status_text=raw_status)
    events = await store.put(snapshot)   # → [PRICE_REDUCED, PHOTOS_ADDED]
"""

from __future__ import annotations

from uk_property_apify_shared.delta.diff import (
    compute_diff,
    compute_events,
    derive_status,
)
from uk_property_apify_shared.delta.fingerprint import (
    fingerprint_description,
    fingerprint_image_url,
    fingerprint_payload,
    stable_hash,
)
from uk_property_apify_shared.delta.mcp import (
    DEFAULT_FIREHOSE_LIMIT,
    DELTA_STORE_PATH_ENV,
    FirehoseInput,
    FirehoseOutput,
    WatchListingOutput,
    WatchQueryOutput,
    default_store_path,
    ingest_listings,
    load_firehose,
    open_store,
)
from uk_property_apify_shared.delta.snapshot import snapshot_from_listing
from uk_property_apify_shared.delta.sqlite_store import (
    SqliteSnapshotStore,
    open_sqlite_store,
)
from uk_property_apify_shared.delta.store import (
    InMemorySnapshotStore,
    SnapshotStore,
)

__all__ = [
    "DEFAULT_FIREHOSE_LIMIT",
    "DELTA_STORE_PATH_ENV",
    "FirehoseInput",
    "FirehoseOutput",
    "InMemorySnapshotStore",
    "SnapshotStore",
    "SqliteSnapshotStore",
    "WatchListingOutput",
    "WatchQueryOutput",
    "compute_diff",
    "compute_events",
    "default_store_path",
    "derive_status",
    "fingerprint_description",
    "fingerprint_image_url",
    "fingerprint_payload",
    "ingest_listings",
    "load_firehose",
    "open_sqlite_store",
    "open_store",
    "snapshot_from_listing",
    "stable_hash",
]
