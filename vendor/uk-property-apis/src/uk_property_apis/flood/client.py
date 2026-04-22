"""Async client for Environment Agency flood monitoring."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.flood.models import (
    FloodArea,
    FloodListResponse,
    FloodWarning,
    MonitoringStation,
)


class FloodClient(BaseAPIClient):
    """Client for https://environment.data.gov.uk/flood-monitoring/."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url="https://environment.data.gov.uk/flood-monitoring/",
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    async def active_floods_near(self, lat: float, lng: float, *, distance_km: float = 10.0) -> list[FloodWarning]:
        """Return active flood warnings within ``distance_km`` of a coordinate."""

        params = {"lat": lat, "long": lng, "dist": distance_km}
        payload = await self._get("id/floods", params=params)
        parsed = self._validate_model(FloodListResponse, payload)
        return [self._validate_model(FloodWarning, item) for item in parsed.items]

    async def flood_areas_near(self, lat: float, lng: float, *, distance_km: float = 10.0) -> list[FloodArea]:
        """Return flood area polygons near a coordinate."""

        params = {"lat": lat, "long": lng, "dist": distance_km}
        payload = await self._get("id/floodAreas", params=params)
        parsed = self._validate_model(FloodListResponse, payload)
        return [self._validate_model(FloodArea, item) for item in parsed.items]

    async def stations_near(self, lat: float, lng: float, *, distance_km: float = 10.0) -> list[MonitoringStation]:
        """Return monitoring stations near a coordinate."""

        params = {"lat": lat, "long": lng, "dist": distance_km}
        payload = await self._get("id/stations", params=params)
        parsed = self._validate_model(FloodListResponse, payload)
        return [self._validate_model(MonitoringStation, item) for item in parsed.items]


async def active_floods_near(lat: float, lng: float, *, distance_km: float = 10.0) -> list[FloodWarning]:
    """Convenience wrapper for :meth:`FloodClient.active_floods_near`."""

    async with FloodClient() as client:
        return await client.active_floods_near(lat, lng, distance_km=distance_km)
