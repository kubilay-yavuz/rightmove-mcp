"""Async client for the ONS Nomis labour market API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.ons_nomis.models import (
    ClaimantCount,
    EmploymentStats,
    JobDensity,
    NomisDataset,
    NomisObservations,
    PopulationBreakdown,
    WageStats,
)

_BASE_URL = "https://www.nomisweb.co.uk/api/v01/"

# Dataset IDs and default dimension filters for convenience methods
_CLAIMANT_COUNT_DATASET = "NM_162_1"
_EMPLOYMENT_RATE_DATASET = "NM_17_5"
_MEDIAN_WAGES_DATASET = "NM_99_1"
_POPULATION_BY_AGE_DATASET = "NM_2002_1"
_JOB_DENSITY_DATASET = "NM_57_1"


def _extract_obs_value(obs: dict[str, Any]) -> float | None:
    """Return the numeric observation value from a raw Nomis obs entry."""
    val = obs.get("obs_value", {})
    if isinstance(val, dict):
        raw = val.get("value")
    else:
        raw = val
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _extract_geography(obs: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (geography_code, geography_name) from a raw Nomis obs entry."""
    geo = obs.get("geography", {})
    if isinstance(geo, dict):
        return geo.get("geogcode"), geo.get("description")
    return None, None


class NomisClient(BaseAPIClient):
    """Client for the ONS Nomis labour market API (https://www.nomisweb.co.uk/api/v01/)."""

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

    # ------------------------------------------------------------------
    # Generic escape hatch
    # ------------------------------------------------------------------

    async def observations(
        self,
        dataset_id: str,
        geography: str,
        **dims: Any,
    ) -> NomisObservations:
        """Return raw observations from *dataset_id* for *geography*.

        Additional keyword arguments are forwarded as query-string dimension filters,
        e.g. ``measures="20207,20209"``.
        """
        params: dict[str, Any] = {"geography": geography, **dims}
        payload = await self._get(f"dataset/{dataset_id}.data.json", params=params)
        raw_obs: list[dict[str, Any]] = payload.get("obs", [])
        return NomisObservations(
            dataset_id=dataset_id,
            geography=geography,
            observations=raw_obs,
            total_observations=len(raw_obs),
        )

    # ------------------------------------------------------------------
    # Dataset catalogue
    # ------------------------------------------------------------------

    async def list_datasets(self) -> list[NomisDataset]:
        """Return a list of all datasets available on Nomis."""
        payload = await self._get("dataset/def.sdmx.json")
        # SDMX envelope: {"structure": {"keyfamilies": {"keyfamily": [...]}}}
        keyfamilies = (
            payload
            .get("structure", {})
            .get("keyfamilies", {})
            .get("keyfamily", [])
        )
        datasets: list[NomisDataset] = []
        for kf in keyfamilies:
            name_obj = kf.get("name", {})
            if isinstance(name_obj, list):
                name_text = name_obj[0].get("value") if name_obj else None
            elif isinstance(name_obj, dict):
                name_text = name_obj.get("value")
            else:
                name_text = str(name_obj) if name_obj else None
            desc_obj = kf.get("description", {})
            if isinstance(desc_obj, list):
                desc_text = desc_obj[0].get("value") if desc_obj else None
            elif isinstance(desc_obj, dict):
                desc_text = desc_obj.get("value")
            else:
                desc_text = str(desc_obj) if desc_obj else None
            datasets.append(
                NomisDataset(
                    id=kf.get("id"),
                    name=name_text,
                    description=desc_text,
                )
            )
        return datasets

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    async def claimant_count(self, geography: str) -> ClaimantCount:
        """Return claimant count statistics for *geography*.

        Uses dataset ``NM_162_1`` with ``measures=20207,20209``
        (count and rate respectively).
        """
        result = await self.observations(
            _CLAIMANT_COUNT_DATASET,
            geography,
            measures="20207,20209",
        )
        geo_code: str | None = None
        geo_name: str | None = None
        claimants: int | None = None
        rate: float | None = None

        for obs in result.observations:
            geo_code, geo_name = _extract_geography(obs)
            measure = obs.get("measures", {})
            measure_id = measure.get("id") if isinstance(measure, dict) else None
            val = _extract_obs_value(obs)
            if measure_id == "20207" and val is not None:
                claimants = int(val)
            elif measure_id == "20209":
                rate = val

        return ClaimantCount(
            geography_code=geo_code,
            geography_name=geo_name,
            claimants=claimants,
            rate=rate,
        )

    async def employment_rate(self, geography: str) -> EmploymentStats:
        """Return employment / unemployment / inactivity rates for *geography*.

        Uses dataset ``NM_17_5`` with ``variable=45,18,284``.
        """
        result = await self.observations(
            _EMPLOYMENT_RATE_DATASET,
            geography,
            variable="45,18,284",
        )
        geo_code: str | None = None
        geo_name: str | None = None
        employment_rate: float | None = None
        unemployment_rate: float | None = None
        economic_inactivity_rate: float | None = None

        for obs in result.observations:
            geo_code, geo_name = _extract_geography(obs)
            variable = obs.get("variable", {})
            var_id = variable.get("id") if isinstance(variable, dict) else None
            val = _extract_obs_value(obs)
            if var_id == "45":
                employment_rate = val
            elif var_id == "18":
                unemployment_rate = val
            elif var_id == "284":
                economic_inactivity_rate = val

        return EmploymentStats(
            geography_code=geo_code,
            geography_name=geo_name,
            employment_rate=employment_rate,
            unemployment_rate=unemployment_rate,
            economic_inactivity_rate=economic_inactivity_rate,
        )

    async def median_wages(self, geography: str) -> WageStats:
        """Return median and mean weekly wages for *geography*.

        Uses dataset ``NM_99_1`` with ``pay=7,8`` (median=7, mean=8).
        """
        result = await self.observations(
            _MEDIAN_WAGES_DATASET,
            geography,
            pay="7,8",
        )
        geo_code: str | None = None
        geo_name: str | None = None
        median_weekly_pay: float | None = None
        mean_weekly_pay: float | None = None

        for obs in result.observations:
            geo_code, geo_name = _extract_geography(obs)
            pay = obs.get("pay", {})
            pay_id = pay.get("id") if isinstance(pay, dict) else None
            val = _extract_obs_value(obs)
            if pay_id == "7":
                median_weekly_pay = val
            elif pay_id == "8":
                mean_weekly_pay = val

        return WageStats(
            geography_code=geo_code,
            geography_name=geo_name,
            median_weekly_pay=median_weekly_pay,
            mean_weekly_pay=mean_weekly_pay,
        )

    async def population_by_age(self, geography: str) -> PopulationBreakdown:
        """Return population broken down by age band for *geography*.

        Uses dataset ``NM_2002_1`` with ``gender=5`` (all persons).
        Age bands are keyed by the ``age`` dimension description from each observation.
        """
        result = await self.observations(
            _POPULATION_BY_AGE_DATASET,
            geography,
            gender="5",
        )
        geo_code: str | None = None
        geo_name: str | None = None
        age_bands: dict[str, int] = {}
        total: int = 0

        for obs in result.observations:
            geo_code, geo_name = _extract_geography(obs)
            age = obs.get("age", {})
            age_label = age.get("description") if isinstance(age, dict) else str(age)
            val = _extract_obs_value(obs)
            if age_label is not None and val is not None:
                age_bands[age_label] = int(val)
                total += int(val)

        return PopulationBreakdown(
            geography_code=geo_code,
            geography_name=geo_name,
            total_population=total if age_bands else None,
            age_bands=age_bands,
        )

    async def job_density(self, geography: str) -> JobDensity:
        """Return job density ratio and total jobs for *geography*.

        Uses dataset ``NM_57_1`` with ``item=3`` (job density ratio).
        """
        result = await self.observations(
            _JOB_DENSITY_DATASET,
            geography,
            item="3",
        )
        geo_code: str | None = None
        geo_name: str | None = None
        density: float | None = None
        total_jobs: int | None = None

        for obs in result.observations:
            geo_code, geo_name = _extract_geography(obs)
            item = obs.get("item", {})
            item_id = item.get("id") if isinstance(item, dict) else None
            val = _extract_obs_value(obs)
            if item_id == "3":
                density = val
            elif item_id == "1" and val is not None:
                total_jobs = int(val)

        return JobDensity(
            geography_code=geo_code,
            geography_name=geo_name,
            job_density=density,
            total_jobs=total_jobs,
        )
