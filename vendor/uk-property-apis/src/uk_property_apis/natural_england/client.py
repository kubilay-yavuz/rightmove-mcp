"""Async client for Natural England MAGIC WFS.

The Defra Data Services platform migrated in 2025 from a single shared
GeoServer (``/spatialdata/`` root with ``Natural_England:*`` layer names)
to **per-dataset WFS endpoints** with UUID-namespaced type names and
data natively in EPSG:27700 (British National Grid) instead of WGS-84.

Rather than ask callers to know these details we hide the migration in
this client: you still pass ``(lat, lng)`` in WGS-84, we transparently
build a tight lat/lng BBOX filter (the server-side reprojects for us)
and map the new property names back to the stable :mod:`models` shape.

The ``green_belt_at`` method is the one exception — the green-belt
dataset never lived on the Natural England WFS and the old
``Natural_England:GreenBelt`` layer disappeared with the 2025 migration.
Production consumers should pull green-belt polygons from
``planning.data.gov.uk`` (``PlanningClient``) instead; calling it here
raises a ``RuntimeError`` with the redirect instructions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.natural_england.models import (
    AncientWoodlandArea,
    AONBArea,
    Designations,
    GreenBeltArea,
    NationalParkArea,
    SSSIArea,
)

_BASE_URL = "https://environment.data.gov.uk/spatialdata/"

# Per-dataset WFS slug + dataset UUID + layer name on the new Defra
# platform. Each entry yields the HTTP path (relative to ``_BASE_URL``)
# and the ``dataset-{uuid}:{layer}`` qualifier required by the new
# WFS. Discovered 2026-04-19 from GetCapabilities.
_DATASETS: dict[str, tuple[str, str, str]] = {
    "sssi": (
        "sites-of-special-scientific-interest-england",
        "ba8dc201-66ef-4983-9d46-7378af21027e",
        "Sites_of_Special_Scientific_Interest_England",
    ),
    "aonb": (
        "areas-of-outstanding-natural-beauty-england",
        "0c1ea47f-3c79-47f0-b0ed-094e0a136971",
        "Areas_of_Outstanding_Natural_Beauty_England",
    ),
    "national_park": (
        "national-parks-england",
        "e819098e-e248-4a8f-b684-5a21ca521b9b",
        "National_Parks_England",
    ),
    "ancient_woodland": (
        "ancient-woodland-england",
        "f425f1e1-fc18-4b5a-88d8-76934125627c",
        "Ancient_Woodland_England",
    ),
}

# Tight ~33m (at UK latitudes) bbox around the query point. The WFS
# engine applies a *real* geometry-intersect inside the bbox, so this
# is functionally equivalent to point-in-polygon for anything with
# polygon features.
_BBOX_EPS_DEG = 0.0003

_GREEN_BELT_MIGRATION_MESSAGE = (
    "Natural England MAGIC no longer hosts a green-belt layer — the old "
    "Natural_England:GreenBelt WFS was retired during the 2025 Defra "
    "Data Services migration. Use PlanningClient with dataset='green-belt' "
    "from planning.data.gov.uk instead. "
    "See planning.data.gov.uk/dataset/green-belt."
)


def _wfs_params(uuid: str, layer: str, lat: float, lng: float) -> dict[str, Any]:
    """Build WFS GetFeature params for a tight bbox around ``(lat, lng)``."""

    type_name = f"dataset-{uuid}:{layer}"
    south = lat - _BBOX_EPS_DEG
    north = lat + _BBOX_EPS_DEG
    west = lng - _BBOX_EPS_DEG
    east = lng + _BBOX_EPS_DEG
    # Defra GeoServer expects ``south,west,north,east,crs`` when the CRS
    # is urn:EPSG:4326 (lat-first). This matches the WFS 2.0.0 spec.
    bbox = f"{south},{west},{north},{east},urn:ogc:def:crs:EPSG::4326"
    return {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "outputFormat": "application/json",
        "typeNames": type_name,
        "bbox": bbox,
    }


def _extract_features(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data.get("features") or []


def _prop(props: Mapping[str, Any], *keys: str) -> Any:
    """Return the first non-null value from ``props`` across ``keys``.

    The pre-2025 GeoServer used uppercase names (``NAME``, ``STATUS``),
    the post-2025 platform uses lowercase (``name``, ``status``) — we
    accept both so this client survives whichever shape is in flight.
    """

    for key in keys:
        value = props.get(key)
        if value is not None:
            return value
    return None


class NaturalEnglandClient(BaseAPIClient):
    """Client for the Natural England MAGIC WFS (Defra Data Services)."""

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

    async def _fetch_layer(
        self, tag: str, lat: float, lng: float
    ) -> list[dict[str, Any]]:
        slug, uuid, layer = _DATASETS[tag]
        data = await self._get(
            f"{slug}/wfs", params=_wfs_params(uuid, layer, lat, lng)
        )
        return _extract_features(data)

    async def green_belt_at(self, lat: float, lng: float) -> list[GreenBeltArea]:
        """**Upstream withdrawn.** Use ``PlanningClient`` instead.

        Raises ``RuntimeError`` with migration instructions.
        """

        raise RuntimeError(_GREEN_BELT_MIGRATION_MESSAGE)

    async def sssi_at(self, lat: float, lng: float) -> list[SSSIArea]:
        """Return SSSI designations intersecting ``(lat, lng)``."""
        results = []
        for feature in await self._fetch_layer("sssi", lat, lng):
            props = feature.get("properties") or {}
            results.append(
                self._validate_model(
                    SSSIArea,
                    {
                        "name": _prop(props, "SSSI_NAME", "name", "sssi_name"),
                        "status": _prop(props, "STATUS", "status"),
                        "properties": props,
                    },
                )
            )
        return results

    async def aonb_at(self, lat: float, lng: float) -> list[AONBArea]:
        """Return AONB designations intersecting ``(lat, lng)``."""
        results = []
        for feature in await self._fetch_layer("aonb", lat, lng):
            props = feature.get("properties") or {}
            results.append(
                self._validate_model(
                    AONBArea,
                    {
                        "name": _prop(props, "NAME", "name"),
                        "properties": props,
                    },
                )
            )
        return results

    async def national_park_at(
        self, lat: float, lng: float
    ) -> list[NationalParkArea]:
        """Return National Park designations intersecting ``(lat, lng)``."""
        results = []
        for feature in await self._fetch_layer("national_park", lat, lng):
            props = feature.get("properties") or {}
            results.append(
                self._validate_model(
                    NationalParkArea,
                    {
                        "name": _prop(props, "NAME", "name"),
                        "properties": props,
                    },
                )
            )
        return results

    async def ancient_woodland_at(
        self, lat: float, lng: float
    ) -> list[AncientWoodlandArea]:
        """Return Ancient Woodland designations intersecting ``(lat, lng)``."""
        results = []
        for feature in await self._fetch_layer("ancient_woodland", lat, lng):
            props = feature.get("properties") or {}
            results.append(
                self._validate_model(
                    AncientWoodlandArea,
                    {
                        "name": _prop(props, "NAME", "name", "themname"),
                        "category": _prop(props, "CATEGORY", "category", "themclass"),
                        "properties": props,
                    },
                )
            )
        return results

    async def designations_at(self, lat: float, lng: float) -> Designations:
        """Return all Natural England designations at ``(lat, lng)``.

        Parallel fan-out across SSSI / AONB / National Park / Ancient
        Woodland. Green belt is *not* queried — it migrated off this
        platform in 2025 (see :meth:`green_belt_at`).
        """

        sssi, aonb, np_, aw = await asyncio.gather(
            self.sssi_at(lat, lng),
            self.aonb_at(lat, lng),
            self.national_park_at(lat, lng),
            self.ancient_woodland_at(lat, lng),
        )
        return Designations(
            is_green_belt=False,  # layer withdrawn
            is_sssi=bool(sssi),
            is_aonb=bool(aonb),
            is_national_park=bool(np_),
            is_ancient_woodland=bool(aw),
            green_belt=[],
            sssi=sssi,
            aonb=aonb,
            national_parks=np_,
            ancient_woodland=aw,
        )
