"""Environment Agency flood monitoring client."""

from __future__ import annotations

from uk_property_apis.flood.client import FloodClient, active_floods_near
from uk_property_apis.flood.models import (
    FloodArea,
    FloodListResponse,
    FloodSeverityItem,
    FloodWarning,
    MonitoringStation,
    StageScaleItem,
)

__all__ = [
    "FloodArea",
    "FloodClient",
    "FloodListResponse",
    "FloodSeverityItem",
    "FloodWarning",
    "MonitoringStation",
    "StageScaleItem",
    "active_floods_near",
]
