"""BGS geohazard ArcGIS REST client (GeoClimate + landslide inventory)."""

from __future__ import annotations

from uk_property_apis.bgs.client import BGSClient
from uk_property_apis.bgs.models import (
    ClimateProjection,
    GeohazardAssessment,
    LandslideEvent,
    ShrinkSwellAssessment,
    ShrinkSwellClass,
    ShrinkSwellHorizon,
)

__all__ = [
    "BGSClient",
    "ClimateProjection",
    "GeohazardAssessment",
    "LandslideEvent",
    "ShrinkSwellAssessment",
    "ShrinkSwellClass",
    "ShrinkSwellHorizon",
]
