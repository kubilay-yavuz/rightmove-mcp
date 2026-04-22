"""Async client for the ONS Beta API."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.ons.models import ONSDatasetVersion, ONSObservationsResponse


def _json_env(name: str, default: str) -> dict[str, str]:
    raw = os.environ.get(name, default)
    data = json.loads(raw)
    if not isinstance(data, dict):
        msg = f"{name} must be a JSON object of string filters"
        raise ValueError(msg)
    return {str(k): str(v) for k, v in data.items()}


class ONSClient(BaseAPIClient):
    """Client for ``https://api.beta.ons.gov.uk/v1`` — census and official statistics."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 60.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url="https://api.beta.ons.gov.uk/v1/",
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    async def dataset_version(
        self,
        dataset_id: str,
        *,
        edition: str = "time-series",
        version: int = 1,
    ) -> ONSDatasetVersion:
        """Return metadata for a dataset edition/version."""

        path = f"datasets/{dataset_id}/editions/{edition}/versions/{version}"
        payload = await self._get(path)
        return self._validate_model(ONSDatasetVersion, payload)

    async def observations(
        self,
        dataset_id: str,
        *,
        edition: str,
        version: int,
        dimension_filters: Mapping[str, str],
    ) -> ONSObservationsResponse:
        """Return observations for a fully-specified dimension filter set."""

        path = f"datasets/{dataset_id}/editions/{edition}/versions/{version}/observations"
        payload = await self._get(path, params=dict(dimension_filters))
        return self._validate_model(ONSObservationsResponse, payload)

    async def population_by_lsoa(self, lsoa_code: str) -> ONSObservationsResponse:
        """Return population-related census observations for a geography code.

        Dimension names and supporting filters are **dataset-specific**. Defaults target
        Census 2021 ``TS039`` (unpaid care by lower-tier local authority). Override via
        ``ONS_POPULATION_LSOA_*`` environment variables or call :meth:`observations`
        directly once you know the dimension ids for your chosen table.
        """

        dataset_id = os.environ.get("ONS_POPULATION_LSOA_DATASET", "TS039")
        edition = os.environ.get("ONS_POPULATION_LSOA_EDITION", "2021")
        ver = int(os.environ.get("ONS_POPULATION_LSOA_VERSION", "1"))
        geo_dim = os.environ.get("ONS_POPULATION_LSOA_GEO_DIM", "ltla")
        filters = _json_env("ONS_POPULATION_LSOA_EXTRA", '{"is_carer":"0"}') | {geo_dim: lsoa_code}
        return await self.observations(dataset_id, edition=edition, version=ver, dimension_filters=filters)

    async def median_income_by_msoa(self, msoa_code: str) -> ONSObservationsResponse:
        """Return income-related observations for an MSOA (or other) geography code.

        Defaults mirror :meth:`population_by_lsoa` but read ``ONS_INCOME_MSOA_*`` env vars
        so you can wire a median-income table without code changes.
        """

        dataset_id = os.environ.get("ONS_INCOME_MSOA_DATASET", "TS039")
        edition = os.environ.get("ONS_INCOME_MSOA_EDITION", "2021")
        ver = int(os.environ.get("ONS_INCOME_MSOA_VERSION", "1"))
        geo_dim = os.environ.get("ONS_INCOME_MSOA_GEO_DIM", "ltla")
        filters = _json_env("ONS_INCOME_MSOA_EXTRA", '{"is_carer":"0"}') | {geo_dim: msoa_code}
        return await self.observations(dataset_id, edition=edition, version=ver, dimension_filters=filters)

    async def unemployment_by_local_authority(self, la_code: str) -> ONSObservationsResponse:
        """Return labour-market style observations for a local authority code.

        Configure the backing dataset via ``ONS_UNEMPLOYMENT_LA_*`` environment variables.
        """

        dataset_id = os.environ.get("ONS_UNEMPLOYMENT_LA_DATASET", "wellbeing-local-authority")
        edition = os.environ.get("ONS_UNEMPLOYMENT_LA_EDITION", "time-series")
        ver = int(os.environ.get("ONS_UNEMPLOYMENT_LA_VERSION", "4"))
        geo_dim = os.environ.get("ONS_UNEMPLOYMENT_LA_GEO_DIM", "geography")
        filters = _json_env(
            "ONS_UNEMPLOYMENT_LA_EXTRA",
            '{"time":"2019-20","measureofwellbeing":"anxiety","estimate":"average-mean"}',
        ) | {geo_dim: la_code}
        return await self.observations(dataset_id, edition=edition, version=ver, dimension_filters=filters)

    async def census_table(
        self,
        table_id: str,
        geography: str,
        *,
        edition: str = "2021",
        version: int = 1,
        area_type: str = "ltla",
    ) -> ONSObservationsResponse:
        """Return Census 2021 observations for a specific table and geography code.

        Calls ``/datasets/{table_id}/editions/{edition}/versions/{version}/json``
        with ``?area-type={area_type},{geography}`` — this is the flexible
        geography endpoint (the legacy ``/observations`` endpoint returns
        500s for Census 2021 tables because it demands every non-geo
        dimension be filtered, which defeats the point of the fan-out).

        ``area_type`` defaults to ``ltla`` (local authority) and can be
        flipped to ``ctry``, ``rgn``, ``msoa``, ``lsoa``, etc. — see the
        per-table dimensions via ``/population-types/UR/dimensions``.

        The returned :class:`ONSObservationsResponse` carries the full
        cube: ``dimensions`` describes the axes (including their
        categories / labels / option IDs), and ``observations`` is a
        flat numeric list aligned with the cartesian product of the
        axis orderings.
        """
        path = f"datasets/{table_id}/editions/{edition}/versions/{version}/json"
        params = {"area-type": f"{area_type},{geography}"}
        payload = await self._get(path, params=params)
        return self._validate_model(ONSObservationsResponse, payload)

    async def population(self, geography: str) -> ONSObservationsResponse:
        """Census 2021 TS001 — usual resident population by area."""
        return await self.census_table("TS001", geography)

    async def household_composition(self, geography: str) -> ONSObservationsResponse:
        """Census 2021 TS003 — household composition."""
        return await self.census_table("TS003", geography)

    async def housing_tenure(self, geography: str) -> ONSObservationsResponse:
        """Census 2021 TS044 — housing tenure (owner-occupied, rented, etc.)."""
        return await self.census_table("TS044", geography)

    async def ethnic_group(self, geography: str) -> ONSObservationsResponse:
        """Census 2021 TS021 — ethnic group."""
        return await self.census_table("TS021", geography)

    async def qualifications(self, geography: str) -> ONSObservationsResponse:
        """Census 2021 TS067 — highest level of qualification."""
        return await self.census_table("TS067", geography)
