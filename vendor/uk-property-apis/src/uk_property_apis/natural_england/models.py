"""Pydantic models for Natural England MAGIC WFS."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GreenBeltArea(BaseModel):
    """A green belt designation area."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str | None = None
    area_ha: float | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class SSSIArea(BaseModel):
    """A Site of Special Scientific Interest."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str | None = None
    status: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class AONBArea(BaseModel):
    """An Area of Outstanding Natural Beauty."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class NationalParkArea(BaseModel):
    """A National Park designation area."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class AncientWoodlandArea(BaseModel):
    """An Ancient Woodland designation area."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str | None = None
    category: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class Designations(BaseModel):
    """Aggregate result of all Natural England designation queries at a point."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    is_green_belt: bool = False
    is_sssi: bool = False
    is_aonb: bool = False
    is_national_park: bool = False
    is_ancient_woodland: bool = False
    green_belt: list[GreenBeltArea] = Field(default_factory=list)
    sssi: list[SSSIArea] = Field(default_factory=list)
    aonb: list[AONBArea] = Field(default_factory=list)
    national_parks: list[NationalParkArea] = Field(default_factory=list)
    ancient_woodland: list[AncientWoodlandArea] = Field(default_factory=list)
