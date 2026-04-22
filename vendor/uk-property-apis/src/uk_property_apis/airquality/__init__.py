"""DEFRA UK-AIR Sensor Observation Service client."""

from __future__ import annotations

from uk_property_apis.airquality.client import (
    DEFAULT_POLLUTANTS,
    DefraAirQualityClient,
    latest_nearby,
)
from uk_property_apis.airquality.models import (
    AirQualityReading,
    AirQualityStation,
    Pollutant,
    StationProximity,
    canonical_pollutant,
)

__all__ = [
    "DEFAULT_POLLUTANTS",
    "AirQualityReading",
    "AirQualityStation",
    "DefraAirQualityClient",
    "Pollutant",
    "StationProximity",
    "canonical_pollutant",
    "latest_nearby",
]
