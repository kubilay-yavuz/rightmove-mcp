"""Pydantic models for the UKHSA / BGS Indicative Atlas of Radon.

The atlas is a 1-km grid published jointly by UKHSA and BGS and
hosted on ``map.bgs.ac.uk``. Every grid square carries:

* ``CLASS_MAX`` — an integer 1..6 ranking the *maximum* radon potential
  anywhere within the tile. The exact bucket definitions come from
  UKHSA (`ukradon.org <https://www.ukradon.org/information/ukmaps>`__):

  ==========  ==========================================
  CLASS_MAX   % of homes >= Action Level (200 Bq m-3)
  ==========  ==========================================
  1           < 1 %
  2           1 - < 3 %
  3           3 - < 5 %
  4           5 - < 10 %
  5           10 - < 30 %
  6           >= 30 %
  ==========  ==========================================

* ``TILE`` — the 4-character BNG grid reference (e.g. ``TQ3080``).
* ``Description`` — the UKHSA-supplied narrative for the band.
* ``VERSION`` — the dataset release (currently ``Radon Indicative
  Atlas GB v3``).

A "Radon Affected Area" in the statutory sense is defined by UKHSA as
any square with >= 1 % homes at or above the Action Level, i.e.
``CLASS_MAX >= 2``. We expose that as a derived boolean so downstream
consumers don't have to relearn the banding.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RadonBand = Literal["very_low", "low", "low_moderate", "moderate", "moderate_high", "high"]

_BAND_BY_CLASS: dict[int, RadonBand] = {
    1: "very_low",
    2: "low",
    3: "low_moderate",
    4: "moderate",
    5: "moderate_high",
    6: "high",
}

_PERCENT_BY_CLASS: dict[int, dict[str, float | None]] = {
    1: {"min_pct": 0.0, "max_pct": 1.0},
    2: {"min_pct": 1.0, "max_pct": 3.0},
    3: {"min_pct": 3.0, "max_pct": 5.0},
    4: {"min_pct": 5.0, "max_pct": 10.0},
    5: {"min_pct": 10.0, "max_pct": 30.0},
    6: {"min_pct": 30.0, "max_pct": None},
}


class RadonPotential(BaseModel):
    """Radon Indicative Atlas potential at a single point.

    When the query lands outside the covered atlas (e.g. the coast or
    offshore), ``class_max`` is ``None`` and all derived fields fall
    back to ``None`` / ``False``; callers should treat that as "no
    data" rather than "safe".
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    class_max: int | None = Field(
        default=None,
        ge=1,
        le=6,
        description=(
            "UKHSA 1..6 band. Higher = more homes over the Action "
            "Level anywhere in the covering 1 km grid square."
        ),
    )
    band: RadonBand | None = Field(
        default=None,
        description="Human-readable band ('very_low' .. 'high').",
    )
    affected_area: bool = Field(
        default=False,
        description=(
            "True if ``class_max >= 2`` — UKHSA's statutory definition "
            "of a 'Radon Affected Area'."
        ),
    )
    min_pct: float | None = Field(
        default=None,
        description=(
            "Lower bound of the % of homes at / above the Action Level "
            "for this band (inclusive)."
        ),
    )
    max_pct: float | None = Field(
        default=None,
        description=(
            "Upper bound of the % of homes at / above the Action Level "
            "for this band (exclusive). ``None`` for the top band "
            "(``class_max == 6`` → 30% +)."
        ),
    )
    tile: str | None = Field(
        default=None,
        description="4- or 6-char British National Grid tile, e.g. 'TQ3080'.",
    )
    description: str | None = Field(
        default=None,
        description="UKHSA-supplied narrative from the ArcGIS attribute.",
    )
    version: str | None = Field(
        default=None,
        description="Dataset release, e.g. 'Radon Indicative Atlas GB v3'.",
    )
    source_url: str | None = Field(
        default=None,
        description="Upstream REST layer URL the attributes came from.",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw ArcGIS attribute row for downstream inspection.",
    )

    @field_validator("class_max", mode="before")
    @classmethod
    def _coerce_class_max(cls, value: object) -> object:
        if value is None or isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return value

    @classmethod
    def from_arcgis_attributes(
        cls,
        attrs: dict[str, Any] | None,
        *,
        source_url: str | None = None,
    ) -> RadonPotential:
        """Build a :class:`RadonPotential` from a raw ArcGIS attribute row.

        ``attrs`` is the ``features[0]["attributes"]`` dict returned by
        the BGS ArcGIS ``/query`` endpoint. ``None`` (or an empty dict)
        means the point fell outside the atlas — we return an empty
        model so callers get a stable shape either way.
        """

        if not attrs:
            return cls(source_url=source_url)

        class_raw = attrs.get("CLASS_MAX") if attrs.get("CLASS_MAX") is not None else attrs.get("class_max")
        class_max: int | None
        if isinstance(class_raw, (int, float)):
            class_max = int(class_raw)
        elif isinstance(class_raw, str) and class_raw.strip().isdigit():
            class_max = int(class_raw.strip())
        else:
            class_max = None

        band: RadonBand | None = _BAND_BY_CLASS.get(class_max) if class_max else None
        pcts = _PERCENT_BY_CLASS.get(class_max, {}) if class_max else {}
        return cls(
            class_max=class_max,
            band=band,
            affected_area=bool(class_max and class_max >= 2),
            min_pct=pcts.get("min_pct"),
            max_pct=pcts.get("max_pct"),
            tile=attrs.get("TILE") or attrs.get("tile"),
            description=attrs.get("Description") or attrs.get("description"),
            version=attrs.get("VERSION") or attrs.get("version"),
            source_url=source_url,
            properties=dict(attrs),
        )


__all__ = [
    "RadonBand",
    "RadonPotential",
]
