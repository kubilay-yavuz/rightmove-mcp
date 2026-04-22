"""Pydantic models for Environment Agency flood monitoring API."""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class FloodSeverityItem(BaseModel):
    """Severity level reference object.

    Retained for the occasional expanded endpoint; the warnings list endpoint
    we consume now ships a flat integer (1-4) in ``severityLevel`` instead.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    label: str | None = None
    severity_value: int | None = Field(default=None, alias="severityValue")


class FloodWarning(BaseModel):
    """Active flood warning or alert."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    description: str | None = None
    message: str | None = None
    severity: str | None = None
    severity_level: int | None = Field(default=None, alias="severityLevel")
    river_or_sea: str | None = Field(default=None, alias="riverOrSea")
    ea_area_name: str | None = Field(default=None, alias="eaAreaName")
    flood_area_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("floodAreaID", "floodAreaId"),
        serialization_alias="floodAreaID",
    )
    is_tidal: bool | None = Field(default=None, alias="isTidal")
    time_raised: str | None = Field(default=None, alias="timeRaised")
    time_severity_changed: str | None = Field(default=None, alias="timeSeverityChanged")
    time_message_changed: str | None = Field(default=None, alias="timeMessageChanged")

    @field_validator("severity_level", mode="before")
    @classmethod
    def _coerce_severity(cls, value: object) -> object:
        # Historically some expanded endpoints returned a nested object with
        # ``severityValue``. Collapse that to its int so downstream consumers
        # always see a plain code.
        if isinstance(value, dict):
            for key in ("severityValue", "value", "severity_value"):
                if value.get(key) is not None:
                    return value[key]
            return None
        return value


class FloodArea(BaseModel):
    """Polygon / metadata for a flood area."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    county: str | None = None
    river_or_sea: str | None = Field(default=None, alias="riverOrSea")
    label: str | None = None
    description: str | None = None
    ea_area_name: str | None = Field(default=None, alias="eaAreaName")
    area_code: str | None = Field(default=None, alias="areaCode")
    polygon: str | None = None


class StageScaleItem(BaseModel):
    """Stage scale entry on a station."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    value: float | None = None
    label: str | None = None


class MonitoringStation(BaseModel):
    """River / rain gauge or similar monitoring station."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str | None = Field(default=None, alias="@id")
    label: str | None = None
    river_name: str | None = Field(default=None, alias="riverName")
    town: str | None = None
    lat: float | None = None
    long: float | None = None
    status: str | None = None
    # The EA Flood-Monitoring API returns ``stageScale`` as a full object
    # (list of readings) when expanded, but as a **bare URL string** on
    # list endpoints (the ``/id/stations?lat=...&dist=...`` query we use
    # for proximity searches) — callers have to GET that URL to expand
    # it. We store the raw URL untouched in that case so downstream
    # consumers can follow it if they care.
    stage_scale: list[StageScaleItem] | str | None = Field(
        default=None, alias="stageScale"
    )
    measures: list[dict[str, Any]] | str | None = None

    @field_validator("measures", "stage_scale", mode="before")
    @classmethod
    def _tolerate_url_string(cls, value: object) -> object:
        # Accept `None`, URL strings, and lists. Anything else raises.
        if value is None or isinstance(value, (str, list)):
            return value
        raise TypeError(
            f"expected None / list / URL string, got {type(value).__name__}"
        )


class FloodListResponse(BaseModel):
    """Envelope for list endpoints under ``/id/...``."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    context: str | None = Field(default=None, alias="@context")
    meta: dict[str, Any] | None = Field(default=None, alias="meta")
    items: list[dict[str, Any]] = Field(default_factory=list)
