"""Natural England MAGIC WFS client."""

from __future__ import annotations

from uk_property_apis.natural_england.client import NaturalEnglandClient
from uk_property_apis.natural_england.models import (
    AONBArea,
    AncientWoodlandArea,
    Designations,
    GreenBeltArea,
    NationalParkArea,
    SSSIArea,
)

__all__ = [
    "AONBArea",
    "AncientWoodlandArea",
    "Designations",
    "GreenBeltArea",
    "NaturalEnglandClient",
    "NationalParkArea",
    "SSSIArea",
]
