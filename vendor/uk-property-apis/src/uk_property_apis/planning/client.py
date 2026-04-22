"""Async client for national planning data (planning.data.gov.uk)."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from typing import Any

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.planning.models import EntityPage


def _wkt_point(lat: float, lon: float) -> str:
    """Return a WKT POINT for ``lon``/``lat`` (x/y) order."""

    return f"POINT ({lon} {lat})"


def _buffer_degrees_for_radius_m(lat: float, radius_m: float) -> float:
    """Approximate degrees longitude offset for a metre radius at ``lat``."""

    metres_per_degree = 111_320 * math.cos(math.radians(lat))
    if metres_per_degree < 1:
        metres_per_degree = 1.0
    return radius_m / metres_per_degree


class PlanningClient(BaseAPIClient):
    """Client for https://www.planning.data.gov.uk/entity.json."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 60.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url="https://www.planning.data.gov.uk/",
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    async def fetch_entities(
        self,
        dataset: str,
        *,
        geometry: str | None = None,
        geometry_relation: str | None = None,
        limit: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> EntityPage:
        """Fetch entities for a dataset with optional spatial filter."""

        params: dict[str, Any] = {"dataset": dataset}
        if geometry is not None:
            params["geometry"] = geometry
        if geometry_relation is not None:
            params["geometry_relation"] = geometry_relation
        if limit is not None:
            params["limit"] = limit
        if extra_params:
            params.update(dict(extra_params))
        payload = await self._get("entity.json", params=params)
        return self._validate_model(EntityPage, payload)

    async def fetch_listed_buildings_near(self, lat: float, lon: float, *, radius_m: float = 500.0) -> EntityPage:
        """Return listed-building entities intersecting a buffered point."""

        delta = _buffer_degrees_for_radius_m(lat, radius_m)
        wkt = (
            f"POLYGON (({lon - delta} {lat - delta}, {lon + delta} {lat - delta}, "
            f"{lon + delta} {lat + delta}, {lon - delta} {lat + delta}, {lon - delta} {lat - delta}))"
        )
        return await self.fetch_entities(
            "listed-building",
            geometry=wkt,
            geometry_relation="intersects",
        )

    async def fetch_conservation_areas(
        self,
        *,
        geometry: str | None = None,
        geometry_relation: str | None = None,
        limit: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> EntityPage:
        """Convenience wrapper for the ``conservation-area`` dataset."""

        return await self.fetch_entities(
            "conservation-area",
            geometry=geometry,
            geometry_relation=geometry_relation,
            limit=limit,
            extra_params=extra_params,
        )

    async def fetch_article4_direction_areas(
        self,
        *,
        geometry: str | None = None,
        geometry_relation: str | None = None,
        limit: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> EntityPage:
        """Convenience wrapper for ``article-4-direction-area``."""

        return await self.fetch_entities(
            "article-4-direction-area",
            geometry=geometry,
            geometry_relation=geometry_relation,
            limit=limit,
            extra_params=extra_params,
        )

    async def fetch_flood_risk_zones(
        self,
        *,
        geometry: str | None = None,
        geometry_relation: str | None = None,
        limit: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> EntityPage:
        """Convenience wrapper for ``flood-risk-zone``."""

        return await self.fetch_entities(
            "flood-risk-zone",
            geometry=geometry,
            geometry_relation=geometry_relation,
            limit=limit,
            extra_params=extra_params,
        )

    async def fetch_green_belt(
        self,
        *,
        geometry: str | None = None,
        geometry_relation: str | None = None,
        limit: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> EntityPage:
        """Convenience wrapper for ``green-belt``."""

        return await self.fetch_entities(
            "green-belt",
            geometry=geometry,
            geometry_relation=geometry_relation,
            limit=limit,
            extra_params=extra_params,
        )

    async def fetch_tree_preservation_zones(
        self,
        *,
        geometry: str | None = None,
        geometry_relation: str | None = None,
        limit: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> EntityPage:
        """Convenience wrapper for ``tree-preservation-zone``."""

        return await self.fetch_entities(
            "tree-preservation-zone",
            geometry=geometry,
            geometry_relation=geometry_relation,
            limit=limit,
            extra_params=extra_params,
        )


def wkt_point(lat: float, lon: float) -> str:
    """Public helper matching :func:`_wkt_point` for tests and callers."""

    return _wkt_point(lat, lon)
