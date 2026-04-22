"""Async client for the Open-Meteo elevation API.

`Open-Meteo <https://open-meteo.com/en/docs/elevation-api>`_ exposes
a no-auth elevation endpoint backed by the Copernicus GLO-90 DEM
(processed by Open-Meteo), which has **global** coverage (including
the full UK coastline) at ~90 m native resolution. Compared to the
alternatives:

* **OS DataHub / OS Terrain 50** would give us a UK-native DEM at
  50 m, but needs an API key + the free tier ships behind tiered
  licences. Overkill for a 1-significant-figure elevation read.
* **Open-Elevation** (open-elevation.com) is a community-hosted
  service that frequently rate-limits / 502s; we'd end up having to
  maintain a failover.

Open-Meteo is free, key-less, and stable — documented rate limit is
10k requests/day for the default endpoint, well above anything an
Apify actor is going to do from a single workspace. The client uses
the same retry/timeout/semaphore machinery as every other
:mod:`uk_property_apis` client so the consumer doesn't need to know
which provider is under the hood.

The endpoint accepts batched lat/lng via comma-separated lists, which
we exercise in :meth:`ElevationClient.elevations_at` to avoid a
per-point round-trip.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.elevation.models import ElevationPoint

_BASE_URL = "https://api.open-meteo.com/v1/"
_ENDPOINT = "elevation"


class ElevationClient(BaseAPIClient):
    """Async elevation client backed by Open-Meteo."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
        source_tag: str = "open-meteo",
    ) -> None:
        super().__init__(
            base_url=_BASE_URL,
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )
        self._source_tag = source_tag

    async def elevation_at(self, lat: float, lng: float) -> ElevationPoint:
        """Return the elevation of a single WGS-84 point."""

        data = await self._get(
            _ENDPOINT,
            params={
                "latitude": f"{lat:.6f}",
                "longitude": f"{lng:.6f}",
            },
        )
        elevations = _coerce_elevations(data)
        value = elevations[0] if elevations else None
        return ElevationPoint(
            lat=lat,
            lng=lng,
            elevation_m=value,
            source=self._source_tag,
        )

    async def elevations_at(
        self,
        points: Iterable[tuple[float, float]],
    ) -> list[ElevationPoint]:
        """Return elevations for a batch of points in one round-trip.

        Open-Meteo accepts comma-separated latitude + longitude query
        params and returns the elevations in order. Order is preserved
        so the returned list index-aligns with the input iterable.
        """

        coords = [(float(lat), float(lng)) for lat, lng in points]
        if not coords:
            return []
        data = await self._get(
            _ENDPOINT,
            params={
                "latitude": ",".join(f"{p[0]:.6f}" for p in coords),
                "longitude": ",".join(f"{p[1]:.6f}" for p in coords),
            },
        )
        elevations = _coerce_elevations(data)
        if len(elevations) < len(coords):
            elevations = list(elevations) + [None] * (len(coords) - len(elevations))
        return [
            ElevationPoint(
                lat=lat,
                lng=lng,
                elevation_m=value,
                source=self._source_tag,
            )
            for (lat, lng), value in zip(coords, elevations[: len(coords)], strict=True)
        ]


def _coerce_elevations(payload: object) -> list[float | None]:
    """Normalise Open-Meteo's slightly permissive response into a flat list."""

    if not isinstance(payload, dict):
        return []
    raw = payload.get("elevation")
    if raw is None:
        return []
    if isinstance(raw, (int, float)):
        return [float(raw)]
    if isinstance(raw, list):
        out: list[float | None] = []
        for item in raw:
            if item is None:
                out.append(None)
            elif isinstance(item, (int, float)):
                out.append(float(item))
            else:
                out.append(None)
        return out
    return []


async def elevation_at(lat: float, lng: float) -> ElevationPoint:
    """Convenience wrapper around :meth:`ElevationClient.elevation_at`."""

    async with ElevationClient() as client:
        return await client.elevation_at(lat, lng)


async def elevations_at(
    points: Iterable[tuple[float, float]],
) -> list[ElevationPoint]:
    """Convenience wrapper around :meth:`ElevationClient.elevations_at`."""

    async with ElevationClient() as client:
        return await client.elevations_at(points)


__all__ = [
    "ElevationClient",
    "elevation_at",
    "elevations_at",
]
