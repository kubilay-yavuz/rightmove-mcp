"""Async client for BGS geohazard ArcGIS REST endpoints (post-2024 reshape).

Background
==========

BGS retired the ``BGS_Hazards/GeoHazardsEngland`` REST service in 2024.
The six A–E graded subsidence hazards (shrink-swell, ground dissolution,
compressible ground, landslide, collapsible deposits, running sand) that
powered the previous client are now either paywalled behind the BGS Data
subscription feed or only ship as WMS raster tiles. The intersection of
"still free + still spatially queryable + still directly relevant to
UK property risk" is:

1. ``GeoIndex_Onshore/geoclimate_basic/MapServer/{0,1,2}`` — GeoClimate
   Basic v1 shrink-swell susceptibility at decade horizons 2030, 2050,
   and 2080 under an averaged climate projection. Values collapse to
   ``{None, Possible, Probable}`` per polygon.
2. ``GeoIndex_Onshore/geoclimate_basic/MapServer/{3,4}`` — the matching
   GeoClimate UKCP18 shrink-swell susceptibility at 2030 and 2070 under
   the UKCP18 probabilistic ensemble.
3. ``GeoIndex_Onshore/hazards/MapServer/2`` — the open landslide
   inventory, a point feature class of historical landslide events.

The client speaks the ArcGIS REST ``query`` API directly (``inSR=4326``,
``spatialRel=esriSpatialRelIntersects`` for polygon lookups,
``distance`` + ``units=esriSRUnit_Meter`` for point buffers). Distances
for landslide events are back-computed with the Haversine formula so
downstream consumers get a post-filtered ``distance_km`` on each
:class:`~uk_property_apis.bgs.models.LandslideEvent`.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from typing import Any

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.bgs.models import (
    ClimateProjection,
    GeohazardAssessment,
    LandslideEvent,
    ShrinkSwellAssessment,
    ShrinkSwellClass,
    ShrinkSwellHorizon,
)

_BASE_URL = "https://map.bgs.ac.uk/arcgis/rest/services/"


_SHRINK_SWELL_LAYERS: dict[tuple[ClimateProjection, ShrinkSwellHorizon], str] = {
    (ClimateProjection.BASIC, ShrinkSwellHorizon.H_2030): "GeoIndex_Onshore/geoclimate_basic/MapServer/0",
    (ClimateProjection.BASIC, ShrinkSwellHorizon.H_2050): "GeoIndex_Onshore/geoclimate_basic/MapServer/1",
    (ClimateProjection.BASIC, ShrinkSwellHorizon.H_2080): "GeoIndex_Onshore/geoclimate_basic/MapServer/2",
    (ClimateProjection.UKCP18, ShrinkSwellHorizon.H_2030): "GeoIndex_Onshore/geoclimate_basic/MapServer/3",
    (ClimateProjection.UKCP18, ShrinkSwellHorizon.H_2070): "GeoIndex_Onshore/geoclimate_basic/MapServer/4",
}

_LANDSLIDE_LAYER = "GeoIndex_Onshore/hazards/MapServer/2"

_RISK_CLASSES = {ShrinkSwellClass.POSSIBLE, ShrinkSwellClass.PROBABLE}


def _point_intersect_params(lat: float, lng: float) -> dict[str, Any]:
    """ArcGIS query params for polygon-intersects-point in WGS-84."""

    return {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }


def _point_buffer_params(
    lat: float, lng: float, *, distance_m: int, result_limit: int
) -> dict[str, Any]:
    """ArcGIS query params for polygon-intersects-buffer in WGS-84 metres."""

    return {
        "geometry": f"{lng},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": str(distance_m),
        "units": "esriSRUnit_Meter",
        "inSR": "4326",
        "outSR": "4326",
        "outFields": "*",
        "returnGeometry": "true",
        "resultRecordCount": str(result_limit),
        "f": "json",
    }


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two WGS-84 points in kilometres."""

    earth_radius_km = 6371.0088
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lng2 - lng1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def _coerce_class(raw: str | None) -> ShrinkSwellClass:
    if not raw:
        return ShrinkSwellClass.UNKNOWN
    normalised = raw.strip().lower()
    for member in ShrinkSwellClass:
        if member.value.lower() == normalised:
            return member
    return ShrinkSwellClass.UNKNOWN


def _optional_int(raw: Any) -> int | None:
    try:
        if raw is None or raw == "":
            return None
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _optional_float(raw: Any) -> float | None:
    try:
        if raw is None or raw == "":
            return None
        return float(raw)
    except (TypeError, ValueError):
        return None


def _optional_str(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.upper() == "UNKNOWN":
        return None
    return text


def _parse_shrink_swell(
    data: dict[str, Any],
    *,
    projection: ClimateProjection,
    horizon: ShrinkSwellHorizon,
) -> ShrinkSwellAssessment:
    """Map an ArcGIS feature response to :class:`ShrinkSwellAssessment`.

    Queries that land outside any polygon return a zero-feature response
    rather than an error; map that to ``ShrinkSwellClass.NONE`` so
    callers get a usable record instead of a missing key.
    """

    features = data.get("features") or []
    if not features:
        return ShrinkSwellAssessment(
            projection=projection,
            horizon_year=horizon,
            susceptibility=ShrinkSwellClass.NONE,
        )
    attrs = dict(features[0].get("attributes") or {})
    return ShrinkSwellAssessment(
        projection=projection,
        horizon_year=horizon,
        susceptibility=_coerce_class(attrs.get("CLASS")),
        legend=attrs.get("LEGEND"),
        version=attrs.get("VERSION"),
        properties=attrs,
    )


def _parse_landslide(
    feature: dict[str, Any],
    *,
    query_lat: float,
    query_lng: float,
) -> LandslideEvent:
    """Map one landslide ArcGIS feature to :class:`LandslideEvent`.

    Populates ``distance_km`` via Haversine from the query point so
    callers don't have to re-compute it.
    """

    attrs = dict(feature.get("attributes") or {})
    geom = feature.get("geometry") or {}
    lat = _optional_float(geom.get("y"))
    lng = _optional_float(geom.get("x"))
    distance_km: float | None = None
    if lat is not None and lng is not None:
        distance_km = _haversine_km(query_lat, query_lng, lat, lng)
    return LandslideEvent(
        landslide_id=_optional_int(attrs.get("LS_ID")),
        landslide_number=_optional_int(attrs.get("LANDSLIDE_NUMBER")),
        name=_optional_str(attrs.get("LANDSLIDE_NAME")),
        locality=_optional_str(attrs.get("LOCALITY_DETAILS")),
        first_known_year=_optional_str(attrs.get("FIRST_KNOWN_DATE_YEAR")),
        last_known_year=_optional_str(attrs.get("LAST_KNOWN_DATE_YEAR")),
        uncertainty_m=_optional_float(attrs.get("PLUS_OR_MINUS_METRES")),
        latitude=lat,
        longitude=lng,
        distance_km=distance_km,
        properties=attrs,
    )


class BGSClient(BaseAPIClient):
    """Client for the free BGS GeoIndex ArcGIS REST endpoints.

    The client surfaces a coarse but real slice of the pre-2024 BGS
    hazard model: three GeoClimate Basic shrink-swell horizons
    (``2030 / 2050 / 2080``), two UKCP18 horizons (``2030 / 2070``), and
    the open landslide inventory.
    """

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

    async def shrink_swell_at(
        self,
        lat: float,
        lng: float,
        *,
        horizon: ShrinkSwellHorizon = ShrinkSwellHorizon.H_2050,
        projection: ClimateProjection = ClimateProjection.BASIC,
    ) -> ShrinkSwellAssessment:
        """Return the shrink-swell rating at ``(lat, lng)`` for one horizon.

        ``projection=ClimateProjection.BASIC`` supports horizons
        ``2030 / 2050 / 2080``. ``projection=ClimateProjection.UKCP18``
        supports ``2030 / 2070``. Other combinations raise
        :class:`ValueError`.
        """

        layer = _SHRINK_SWELL_LAYERS.get((projection, horizon))
        if layer is None:
            raise ValueError(
                f"No BGS GeoClimate layer for projection={projection.value} "
                f"horizon={int(horizon)}"
            )
        data = await self._get(f"{layer}/query", params=_point_intersect_params(lat, lng))
        return _parse_shrink_swell(data, projection=projection, horizon=horizon)

    async def shrink_swell_trajectory(
        self,
        lat: float,
        lng: float,
        *,
        projection: ClimateProjection = ClimateProjection.BASIC,
    ) -> list[ShrinkSwellAssessment]:
        """Return shrink-swell ratings across every horizon for one projection.

        Results are ordered oldest horizon first so consumers can scan
        for ``None → Possible → Probable`` transitions over time.
        """

        horizons = sorted(
            h for (p, h) in _SHRINK_SWELL_LAYERS if p == projection
        )
        assessments = await asyncio.gather(
            *(
                self.shrink_swell_at(lat, lng, horizon=h, projection=projection)
                for h in horizons
            )
        )
        return list(assessments)

    async def landslides_near(
        self,
        lat: float,
        lng: float,
        *,
        distance_km: float = 5.0,
        limit: int = 50,
    ) -> list[LandslideEvent]:
        """Return historical landslide events within ``distance_km`` of the point.

        The ArcGIS buffer query is pre-filtered server-side via the
        ``distance`` parameter; results are Haversine-re-sorted client
        side so callers get strict ascending distance even when the
        upstream ordering slips.
        """

        if distance_km <= 0:
            raise ValueError(f"distance_km must be > 0 (got {distance_km})")
        if limit <= 0:
            raise ValueError(f"limit must be > 0 (got {limit})")
        distance_m = int(round(distance_km * 1000))
        data = await self._get(
            f"{_LANDSLIDE_LAYER}/query",
            params=_point_buffer_params(
                lat, lng, distance_m=distance_m, result_limit=limit
            ),
        )
        features = data.get("features") or []
        events = [
            _parse_landslide(f, query_lat=lat, query_lng=lng) for f in features
        ]
        events.sort(
            key=lambda e: e.distance_km if e.distance_km is not None else math.inf
        )
        return events

    async def geohazards_at(
        self,
        lat: float,
        lng: float,
        *,
        shrink_swell_projection: ClimateProjection = ClimateProjection.BASIC,
        landslide_radius_km: float = 5.0,
        landslide_limit: int = 25,
    ) -> GeohazardAssessment:
        """Aggregate shrink-swell trajectory + landslide inventory for one point.

        Fan-out is a single ``asyncio.gather`` — failure of any single
        call propagates. Callers that need partial failure semantics
        should wrap individual methods in their own ``gather`` with
        ``return_exceptions=True``.
        """

        trajectory, landslides = await asyncio.gather(
            self.shrink_swell_trajectory(lat, lng, projection=shrink_swell_projection),
            self.landslides_near(
                lat, lng, distance_km=landslide_radius_km, limit=landslide_limit
            ),
        )
        has_shrink_swell_risk = any(a.susceptibility in _RISK_CLASSES for a in trajectory)
        return GeohazardAssessment(
            shrink_swell_trajectory=trajectory,
            landslides_nearby=landslides,
            landslide_search_radius_km=landslide_radius_km,
            has_shrink_swell_risk=has_shrink_swell_risk,
            has_landslide_risk=bool(landslides),
        )
