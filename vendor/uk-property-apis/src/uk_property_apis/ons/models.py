"""Pydantic models for the ONS Beta API (``api.beta.ons.gov.uk``)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ONSDimension(BaseModel):
    """Dataset dimension metadata."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    label: str | None = None
    description: str | None = None


class ONSDatasetVersion(BaseModel):
    """Dataset edition/version metadata block."""

    model_config = ConfigDict(extra="allow")

    id: str | None = None
    edition: str | None = None
    version: int | None = None
    release_date: str | None = Field(default=None, alias="release_date")
    last_updated: str | None = Field(default=None, alias="last_updated")
    dimensions: list[ONSDimension] = Field(default_factory=list)
    state: str | None = None


class ONSObservationRow(BaseModel):
    """Single observation row."""

    model_config = ConfigDict(extra="allow")

    observation: str | None = None
    metadata: dict[str, Any] | None = None


class ONSObservationsResponse(BaseModel):
    """Observations payload for a filtered slice.

    The Beta API returns two different shapes:

    * The deprecated ``/observations`` endpoint (now mostly 5xx'ing for
      Census 2021 tables) used a list of per-row dicts.
    * The current ``/json`` endpoint returns a flat
      ``observations: list[float | int]`` cube plus a richer
      ``dimensions`` block describing the axis labels.

    We accept both — callers look at ``dimensions`` to interpret the
    flat list.
    """

    model_config = ConfigDict(extra="allow")

    observations: list[Any] = Field(default_factory=list)
    dimensions: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=dict)
    total_observations: int | None = Field(default=None, alias="total_observations")
    blocked_areas: int | None = None
    total_areas: int | None = None
    areas_returned: int | None = None
    offset: int | None = None
    limit: int | None = None
    links: dict[str, Any] | None = None
