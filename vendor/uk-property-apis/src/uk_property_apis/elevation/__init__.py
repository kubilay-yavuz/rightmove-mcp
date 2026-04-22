"""Open-Meteo elevation API client.

Exposes :class:`ElevationClient` (live HTTP client) and a standalone
``elevation_at`` convenience wrapper that opens / closes the client in
a single call. See :mod:`uk_property_apis.elevation.client` for the
rationale behind picking Open-Meteo over OS Terrain 50 / Open-Elevation.
"""

from __future__ import annotations

from uk_property_apis.elevation.client import (
    ElevationClient,
    elevation_at,
    elevations_at,
)
from uk_property_apis.elevation.models import ElevationPoint

__all__ = [
    "ElevationClient",
    "ElevationPoint",
    "elevation_at",
    "elevations_at",
]
