"""Stable content hashing for the delta pipeline.

Fingerprints need two properties:

1. **Stable** — the same logical input produces the same hash across
   Python processes, runs, and package versions. No Python-object
   identity leaks; no pickle.
2. **Sensitive** — any change that a human would call "a real change"
   shifts the hash. Whitespace and casing in prose descriptions does
   not usually count, so we canonicalize text before hashing.

The functions here are intentionally pure and side-effect free so the
delta store can call them during an async SQLite write without having
to spawn threads.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

__all__ = [
    "fingerprint_description",
    "fingerprint_image_url",
    "fingerprint_payload",
    "stable_hash",
]


_WS_RUN = re.compile(r"\s+")


def stable_hash(value: str) -> str:
    """Return a short deterministic hex digest for ``value``.

    Truncated SHA-256 is collision-resistant enough for snapshot-level
    change detection and keeps per-row overhead small in SQLite.
    """

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _canonicalize_text(text: str) -> str:
    """Normalize prose so trivial whitespace/casing noise doesn't churn fingerprints."""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\xa0", " ").replace("\u200b", "")
    collapsed = _WS_RUN.sub(" ", normalized).strip()
    return collapsed.lower()


def fingerprint_description(description: str | None) -> str | None:
    """Hash a listing description; ``None`` → ``None``.

    Handles the nbsp/zero-width-space sprinkling portals use for
    tracking and the leading/trailing whitespace from ``<br>`` → ``\\n``
    conversions.
    """
    if description is None:
        return None
    text = _canonicalize_text(description)
    if not text:
        return None
    return stable_hash(text)


def fingerprint_image_url(url: str) -> str:
    """Hash an image URL, stripping the query string.

    Portals serve the same image under the same path with different
    cache-busting / resize query strings — those should not count as a
    "new photo".
    """
    base = url.split("?", 1)[0].rstrip("/")
    return stable_hash(base)


def fingerprint_payload(payload: dict[str, Any]) -> str:
    """Hash a canonical JSON payload with sorted keys.

    Used by :class:`~uk_property_apify_shared.delta.snapshot.ListingSnapshot`
    to produce the snapshot-level fingerprint.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return stable_hash(canonical)
