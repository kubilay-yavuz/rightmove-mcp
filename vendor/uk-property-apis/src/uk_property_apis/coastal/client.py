"""Async client for the EA NCERM 2024 coastal erosion WFS.

History
-------
The original NCERM ("NCERM 2018") was served at
``environment.data.gov.uk/spatialdata/`` with a single layer carrying
three predictions per feature (``PREDICTION_20`` / ``PREDICTION_50`` /
``PREDICTION_100``). That endpoint was retired in January 2025 — any
``GetFeature`` call against it now returns the Defra Next.js portal HTML.

The replacement dataset ("NCERM 2024", a.k.a. "NCERN National 2024"
internally at the EA) is published at
``environment.data.gov.uk/spatialdata/ncern-national-2024/wfs`` with a
very different layer surface:

* **Instability footprint** (non-frontage-resolved recession hotspots):
    - ``NCERM_Ground_Instability_Recession`` (lines)
    - ``NCERM_Ground_Instability_Zone`` (polygons — ≈80 nationally)
* **SMP predictions** (policy-aware erosion distance by year and climate):
    - ``NCERM_SMP_{horizon}_{uplift}CC`` where
      ``horizon ∈ {2055, 2105}`` and ``uplift ∈ {0, 70, 95}`` → 6 layers
* **NFI predictions** (no-further-intervention erosion distance):
    - ``NCERM_NFI_{horizon}_{uplift}CC`` — same 6 axes, 6 layers

Axes modelling lives in :mod:`uk_property_apis.coastal.models`
(``HorizonYear`` / ``ClimateUplift`` / ``ManagementScenario``).

WFS contract
------------
* Output CRS — the service natively supports ``EPSG:4326`` /
  ``EPSG:4258`` / ``EPSG:3857`` / ``EPSG:27700`` via ``srsName``. We
  always request 4326 so downstream callers never have to reproject.
* Filter CRS — ``DWITHIN`` / ``INTERSECTS`` against the ``shape``
  geometry column are unreliable when the filter CRS doesn't match the
  native storage CRS (EPSG:27700 BNG). ``BBOX`` filters work cleanly in
  ``EPSG:4326`` with axis order ``(minLat, minLon, maxLat, maxLon)``
  when you pin the CRS via the ``urn:ogc:def:crs:EPSG::4326`` URN, so
  this client uses BBOX exclusively and post-filters client-side when
  an exact radius is needed.
* Geometry column is named ``shape`` (lower-case). The global
  WFS namespace under ``/spatialdata/`` accepts unqualified type
  names — we rely on that and don't hard-code the dataset GUID.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from typing import Any, Literal, cast

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.coastal.models import (
    ClimateUplift,
    ErosionZone,
    HorizonYear,
    ManagementScenario,
    ShorelinePrediction,
    SMPPolicy,
)

_BASE_URL = "https://environment.data.gov.uk/spatialdata/ncern-national-2024/wfs"

_INSTABILITY_ZONE_LAYER = "NCERM_Ground_Instability_Zone"
_INSTABILITY_RECESSION_LAYER = "NCERM_Ground_Instability_Recession"

_DEFAULT_WFS_COUNT = 200
"""Maximum features per GetFeature call. The service's default server-side
cap is ~1000; we deliberately stay below that so a wide BBOX query can't
blow up memory in the agent process. Callers needing more should scope
the BBOX tighter rather than paginate."""

_EARTH_RADIUS_KM = 6371.0088


def _smp_layer(horizon: HorizonYear, uplift: ClimateUplift) -> str:
    return f"NCERM_SMP_{int(horizon)}_{int(uplift)}CC"


def _nfi_layer(horizon: HorizonYear, uplift: ClimateUplift) -> str:
    return f"NCERM_NFI_{int(horizon)}_{int(uplift)}CC"


def _scenario_layer(
    scenario: ManagementScenario, horizon: HorizonYear, uplift: ClimateUplift
) -> str:
    if scenario is ManagementScenario.SMP:
        return _smp_layer(horizon, uplift)
    return _nfi_layer(horizon, uplift)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in km between two WGS-84 points.

    Used as the post-filter after a BBOX query so callers can still ask for
    "within 5 km of this point" even though the WFS DWITHIN filter is
    unreliable on the NCERM 2024 endpoint.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _bbox_for_point(lat: float, lng: float, distance_km: float) -> tuple[float, float, float, float]:
    """Return ``(minLat, minLon, maxLat, maxLon)`` covering a disc of
    ``distance_km`` around ``(lat, lng)``.

    Degrees-per-km on the longitudinal axis scales with ``cos(lat)``; we
    cap the denominator so callers near the poles still get a finite box
    (NCERM itself only covers ~50..56°N so the cap is defensive).
    """
    if distance_km <= 0:
        raise ValueError(f"distance_km must be > 0, got {distance_km!r}")
    lat_delta = distance_km / 111.32
    lng_scale = max(math.cos(math.radians(lat)), 0.01)
    lng_delta = distance_km / (111.32 * lng_scale)
    return (
        max(-90.0, lat - lat_delta),
        lng - lng_delta,
        min(90.0, lat + lat_delta),
        lng + lng_delta,
    )


def _feature_centroid(feature: dict[str, Any]) -> tuple[float, float] | None:
    """Crude centroid of a GeoJSON feature, expressed as ``(lat, lng)``.

    We average the first exterior ring of the first polygon and trust
    that's "close enough" for post-BBOX distance filtering: NCERM
    frontage polygons are <1 km wide so the centroid is within ~500 m of
    any point on the polygon.
    """
    geom = feature.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None

    ring: list[Any] | None = None
    if gtype == "Polygon":
        ring = coords[0] if coords else None
    elif gtype == "MultiPolygon":
        ring = coords[0][0] if coords and coords[0] else None
    elif gtype == "LineString":
        ring = coords
    elif gtype == "MultiLineString":
        ring = coords[0] if coords else None
    elif gtype == "Point":
        lng, lat, *_ = cast(list[float], coords)
        return lat, lng

    if not ring:
        return None
    lngs = [pt[0] for pt in ring if len(pt) >= 2]
    lats = [pt[1] for pt in ring if len(pt) >= 2]
    if not lngs or not lats:
        return None
    return sum(lats) / len(lats), sum(lngs) / len(lngs)


def _wfs_get_feature_params(
    type_name: str,
    *,
    bbox: tuple[float, float, float, float],
    count: int = _DEFAULT_WFS_COUNT,
) -> dict[str, Any]:
    min_lat, min_lon, max_lat, max_lon = bbox
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "outputFormat": "json",
        "srsName": "urn:ogc:def:crs:EPSG::4326",
        "typeName": type_name,
        "bbox": (
            f"{min_lat},{min_lon},{max_lat},{max_lon},"
            "urn:ogc:def:crs:EPSG::4326"
        ),
        "count": count,
    }


def _extract_features(data: dict[str, Any]) -> list[dict[str, Any]]:
    feats = data.get("features") or []
    return feats if isinstance(feats, list) else []


def _coerce_policy(raw: Any) -> SMPPolicy | None:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        return SMPPolicy(value)
    except ValueError:
        return SMPPolicy.UNKNOWN


def _nonempty_policy_units(props: Mapping[str, Any]) -> list[str]:
    """Gather smp_pu1..smp_pu5 into an ordered, de-duplicated list."""
    seen: list[str] = []
    for idx in range(1, 6):
        value = props.get(f"smp_pu{idx}")
        if value is None:
            continue
        value_str = str(value).strip()
        if not value_str or value_str in seen:
            continue
        seen.append(value_str)
    return seen


class CoastalErosionClient(BaseAPIClient):
    """Client for the EA NCERM 2024 coastal erosion WFS.

    Three query modes, each of which returns Pydantic models ready for
    the climate-risk actor to serialise:

    * :meth:`erosion_risk_near` — search the instability-zone layer
      within a radius of a point. Returns zero or more zones.
    * :meth:`shoreline_predictions_near` — SMP or NFI predictions for
      one ``(horizon_year, climate_uplift)`` combo, scoped to a radius.
    * :meth:`shoreline_prediction` — single closest frontage for one
      ``(horizon_year, climate_uplift)`` combo. Returns ``None`` if no
      frontage intersects / lies within ``distance_km`` of the point.

    The client is deliberately thin — the climate-risk actor's
    ``_run_coastal`` fan-out composes these calls across the axes it
    cares about and owns the output-shape decisions.
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

    async def erosion_risk_near(
        self,
        lat: float,
        lng: float,
        *,
        distance_km: float = 5.0,
    ) -> list[ErosionZone]:
        """Return instability zones whose centroid is within ``distance_km``.

        Uses ``NCERM_Ground_Instability_Zone`` (≈80 features nationally).
        A BBOX query filters at the service, then a Haversine post-filter
        tightens to the exact disc.
        """
        bbox = _bbox_for_point(lat, lng, distance_km)
        data = await self._get(
            "",
            params=_wfs_get_feature_params(_INSTABILITY_ZONE_LAYER, bbox=bbox),
        )
        results: list[ErosionZone] = []
        for feature in _extract_features(data):
            props = feature.get("properties") or {}
            centroid = _feature_centroid(feature)
            if centroid is not None:
                c_lat, c_lng = centroid
                if _haversine_km(lat, lng, c_lat, c_lng) > distance_km:
                    continue
            results.append(
                self._validate_model(
                    ErosionZone,
                    {
                        "zone_id": props.get("id"),
                        "local_authority": props.get("local_auth"),
                        "smp_name": props.get("smp_name"),
                        "policy_units": _nonempty_policy_units(props),
                        "rear_scarp": props.get("rearscarpr"),
                        "properties": props,
                    },
                )
            )
        return results

    async def shoreline_predictions_near(
        self,
        lat: float,
        lng: float,
        *,
        distance_km: float = 5.0,
        horizon_year: HorizonYear | int = HorizonYear.NEAR_TERM,
        climate_uplift: ClimateUplift | int = ClimateUplift.NONE,
        scenario: ManagementScenario = ManagementScenario.SMP,
    ) -> list[ShorelinePrediction]:
        """Return SMP / NFI frontages within ``distance_km`` of the point,
        each scored at the requested ``(horizon, climate)`` combo.
        """
        candidates = await self._fetch_candidates(
            lat,
            lng,
            distance_km=distance_km,
            horizon_year=horizon_year,
            climate_uplift=climate_uplift,
            scenario=scenario,
        )
        return [pred for _, pred in candidates]

    async def shoreline_prediction(
        self,
        lat: float,
        lng: float,
        *,
        horizon_year: HorizonYear | int = HorizonYear.NEAR_TERM,
        climate_uplift: ClimateUplift | int = ClimateUplift.NONE,
        scenario: ManagementScenario = ManagementScenario.SMP,
        max_radius_km: float = 2.0,
    ) -> ShorelinePrediction | None:
        """Closest-frontage convenience wrapper over
        :meth:`shoreline_predictions_near`.

        Useful for per-property risk scoring: hydrates the single NCERM
        frontage that best represents the erosion exposure at
        ``(lat, lng)``. Returns ``None`` when the point isn't on the
        coast.

        ``max_radius_km`` caps the search so an inland point doesn't
        grab a distant seaside frontage as a false positive.
        """
        candidates = await self._fetch_candidates(
            lat,
            lng,
            distance_km=max_radius_km,
            horizon_year=horizon_year,
            climate_uplift=climate_uplift,
            scenario=scenario,
        )
        if not candidates:
            return None
        # Return the frontage with the smallest centroid-to-point
        # Haversine distance so "closest coastal frontage" semantics
        # survive even when BBOX returned many adjacent polygons.
        _, pred = min(candidates, key=lambda item: item[0])
        return pred

    async def _fetch_candidates(
        self,
        lat: float,
        lng: float,
        *,
        distance_km: float,
        horizon_year: HorizonYear | int,
        climate_uplift: ClimateUplift | int,
        scenario: ManagementScenario,
    ) -> list[tuple[float, ShorelinePrediction]]:
        """Internal: run one BBOX query, post-filter by Haversine, and
        return ``(distance_km, prediction)`` pairs so both the "near"
        and the "closest" entry points can reuse one fetch path.
        """
        horizon = HorizonYear(int(horizon_year))
        uplift = ClimateUplift(int(climate_uplift))
        layer = _scenario_layer(scenario, horizon, uplift)
        bbox = _bbox_for_point(lat, lng, distance_km)
        data = await self._get(
            "",
            params=_wfs_get_feature_params(layer, bbox=bbox),
        )
        out: list[tuple[float, ShorelinePrediction]] = []
        for feature in _extract_features(data):
            centroid = _feature_centroid(feature)
            if centroid is None:
                continue
            c_lat, c_lng = centroid
            distance = _haversine_km(lat, lng, c_lat, c_lng)
            if distance > distance_km:
                continue
            out.append(
                (
                    distance,
                    self._build_prediction(feature, scenario, horizon, uplift),
                )
            )
        return out

    def _build_prediction(
        self,
        feature: dict[str, Any],
        scenario: ManagementScenario,
        horizon: HorizonYear,
        uplift: ClimateUplift,
    ) -> ShorelinePrediction:
        props = feature.get("properties") or {}
        distance_key = (
            f"smp{int(horizon)}_{int(uplift)}"
            if scenario is ManagementScenario.SMP
            else f"nfi{int(horizon)}_{int(uplift)}"
        )
        distance_raw = props.get(distance_key)
        try:
            predicted = float(distance_raw) if distance_raw is not None else None
        except (TypeError, ValueError):
            predicted = None
        return self._validate_model(
            ShorelinePrediction,
            {
                "management_scenario": scenario,
                "horizon_year": int(horizon),
                "climate_uplift": int(uplift),
                "frontage_id": props.get("frontageid"),
                "smp_name": props.get("smp_name"),
                "policy_unit": props.get("smp_pu"),
                "medium_term_policy": _coerce_policy(props.get("mt_smp")),
                "long_term_policy": _coerce_policy(props.get("lt_smp")),
                "predicted_erosion_distance_m": predicted,
                "def_type": props.get("def_type"),
                "properties": props,
            },
        )


__all__ = [
    "CoastalErosionClient",
]


_DWithin = Literal["bbox+haversine"]
"""NCERM 2024 quirk documented in the module docstring: the WFS rejects
``DWITHIN`` filters in EPSG:4326, so this client implements disc queries
as BBOX + client-side Haversine post-filter. Type alias exists purely so
someone grepping for "dwithin" lands here."""
