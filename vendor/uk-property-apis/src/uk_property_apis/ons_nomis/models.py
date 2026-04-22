"""Pydantic models for the ONS Nomis labour market API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ClaimantCount(BaseModel):
    """Claimant count statistics for a geography."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    geography_code: str | None = None
    geography_name: str | None = None
    claimants: int | None = None
    rate: float | None = None


class EmploymentStats(BaseModel):
    """Employment, unemployment and economic inactivity rates for a geography."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    geography_code: str | None = None
    geography_name: str | None = None
    employment_rate: float | None = None
    unemployment_rate: float | None = None
    economic_inactivity_rate: float | None = None


class WageStats(BaseModel):
    """Median and mean weekly wage statistics for a geography."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    geography_code: str | None = None
    geography_name: str | None = None
    median_weekly_pay: float | None = None
    mean_weekly_pay: float | None = None


class PopulationBreakdown(BaseModel):
    """Total population and age-band breakdown for a geography."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    geography_code: str | None = None
    geography_name: str | None = None
    total_population: int | None = None
    age_bands: dict[str, int] = Field(default_factory=dict)


class JobDensity(BaseModel):
    """Job density and total job count for a geography."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    geography_code: str | None = None
    geography_name: str | None = None
    job_density: float | None = None
    total_jobs: int | None = None


class NomisDataset(BaseModel):
    """Metadata for a single Nomis dataset."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = None
    name: str | None = None
    description: str | None = None


class NomisObservations(BaseModel):
    """Raw observation results from a Nomis dataset query."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    dataset_id: str | None = None
    geography: str | None = None
    observations: list[dict[str, Any]] = Field(default_factory=list)
    total_observations: int | None = None
