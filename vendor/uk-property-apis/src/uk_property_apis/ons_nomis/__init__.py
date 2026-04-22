"""ONS Nomis labour market API client."""

from __future__ import annotations

from uk_property_apis.ons_nomis.client import NomisClient
from uk_property_apis.ons_nomis.models import (
    ClaimantCount,
    EmploymentStats,
    JobDensity,
    NomisDataset,
    NomisObservations,
    PopulationBreakdown,
    WageStats,
)

__all__ = [
    "ClaimantCount",
    "EmploymentStats",
    "JobDensity",
    "NomisClient",
    "NomisDataset",
    "NomisObservations",
    "PopulationBreakdown",
    "WageStats",
]
