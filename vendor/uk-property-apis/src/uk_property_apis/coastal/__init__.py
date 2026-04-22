"""EA coastal erosion NCERM WFS client."""

from __future__ import annotations

from uk_property_apis.coastal.client import CoastalErosionClient
from uk_property_apis.coastal.models import (
    ClimateUplift,
    ErosionZone,
    HorizonYear,
    ManagementScenario,
    ShorelinePrediction,
    SMPPolicy,
)

__all__ = [
    "ClimateUplift",
    "CoastalErosionClient",
    "ErosionZone",
    "HorizonYear",
    "ManagementScenario",
    "SMPPolicy",
    "ShorelinePrediction",
]
