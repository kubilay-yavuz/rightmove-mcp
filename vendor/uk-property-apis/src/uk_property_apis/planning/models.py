"""Models for planning.data.gov.uk entity API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Entity(BaseModel):
    """Generic planning entity row.

    The real API is loose about types - ``organisation-entity`` arrives as a
    string, ``geometry`` can be an empty string when no polygon is attached,
    and ``entity`` is occasionally a string too. We normalise on the way in.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    entity: int | None = None
    name: str | None = None
    dataset: str | None = None
    typology: str | None = None
    geometry: dict[str, Any] | str | None = None
    point: str | None = None
    organisation_entity: int | None = Field(default=None, alias="organisation-entity")
    prefix: str | None = None
    reference: str | None = None
    entry_date: str | None = Field(default=None, alias="entry-date")
    start_date: str | None = Field(default=None, alias="start-date")
    end_date: str | None = Field(default=None, alias="end-date")

    @field_validator("entity", "organisation_entity", mode="before")
    @classmethod
    def _coerce_int(cls, v: object) -> int | None:
        if v is None or v == "":
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return None
        return None

    @field_validator("geometry", mode="before")
    @classmethod
    def _normalise_geometry(cls, v: object) -> dict[str, Any] | str | None:
        if v is None or v == "":
            return None
        return v  # type: ignore[return-value]


type ListedBuildingEntity = Entity
type ConservationAreaEntity = Entity
type Article4DirectionAreaEntity = Entity
type FloodRiskZoneEntity = Entity
type GreenBeltEntity = Entity
type TreePreservationZoneEntity = Entity


class EntityPage(BaseModel):
    """Paginated entity response."""

    model_config = ConfigDict(extra="allow")

    entities: list[Entity] = Field(default_factory=list)
    links: dict[str, Any] = Field(default_factory=dict)
    count: int | None = None
