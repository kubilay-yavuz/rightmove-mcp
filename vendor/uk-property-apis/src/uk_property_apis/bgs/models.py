"""Pydantic models for BGS geohazard ArcGIS REST endpoints.

Scope and provenance
====================

The old BGS ``BGS_Hazards/GeoHazardsEngland`` REST service (which shipped
six A–E graded hazards — shrink-swell, ground dissolution, compressible
ground, landslide, collapsible deposits, running sand) was withdrawn from
public access in 2024. Four of those layers (ground dissolution,
compressible ground, collapsible deposits, running sand) now only ship
via the paid BGS Data subscription feed.

These models cover what remains **free and programmatically queryable**:

* ``GeoClimate Basic`` shrink-swell ratings across three climate horizons
  (2030 / 2050 / 2080). Values are a coarse ``{None, Possible, Probable}``
  trichotomy rather than the old A–E grade.
* ``GeoClimate UKCP18`` shrink-swell ratings at 2030 and 2070 using the
  UKCP18 climate projection ensemble.
* The ``GeoIndex_Onshore/hazards`` landslide inventory — a point feature
  class of historical landslide events (not a rating).

Consumers that still want an aggregate "is this a subsidence risk?"
should key off :attr:`GeohazardAssessment.has_shrink_swell_risk` or
:attr:`GeohazardAssessment.has_landslide_risk` — they preserve the
pre-migration boolean surface while the underlying data axes have
changed.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ShrinkSwellClass(StrEnum):
    """GeoClimate shrink-swell susceptibility class.

    The GeoClimate Basic layers ship with a ``CLASS`` of ``"None"`` /
    ``"Possible"`` / ``"Probable"``; the UKCP18 layers additionally use
    ``"Improbable"`` for the "climate-change-is-not-expected-to-move-
    the-needle" background class. Mapping all four to an enum keeps
    downstream consumers on a clean dispatch target instead of
    free-form strings.
    """

    NONE = "None"
    IMPROBABLE = "Improbable"
    POSSIBLE = "Possible"
    PROBABLE = "Probable"
    UNKNOWN = "Unknown"


class ClimateProjection(StrEnum):
    """Which underlying climate-projection family a shrink-swell rating uses.

    * ``basic`` → BGS GeoClimate Basic v1 (averaged multi-model mean).
    * ``ukcp18`` → GeoClimate UKCP18 (UKCP18 probabilistic ensemble).
    """

    BASIC = "basic"
    UKCP18 = "ukcp18"


class ShrinkSwellHorizon(IntEnum):
    """Supported climate horizons for shrink-swell.

    Horizons available per projection:

    * ``basic`` → 2030, 2050, 2080.
    * ``ukcp18`` → 2030, 2070.
    """

    H_2030 = 2030
    H_2050 = 2050
    H_2070 = 2070
    H_2080 = 2080


class ShrinkSwellAssessment(BaseModel):
    """A single shrink-swell rating for one ``(projection, horizon)`` cell."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    projection: ClimateProjection = Field(
        ...,
        description=(
            "Which BGS climate projection supplied the rating — ``basic`` for "
            "the multi-model mean or ``ukcp18`` for the UKCP18 ensemble."
        ),
    )
    horizon_year: ShrinkSwellHorizon = Field(
        ...,
        description="Decade-year the rating targets (e.g. 2050 for the 2046–2075 bin).",
    )
    susceptibility: ShrinkSwellClass = Field(
        default=ShrinkSwellClass.UNKNOWN,
        description=(
            "Coarse shrink-swell susceptibility class derived from the "
            "``CLASS`` field on the GeoClimate polygon that contains the "
            "query point."
        ),
    )
    legend: str | None = Field(
        default=None,
        description="The long-form description of the shrink-swell class (the ``LEGEND`` field).",
    )
    version: str | None = Field(
        default=None,
        description="BGS-assigned version string for the layer (``VERSION`` field).",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw ArcGIS ``attributes`` payload from the responding layer.",
    )


class LandslideEvent(BaseModel):
    """One historical landslide event from the BGS landslide inventory.

    Events are point features with a spatial ``+/- metres`` uncertainty.
    Only the fields downstream consumers have expressed interest in are
    surfaced; everything else is preserved in :attr:`properties`.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    landslide_id: int | None = Field(
        default=None,
        description="BGS ``LS_ID`` — stable integer identifier for the inventory event.",
    )
    landslide_number: int | None = Field(
        default=None,
        description=(
            "The ``LANDSLIDE_NUMBER`` field — an alternate BGS identifier that "
            "often appears in paper records."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Human-readable landslide name (``LANDSLIDE_NAME``).",
    )
    locality: str | None = Field(
        default=None,
        description="Locality / area description (``LOCALITY_DETAILS``).",
    )
    first_known_year: str | None = Field(
        default=None,
        description="Year the landslide was first recorded (``FIRST_KNOWN_DATE_YEAR``).",
    )
    last_known_year: str | None = Field(
        default=None,
        description="Most recent reactivation year (``LAST_KNOWN_DATE_YEAR``).",
    )
    uncertainty_m: float | None = Field(
        default=None,
        description=(
            "``PLUS_OR_MINUS_METRES`` — spatial uncertainty on the recorded "
            "centroid. Large values usually flag legacy paper records."
        ),
    )
    latitude: float | None = Field(
        default=None,
        description="WGS-84 latitude of the event centroid.",
    )
    longitude: float | None = Field(
        default=None,
        description="WGS-84 longitude of the event centroid.",
    )
    distance_km: float | None = Field(
        default=None,
        description=(
            "Great-circle distance (km) from the query point, populated by "
            ":meth:`BGSClient.landslides_near`."
        ),
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw ArcGIS ``attributes`` payload for the landslide record.",
    )


class GeohazardAssessment(BaseModel):
    """Aggregate shrink-swell trajectory + landslide inventory at one point.

    This is deliberately a **coarser** contract than the pre-2024 BGS
    model: the six A–E hazard ratings no longer exist in the free feed,
    so any downstream consumer that wants a single "is this a subsidence
    risk?" flag should rely on :attr:`has_shrink_swell_risk` and / or
    :attr:`has_landslide_risk` rather than fishing for a specific layer.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    shrink_swell_trajectory: list[ShrinkSwellAssessment] = Field(
        default_factory=list,
        description=(
            "All available shrink-swell ``(projection, horizon)`` ratings for "
            "this point — one entry per layer queried. Ordered oldest horizon "
            "first so consumers can scan for ``None → Possible → Probable`` "
            "transitions."
        ),
    )
    landslides_nearby: list[LandslideEvent] = Field(
        default_factory=list,
        description=(
            "Historical landslide events within the requested search radius, "
            "ordered by ascending great-circle distance."
        ),
    )
    landslide_search_radius_km: float | None = Field(
        default=None,
        description="Radius (km) used for the landslide inventory query.",
    )
    has_shrink_swell_risk: bool = Field(
        default=False,
        description=(
            "``True`` if **any** horizon / projection returned a ``Possible`` "
            "or ``Probable`` class. Preserves the pre-migration binary flag "
            "while the underlying data is coarser."
        ),
    )
    has_landslide_risk: bool = Field(
        default=False,
        description=(
            "``True`` if one or more landslide inventory events fall inside "
            "the requested radius."
        ),
    )


__all__ = [
    "ClimateProjection",
    "GeohazardAssessment",
    "LandslideEvent",
    "ShrinkSwellAssessment",
    "ShrinkSwellClass",
    "ShrinkSwellHorizon",
]
