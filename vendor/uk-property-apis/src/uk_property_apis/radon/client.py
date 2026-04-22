"""Async client for the UKHSA / BGS Indicative Atlas of Radon.

This replaces the withdrawn ``BGS_Hazards/GeoHazardsEngland`` subset
for one specific geohazard signal — indoor radon risk. The dataset is
free (Open Government Licence) and served as an ArcGIS MapServer at
``https://map.bgs.ac.uk/arcgis/rest/services/GeoIndex_Onshore/radon/MapServer/``.

Only one layer (``Radon.1km``, id=0) is needed per query. We run a
point-intersects ``/query?geometry=...`` against that layer and parse
the single returned feature via
:meth:`uk_property_apis.radon.models.RadonPotential.from_arcgis_attributes`.

Compared to the old BGS hazard endpoints this layer is still actively
published and we verified it returns distinct ``CLASS_MAX`` values
for London (class 1, low potential) and Bodmin / Cornwall (class 6,
high potential) as of the writing commit.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.radon.models import RadonPotential

_BASE_URL = "https://map.bgs.ac.uk/arcgis/rest/services/"
_LAYER_PATH = "GeoIndex_Onshore/radon/MapServer/0/query"


def _point_geometry(lat: float, lng: float) -> str:
    """Build the ArcGIS ``geometry`` parameter for a WGS-84 point.

    ArcGIS REST accepts two shapes: the terse ``lng,lat`` form and the
    canonical JSON-serialised ``{"x":..,"y":..,"spatialReference":..}``
    form. The JSON form is required whenever ``inSR`` differs from the
    layer's native SR (BGS MapServer is in BNG / EPSG:27700, we query
    in EPSG:4326) so we use it unconditionally.
    """

    return json.dumps(
        {
            "x": lng,
            "y": lat,
            "spatialReference": {"wkid": 4326},
        },
        separators=(",", ":"),
    )


class BGSRadonClient(BaseAPIClient):
    """Client for the BGS / UKHSA Indicative Atlas of Radon (1 km grid)."""

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

    async def potential_at(self, lat: float, lng: float) -> RadonPotential:
        """Return the radon potential band for a WGS-84 point.

        When the point falls outside the atlas (offshore / outside GB)
        the ArcGIS response is a payload with an empty ``features``
        list; the returned :class:`RadonPotential` is empty with
        ``class_max=None`` and ``affected_area=False``. Callers should
        treat that as 'no data' rather than 'safe'.
        """

        params: dict[str, Any] = {
            "geometry": _point_geometry(lat, lng),
            "geometryType": "esriGeometryPoint",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
        }
        data = await self._get(_LAYER_PATH, params=params)
        features = data.get("features") or []
        source_url = f"{self._base_url}{_LAYER_PATH}"
        if not features:
            return RadonPotential(source_url=source_url)
        attrs = features[0].get("attributes") or {}
        return RadonPotential.from_arcgis_attributes(attrs, source_url=source_url)


async def potential_at(lat: float, lng: float) -> RadonPotential:
    """Convenience wrapper that opens a one-shot :class:`BGSRadonClient`."""

    async with BGSRadonClient() as client:
        return await client.potential_at(lat, lng)


__all__ = [
    "BGSRadonClient",
    "potential_at",
]
