"""data.police.uk client."""

from __future__ import annotations

from uk_property_apis.police.client import PoliceClient, crime_stats_near
from uk_property_apis.police.models import (
    Crime,
    CrimeCategory,
    CrimeLocation,
    CrimeMonthCategoryCount,
    CrimeStatsSummary,
    CrimeStreet,
    Force,
    Neighbourhood,
    OutcomeStatus,
)

__all__ = [
    "Crime",
    "CrimeCategory",
    "CrimeLocation",
    "CrimeMonthCategoryCount",
    "CrimeStatsSummary",
    "CrimeStreet",
    "Force",
    "Neighbourhood",
    "OutcomeStatus",
    "PoliceClient",
    "crime_stats_near",
]
