"""Diff engine: compare two :class:`ListingSnapshot`s → :class:`ListingChangeEvent`s.

A single snapshot transition can produce multiple events (price
reduction *and* photo churn *and* description rewrite, say), so this
module returns a ``list[ListingChangeEvent]`` rather than a single
event. Each emitted event carries the same :class:`SnapshotDiff`, but
has its own ``kind`` so the firehose MCP tools
(``reductions_firehose``, ``new_listings_firehose``, ``back_on_market``)
can filter cleanly by ``ListingChangeKind``.

Status parsing is deliberately defensive: portals publish free-form
strings like ``"Sold STC"``, ``"Under Offer"``, ``"Reduced"``,
``"Available"``, with variable casing and punctuation. We classify
them into the canonical statuses the schema cares about
(``sold_stc`` / ``under_offer`` / ``available``) and fall back to the
raw string for the diff payload.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from uk_property_scrapers.schema import (
    ListingChangeEvent,
    ListingChangeKind,
    ListingSnapshot,
    SnapshotDiff,
)

__all__ = [
    "compute_diff",
    "compute_events",
    "derive_status",
]


_Status = Literal["available", "sold_stc", "under_offer", "unknown"]


_RE_SOLD_STC = re.compile(r"\b(sold\s*stc|sstc|sold\s+subject\s+to\s+contract)\b", re.IGNORECASE)
_RE_UNDER_OFFER = re.compile(r"\bunder\s*offer\b", re.IGNORECASE)
_RE_AVAILABLE = re.compile(
    r"\b(available|for\s+sale|new\s+instruction|back\s+on\s+(the\s+)?market|reduced)\b",
    re.IGNORECASE,
)


def derive_status(status_text: str | None) -> _Status:
    """Classify a free-form status ribbon into a canonical status."""
    if not status_text:
        return "unknown"
    if _RE_SOLD_STC.search(status_text):
        return "sold_stc"
    if _RE_UNDER_OFFER.search(status_text):
        return "under_offer"
    if _RE_AVAILABLE.search(status_text):
        return "available"
    return "unknown"


def _image_delta(
    before: Sequence[str], after: Sequence[str]
) -> tuple[int, set[str], set[str]]:
    """Return ``(count_delta, added, removed)`` image-fingerprint sets."""
    before_set = set(before)
    after_set = set(after)
    added = after_set - before_set
    removed = before_set - after_set
    return len(after_set) - len(before_set), added, removed


def compute_diff(before: ListingSnapshot, after: ListingSnapshot) -> SnapshotDiff:
    """Return a fine-grained :class:`SnapshotDiff` between two snapshots."""
    price_change_pence: int | None = None
    price_change_pct: float | None = None
    if before.price_pence is not None and after.price_pence is not None:
        price_change_pence = after.price_pence - before.price_pence
        if before.price_pence > 0:
            price_change_pct = price_change_pence / before.price_pence * 100.0

    before_features = set(before.features)
    after_features = set(after.features)
    added_features = sorted(after_features - before_features, key=lambda f: f.value)
    removed_features = sorted(before_features - after_features, key=lambda f: f.value)

    count_delta, _added_imgs, _removed_imgs = _image_delta(
        before.image_fingerprints, after.image_fingerprints
    )
    image_count_delta: int | None
    if before.image_fingerprints or after.image_fingerprints:
        image_count_delta = count_delta
    else:
        image_count_delta = None

    description_changed = before.description_fingerprint != after.description_fingerprint
    agent_changed = before.agent_source_id != after.agent_source_id

    before_status = derive_status(before.status_text)
    after_status = derive_status(after.status_text)
    status_changed = (before.status_text or "") != (after.status_text or "")

    field_changes: dict[str, tuple[str | None, str | None]] = {}
    for field in (
        "bedrooms",
        "bathrooms",
        "reception_rooms",
        "floor_area_sqft",
        "property_type",
        "tenure",
        "price_qualifier",
    ):
        b = getattr(before, field)
        a = getattr(after, field)
        if b != a:
            field_changes[field] = (None if b is None else str(b), None if a is None else str(a))
    if status_changed:
        field_changes["status"] = (before_status, after_status)
    if before.agent_source_id != after.agent_source_id:
        field_changes["agent_source_id"] = (before.agent_source_id, after.agent_source_id)

    return SnapshotDiff(
        price_change_pence=price_change_pence,
        price_change_pct=price_change_pct,
        added_features=added_features,
        removed_features=removed_features,
        image_count_delta=image_count_delta,
        description_changed=description_changed,
        agent_changed=agent_changed,
        status_changed=status_changed,
        field_changes=field_changes,
    )


def _events_from_transition(
    before: ListingSnapshot, after: ListingSnapshot, diff: SnapshotDiff
) -> list[ListingChangeKind]:
    """Return the list of :class:`ListingChangeKind` triggered by this transition."""
    kinds: list[ListingChangeKind] = []

    if diff.price_change_pence is not None:
        if diff.price_change_pence < 0:
            kinds.append(ListingChangeKind.PRICE_REDUCED)
        elif diff.price_change_pence > 0:
            kinds.append(ListingChangeKind.PRICE_INCREASED)

    before_status = derive_status(before.status_text)
    after_status = derive_status(after.status_text)
    if before_status != after_status:
        # These are NOT mutually exclusive: Sold STC → Under Offer fires
        # both UNDER_OFFER (the new state) and REMOVED_SOLD_STC (the prior
        # milestone that was reversed). Firehose watchers subscribe on the
        # kind they care about.
        if after_status == "sold_stc" and before_status != "sold_stc":
            kinds.append(ListingChangeKind.SOLD_STC)
        if after_status == "under_offer" and before_status != "under_offer":
            kinds.append(ListingChangeKind.UNDER_OFFER)
        if after_status == "available" and before_status in {"sold_stc", "under_offer"}:
            kinds.append(ListingChangeKind.BACK_ON_MARKET)
        if before_status == "sold_stc" and after_status != "sold_stc":
            kinds.append(ListingChangeKind.REMOVED_SOLD_STC)

    if diff.description_changed:
        kinds.append(ListingChangeKind.DESCRIPTION_CHANGED)

    if diff.image_count_delta is not None and diff.image_count_delta != 0:
        if diff.image_count_delta > 0:
            kinds.append(ListingChangeKind.PHOTOS_ADDED)
        else:
            kinds.append(ListingChangeKind.PHOTOS_REMOVED)

    if diff.agent_changed:
        kinds.append(ListingChangeKind.AGENT_CHANGED)

    if diff.added_features or diff.removed_features:
        kinds.append(ListingChangeKind.FEATURE_CHANGED)

    return kinds


def compute_events(
    before: ListingSnapshot | None,
    after: ListingSnapshot | None,
    *,
    detected_at: datetime | None = None,
) -> list[ListingChangeEvent]:
    """Return the full list of events fired by the ``before → after`` transition.

    ``before=None`` means we've never seen the listing before, so we emit a
    single ``NEW`` event. ``after=None`` means the listing has dropped off
    the source (usually a watch returning "not found") and we emit
    ``DELETED``. When both are present, we compute the structured diff and
    emit one event per triggered :class:`ListingChangeKind`.

    If neither snapshot has any material changes, we emit a single
    ``UNCHANGED`` event so polling watchers still get a "seen on" tick.
    """
    if before is None and after is None:
        return []

    now = detected_at or datetime.now(UTC)

    if before is None:
        assert after is not None  # for type-checker
        return [
            ListingChangeEvent(
                kind=ListingChangeKind.NEW,
                source=after.source,
                source_id=after.source_id,
                source_url=after.source_url,
                detected_at=now,
                before=None,
                after=after,
                diff=None,
            )
        ]

    if after is None:
        return [
            ListingChangeEvent(
                kind=ListingChangeKind.DELETED,
                source=before.source,
                source_id=before.source_id,
                source_url=before.source_url,
                detected_at=now,
                before=before,
                after=None,
                diff=None,
            )
        ]

    if before.fingerprint == after.fingerprint:
        return [
            ListingChangeEvent(
                kind=ListingChangeKind.UNCHANGED,
                source=after.source,
                source_id=after.source_id,
                source_url=after.source_url,
                detected_at=now,
                before=before,
                after=after,
                diff=None,
            )
        ]

    diff = compute_diff(before, after)
    kinds = _events_from_transition(before, after, diff)
    if not kinds:
        return [
            ListingChangeEvent(
                kind=ListingChangeKind.UNCHANGED,
                source=after.source,
                source_id=after.source_id,
                source_url=after.source_url,
                detected_at=now,
                before=before,
                after=after,
                diff=diff,
            )
        ]

    return [
        ListingChangeEvent(
            kind=kind,
            source=after.source,
            source_id=after.source_id,
            source_url=after.source_url,
            detected_at=now,
            before=before,
            after=after,
            diff=diff,
        )
        for kind in kinds
    ]
