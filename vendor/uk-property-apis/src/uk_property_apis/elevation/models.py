"""Pydantic model for elevation lookups."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ElevationPoint(BaseModel):
    """One elevation sample at a geographic coordinate.

    ``elevation_m`` is metres above WGS-84 ellipsoid / EGM-2008 geoid
    depending on the DEM — Open-Meteo documents the source as
    "Copernicus GLO-90 (Open-Meteo processed)" which is EGM-2008
    orthometric, i.e. metres above mean sea level, not above the
    ellipsoid. For the UK that difference is tiny (<1m) and we surface
    ``source`` in case a caller needs to reason about it.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    lat: float = Field(description="WGS-84 latitude (decimal degrees).")
    lng: float = Field(description="WGS-84 longitude (decimal degrees).")
    elevation_m: float | None = Field(
        default=None,
        description=(
            "Elevation in metres, or ``None`` when the upstream DEM has "
            "no value for the requested point. Open-Meteo returns "
            "``null`` for points outside its coverage."
        ),
    )
    source: str = Field(
        default="open-meteo",
        description="Name of the elevation data source (provider-level tag).",
    )


__all__ = ["ElevationPoint"]
