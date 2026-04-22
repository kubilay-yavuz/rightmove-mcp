"""Pydantic models for the NCERM 2024 coastal erosion WFS.

The Environment Agency's National Coastal Erosion Risk Map (NCERM) is the
authoritative source for coastal erosion + shoreline-management-plan data
for England. The 2024 release (published Jan 2025, served at
``/spatialdata/ncern-national-2024/wfs``) slices the data by:

* **horizon year**: ``2055`` (near-term, ~30 years out) or ``2105``
  (long-term, ~80 years out).
* **climate-change uplift**: ``0`` (no uplift), ``70``
  (central 70% sea-level rise scenario), ``95`` (upper-bound 95%).
* **management assumption**: ``SMP`` (shoreline-management plan as-adopted)
  or ``NFI`` ("no further intervention", i.e. what would happen with zero
  maintenance).

Rather than try to coerce that into the old ``epoch=20|50|100`` surface we
expose the NCERM axes directly and let the climate-risk actor pick which
combinations it wants to hydrate.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HorizonYear(IntEnum):
    """NCERM prediction horizons (years). Only 2055 and 2105 are published."""

    NEAR_TERM = 2055
    LONG_TERM = 2105


class ClimateUplift(IntEnum):
    """NCERM sea-level-rise climate-uplift scenarios.

    * ``NONE`` — baseline, no climate change uplift applied.
    * ``CENTRAL`` — 70% confidence upper-plausible-range SLR uplift
      (UKCP18 H++ at ~70% probability).
    * ``UPPER`` — 95% confidence upper-plausible-range SLR uplift.
    """

    NONE = 0
    CENTRAL = 70
    UPPER = 95


class ManagementScenario(StrEnum):
    """Which management assumption the prediction applies under.

    * ``SMP`` uses the as-adopted Shoreline Management Plan policy
      (``Hold The Line`` / ``Managed Realignment`` / etc).
    * ``NFI`` assumes **N**o **F**urther **I**ntervention — i.e. the
      erosion footprint if defences were left to decay. Always a
      pessimistic bound.
    """

    SMP = "SMP"
    NFI = "NFI"


class SMPPolicy(StrEnum):
    """NCERM SMP policy options.

    Values come straight from the ``mt_smp`` / ``lt_smp`` columns of the
    SMP layer; the ``UNKNOWN`` variant catches anything the EA ships in a
    future refresh that we haven't seen yet.
    """

    HOLD_THE_LINE = "Hold The Line"
    ADVANCE_THE_LINE = "Advance The Line"
    MANAGED_REALIGNMENT = "Managed Realignment"
    NO_ACTIVE_INTERVENTION = "No Active Intervention"
    UNKNOWN = "Unknown"


class ErosionZone(BaseModel):
    """A land parcel flagged as at-risk by the NCERM instability-zone layer.

    These zones are a small, curated set (≈80 polygons nationally) identifying
    coastline where the EA has adopted a specific SMP management decision and
    the land behind the cliff/beach is explicitly within the predicted
    recession footprint. Think of them as "hot spots" rather than the full
    erosion footprint — the per-frontage :class:`ShorelinePrediction` records
    carry the continuous year-by-year distances.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    zone_id: str | None = Field(
        None,
        description="Local zone identifier (e.g. '02'); unique within the ``local_authority``.",
    )
    local_authority: str | None = Field(
        None,
        description="Coastal local authority for this zone (e.g. 'Scarborough').",
    )
    smp_name: str | None = Field(
        None,
        description="Parent SMP unit (e.g. 'The Tyne to Flamborough Head').",
    )
    policy_units: list[str] = Field(
        default_factory=list,
        description="Non-empty list of SMP policy-unit references (smp_pu1..smp_pu5) that cover this zone.",
    )
    rear_scarp: str | None = Field(
        None,
        description="Whether the NCERM rear-scarp flag is set (Y/N); informational.",
    )
    properties: dict[str, Any] = Field(default_factory=dict)


class ShorelinePrediction(BaseModel):
    """A single NCERM SMP / NFI prediction at one frontage for one (horizon, climate).

    Each NCERM feature is a frontage polygon carrying the predicted recession
    distance (m landward) for the selected ``horizon_year`` and
    ``climate_uplift``, plus the adopted medium-term + long-term SMP policy.
    ``policy_unit`` ties back to the SMP policy-unit code (e.g.
    ``6b-01-10``).
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    management_scenario: ManagementScenario = Field(
        ManagementScenario.SMP,
        description="Which NCERM layer family this came from (SMP or NFI).",
    )
    horizon_year: HorizonYear = Field(
        ...,
        description="The year this prediction targets (2055 or 2105).",
    )
    climate_uplift: ClimateUplift = Field(
        ...,
        description="Sea-level-rise uplift scenario applied to the recession model.",
    )

    frontage_id: int | None = None
    smp_name: str | None = None
    policy_unit: str | None = None
    medium_term_policy: SMPPolicy | None = Field(
        None,
        description="Short- to medium-term SMP policy (~20 years out). Only populated for SMP layers.",
    )
    long_term_policy: SMPPolicy | None = Field(
        None,
        description="Long-term SMP policy (~100 years out). Only populated for SMP layers.",
    )
    predicted_erosion_distance_m: float | None = Field(
        None,
        description="Predicted landward recession (meters) at the frontage for this (horizon, climate).",
    )
    def_type: str | None = Field(
        None,
        description="Defence type category (``Open Coast`` / ``Defended`` / etc).",
    )
    properties: dict[str, Any] = Field(default_factory=dict)
