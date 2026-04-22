"""Pydantic models for data.police.uk."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CrimeStreet(BaseModel):
    """Street name object embedded on a crime record."""

    model_config = ConfigDict(extra="allow")

    id: int | None = None
    name: str | None = None


class CrimeLocation(BaseModel):
    """Geographic location for a recorded crime."""

    model_config = ConfigDict(extra="allow")

    latitude: str | None = None
    longitude: str | None = None
    street: CrimeStreet | None = None
    category: str | None = None


class OutcomeStatus(BaseModel):
    """Latest known outcome for an investigation."""

    model_config = ConfigDict(extra="allow")

    category: str | None = None


class Crime(BaseModel):
    """Single street-level crime record."""

    model_config = ConfigDict(extra="allow")

    category: str
    persistent_id: str | None = None
    location_subtype: str | None = None
    id: int | None = None
    location_type: str | None = None
    location: CrimeLocation | None = None
    context: str | None = None
    outcome_status: OutcomeStatus | None = None
    month: str | None = None


class Neighbourhood(BaseModel):
    """Policing neighbourhood metadata."""

    model_config = ConfigDict(extra="allow")

    neighbourhood: str | None = None
    force: str | None = None
    url_force: str | None = Field(default=None, alias="url_force")
    url_neighbourhood: str | None = Field(default=None, alias="url_neighbourhood")


class Force(BaseModel):
    """Police force listing entry."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str


class CrimeCategory(BaseModel):
    """Crime category code and readable name."""

    model_config = ConfigDict(extra="allow")

    url: str | None = None
    name: str


class CrimeMonthCategoryCount(BaseModel):
    """Aggregated counts for one month and category."""

    month: str
    category: str
    count: int


class CrimeStatsSummary(BaseModel):
    """Output of :func:`crime_stats_near`."""

    months: list[str]
    by_category: dict[str, int]
    by_month_category: list[CrimeMonthCategoryCount]
