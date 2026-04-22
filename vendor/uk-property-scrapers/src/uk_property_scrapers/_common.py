"""Cross-parser helpers for coord extraction and floorplan classification.

These utilities stay deliberately dumb and site-agnostic: they operate on a
raw HTML string and return primitive types so each portal parser can adapt
the result to its own data shapes. All three listing portals embed precise
lat/lng coordinates in their hydration payloads, and all three emit image
URLs that can be cheaply classified as photo vs floorplan by URL-path
substring — this module centralises those two operations so every parser
behaves identically and drift in one place fans out to all of them.

The regexes here operate on raw HTML — not parsed DOM — because the
coordinate payloads live inside ``<script>`` blobs (Next.js RSC streams,
inline ``window.__NEXT_DATA__``) that selectolax does not normalise and
that would otherwise require parsing the script body as a JSON dialect.
"""

from __future__ import annotations

import re
from typing import Final

from uk_property_scrapers.schema import LatLng

# ── Coordinate extraction ───────────────────────────────────────────────────

_FLOAT = r"-?\d+(?:\.\d+)?"

# Match both Zoopla/Rightmove shape (``"latitude":X,"longitude":Y``) and
# OnTheMarket's transposed ``"location":{"lon":Y,"lat":X}`` shape.
# The ``longitude``/``lat`` keys may appear first or second so we try both
# orders. The capturing order always returns (lat, lng).
_COORD_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # "latitude":52.218,"longitude":-0.078
    re.compile(
        rf'"latitude"\s*:\s*(?P<lat>{_FLOAT})\s*,\s*"longitude"\s*:\s*(?P<lng>{_FLOAT})'
    ),
    # "longitude":-0.078,"latitude":52.218
    re.compile(
        rf'"longitude"\s*:\s*(?P<lng>{_FLOAT})\s*,\s*"latitude"\s*:\s*(?P<lat>{_FLOAT})'
    ),
    # "lat":52.218,"lon":-0.078 or "lat":52.218,"lng":-0.078 (OTM / API payloads)
    re.compile(
        rf'"lat"\s*:\s*(?P<lat>{_FLOAT})\s*,\s*"lo[nr]g?"\s*:\s*(?P<lng>{_FLOAT})'
    ),
    # "lon":-0.078,"lat":52.218 (OTM transposed)
    re.compile(
        rf'"lo[nr]g?"\s*:\s*(?P<lng>{_FLOAT})\s*,\s*"lat"\s*:\s*(?P<lat>{_FLOAT})'
    ),
)

# UK bounding box (roughly) - north 61 includes Shetland, south 49 includes
# Jersey/Scilly, west -8.8 includes Northern Ireland, east 2.0 includes Kent
# / Norfolk. We reject coords outside this because upstream pages sometimes
# embed coords for agent offices in other European countries or for generic
# placeholders (0,0, 51.5074/-0.1278 London office defaults, etc.).
_UK_LAT_RANGE: Final = (49.0, 61.0)
_UK_LNG_RANGE: Final = (-8.8, 2.1)


def extract_uk_coords(html: str) -> LatLng | None:
    """Pull the first UK-bounded ``(lat, lng)`` pair out of a hydration payload.

    Zoopla and Rightmove emit their coords inside Next.js RSC script chunks
    where the payload is JSON-encoded and then quoted as a JS string, so
    keys appear as ``\\"latitude\\"`` rather than ``"latitude"``. We scan
    both forms so we don't need a site-specific unescape step.

    Returns ``None`` when no coordinate pair is found, when the first pair
    falls outside the UK bounding box, or when the numeric values fail
    :class:`LatLng` validation (eg. ``(0.0, 0.0)`` placeholder).
    """
    haystacks = (html, html.replace('\\"', '"'))
    for haystack in haystacks:
        for pattern in _COORD_PATTERNS:
            for match in pattern.finditer(haystack):
                try:
                    lat = float(match.group("lat"))
                    lng = float(match.group("lng"))
                except (TypeError, ValueError):
                    continue
                if not (_UK_LAT_RANGE[0] <= lat <= _UK_LAT_RANGE[1]):
                    continue
                if not (_UK_LNG_RANGE[0] <= lng <= _UK_LNG_RANGE[1]):
                    continue
                if lat == 0.0 and lng == 0.0:
                    continue
                try:
                    return LatLng(lat=lat, lng=lng)
                except ValueError:
                    continue
    return None


# ── Floorplan classification ────────────────────────────────────────────────

# Each portal exposes floorplan images under a recognisable URL shape:
#   * Zoopla   lc.zoocdn.com/... served from the ``floorPlan`` payload block
#              (we tag at the payload level, not from URL shape alone).
#   * Rightmove ``media.rightmove.co.uk/.../FLP_00_IMG_...jpeg``
#   * OnTheMarket ``media.onthemarket.com/properties/.../floor-plan-<n>-<size>.jpg``
# The URL-based classifier below handles Rightmove + OnTheMarket directly;
# Zoopla's ``extract_zoopla_floorplan_urls`` (defined in the zoopla parser)
# cooperates with this helper by passing through its payload discoveries.
_FLOORPLAN_URL_TOKENS: Final[tuple[str, ...]] = (
    "floor-plan",
    "floorplan",
    "/flp_",
    "_flp_",
    "/FLP_",
    "_FLP_",
)


def is_floorplan_url(url: str | None) -> bool:
    """Best-effort URL-shape classification: does this look like a floorplan?

    Deliberately over-triggers rather than under-triggers — a mis-tagged
    photo downstream is easy to untag, a missed floorplan pollutes hero-image
    previews permanently. Callers that have authoritative payload-level
    knowledge (``"floorPlan":[…]``) should use that directly and only fall
    back to this helper for URLs not seen in the payload.
    """
    if not url:
        return False
    lowered = url.lower()
    return any(token.lower() in lowered for token in _FLOORPLAN_URL_TOKENS)


FLOORPLAN_CAPTION: Final = "floorplan"
"""Canonical :attr:`Image.caption` value we set on floorplan entries.

Consumers downstream filter on ``caption == "floorplan"`` to separate hero
photos from floorplans in galleries. Kept as a constant so renamers stay
consistent across parsers.
"""


__all__ = [
    "FLOORPLAN_CAPTION",
    "extract_uk_coords",
    "is_floorplan_url",
]
