"""Async client for the DEFRA UK-AIR Sensor Observation Service (JSON API).

The OGC SOS service at ``https://uk-air.defra.gov.uk/sos-ukair/api/v1/``
ships a compact JSON REST layer with three endpoints we care about:

* ``/timeseries/`` — active-timeseries index (~1000 rows, one per
  pollutant-at-site combination). Every record carries the
  timeseries id (``id``), unit (``uom``), and the parent station's
  coordinates + label. This is our primary list endpoint because the
  timeseries id is the handle the ``getData`` call needs.
* ``/timeseries/{id}/getData`` — raw measurements as ``{timestamp,
  value}`` pairs (``timestamp`` is epoch-ms, ``value`` in the native
  pollutant unit returned by ``uom``).
* ``/stations/`` — a superset list including historical /
  decommissioned stations that no longer have a timeseries. We
  deliberately don't use it as the primary feed because
  ``getData`` 404s on stations with no active timeseries.

The full timeseries index is stable across requests — we fetch it
once per client instance and keep it in memory. Proximity search is
done locally via haversine so we don't hammer DEFRA with repeated
identical list calls on fan-out.

Pollutant units come straight from DEFRA's ``uom`` field; we expose
them on :class:`AirQualityStation` so callers can trust them without
hard-coding the conventional mapping.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.airquality.models import (
    AirQualityReading,
    AirQualityStation,
    Pollutant,
    StationProximity,
)

_BASE_URL = "https://uk-air.defra.gov.uk/sos-ukair/api/v1/"

# DEFRA's "regulatory / headline" pollutant set — the ones that
# actually drive the Daily Air Quality Index. Useful default for
# callers that just want the common pack without enumerating 200+
# speciated VOC timeseries we don't benchmark against.
DEFAULT_POLLUTANTS: tuple[Pollutant, ...] = (
    "pm25",
    "pm10",
    "no2",
    "o3",
    "so2",
    "co",
)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two WGS-84 points."""

    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


class DefraAirQualityClient(BaseAPIClient):
    """Client for DEFRA UK-AIR SOS JSON endpoints."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url=_BASE_URL,
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )
        self._stations_cache: list[AirQualityStation] | None = None
        self._stations_lock = asyncio.Lock()

    async def list_stations(
        self,
        *,
        force_refresh: bool = False,
    ) -> list[AirQualityStation]:
        """Return (and cache) every DEFRA timeseries / pollutant combo.

        Uses ``/timeseries/`` as the underlying feed — each record is
        an active timeseries, so every returned
        :class:`AirQualityStation` has a ``timeseries_id`` guaranteed
        to be queryable via :meth:`latest_value`. The upstream list is
        ~1000 rows and ~380 KB; caching inside a client instance is
        safe because DEFRA regenerates it infrequently.
        """

        if self._stations_cache is not None and not force_refresh:
            return self._stations_cache
        async with self._stations_lock:
            if self._stations_cache is not None and not force_refresh:
                return self._stations_cache
            raw = await self._request_json("GET", "timeseries/")
            if not isinstance(raw, list):
                raise RuntimeError(
                    "DEFRA UK-AIR /timeseries/ did not return a list",
                )
            stations: list[AirQualityStation] = []
            for feature in raw:
                if not isinstance(feature, dict):
                    continue
                station = AirQualityStation.from_timeseries_feature(feature)
                if station is None or station.lat is None or station.lng is None:
                    continue
                stations.append(station)
            self._stations_cache = stations
            return stations

    async def stations_near(
        self,
        lat: float,
        lng: float,
        *,
        radius_km: float = 5.0,
        pollutants: Iterable[Pollutant] | None = None,
        max_stations: int | None = None,
    ) -> list[StationProximity]:
        """Return stations within ``radius_km`` of (lat, lng), sorted by distance.

        ``pollutants`` filters to a canonical set (defaults to all).
        ``max_stations`` caps the return length after distance sort —
        combined with the proximity sort this cheaply gives you 'the
        N closest NO2 sensors' without enumerating 200+ VOC timeseries.
        """

        if radius_km <= 0:
            raise ValueError(f"radius_km must be > 0 (got {radius_km})")
        pollutant_filter: set[Pollutant] | None = (
            set(pollutants) if pollutants is not None else None
        )
        stations = await self.list_stations()
        hits: list[StationProximity] = []
        for station in stations:
            if station.lat is None or station.lng is None:
                continue
            if pollutant_filter is not None and station.pollutant not in pollutant_filter:
                continue
            distance = _haversine_km(lat, lng, station.lat, station.lng)
            if distance > radius_km:
                continue
            hits.append(StationProximity(station=station, distance_km=distance))
        hits.sort(key=lambda p: p.distance_km)
        if max_stations is not None and max_stations > 0:
            hits = hits[:max_stations]
        return hits

    async def latest_value(
        self,
        timeseries_id: str,
        *,
        window_hours: int = 24,
        now: datetime | None = None,
    ) -> tuple[int | None, float | None]:
        """Return the latest ``(timestamp_ms, value)`` pair for a timeseries.

        The DEFRA ``/getData`` endpoint expects an ISO-8601 duration
        suffix (``PT24H``) and an upper-bound timestamp. We pick 'now'
        (UTC) by default and return the final non-null pair in the
        response. When the window is empty we return ``(None, None)``.
        """

        if window_hours <= 0:
            raise ValueError(f"window_hours must be > 0 (got {window_hours})")
        upper = (now or datetime.now(tz=UTC)).replace(microsecond=0)
        timespan = f"PT{window_hours}H/{upper.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        data = await self._get(
            f"timeseries/{timeseries_id}/getData",
            params={"timespan": timespan},
        )
        values = data.get("values") or []
        if not isinstance(values, list) or not values:
            return None, None
        for record in reversed(values):
            if not isinstance(record, dict):
                continue
            val = record.get("value")
            ts = record.get("timestamp")
            if val is None or ts is None:
                continue
            try:
                ts_int = int(ts)
            except (TypeError, ValueError):
                continue
            try:
                v_float = float(val)
            except (TypeError, ValueError):
                continue
            return ts_int, v_float
        return None, None

    async def latest_nearby(
        self,
        lat: float,
        lng: float,
        *,
        radius_km: float = 5.0,
        pollutants: Iterable[Pollutant] | None = None,
        max_stations: int = 12,
        window_hours: int = 24,
        max_concurrency: int = 4,
    ) -> list[AirQualityReading]:
        """Fetch the latest reading per nearby station / pollutant combo.

        Combines :meth:`stations_near` + :meth:`latest_value` with an
        in-flight cap so one climate-risk point doesn't fan out 50+
        concurrent ``getData`` calls. Stations whose timeseries is
        empty in the window are dropped (no row emitted) rather than
        reported as ``value=None``.
        """

        selected = pollutants if pollutants is not None else DEFAULT_POLLUTANTS
        nearby = await self.stations_near(
            lat,
            lng,
            radius_km=radius_km,
            pollutants=selected,
            max_stations=max_stations,
        )
        if not nearby:
            return []
        sem = asyncio.Semaphore(max(1, max_concurrency))

        async def _fetch(proximity: StationProximity) -> AirQualityReading | None:
            async with sem:
                try:
                    ts_ms, value = await self.latest_value(
                        proximity.station.timeseries_id, window_hours=window_hours
                    )
                except Exception:
                    return None
            if value is None:
                return None
            station = proximity.station
            return AirQualityReading(
                timeseries_id=station.timeseries_id,
                site_name=station.site_name,
                pollutant=station.pollutant,
                pollutant_raw=station.pollutant_raw,
                timestamp_ms=ts_ms,
                value=value,
                unit=station.unit,
                lat=station.lat,
                lng=station.lng,
            )

        raw = await asyncio.gather(*(_fetch(p) for p in nearby))
        return [r for r in raw if r is not None]


async def latest_nearby(
    lat: float,
    lng: float,
    *,
    radius_km: float = 5.0,
    pollutants: Iterable[Pollutant] | None = None,
    max_stations: int = 12,
    window_hours: int = 24,
) -> list[AirQualityReading]:
    """Convenience wrapper that opens a one-shot client."""

    async with DefraAirQualityClient() as client:
        return await client.latest_nearby(
            lat,
            lng,
            radius_km=radius_km,
            pollutants=pollutants,
            max_stations=max_stations,
            window_hours=window_hours,
        )


__all__ = [
    "DEFAULT_POLLUTANTS",
    "DefraAirQualityClient",
    "latest_nearby",
]
