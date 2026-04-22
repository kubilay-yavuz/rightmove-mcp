"""Async client for data.police.uk."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import date

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import NotFoundError
from uk_property_apis.police.models import (
    Crime,
    CrimeCategory,
    CrimeMonthCategoryCount,
    CrimeStatsSummary,
    Force,
    Neighbourhood,
)


class PoliceClient(BaseAPIClient):
    """Client for https://data.police.uk/api/ — UK police open data."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url="https://data.police.uk/api/",
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    async def street_crimes(self, lat: float, lng: float, *, month: str) -> list[Crime]:
        """Return all crimes within ~1 mile of ``lat``/``lng`` for ``month`` (``YYYY-MM``)."""

        params = {"lat": lat, "lng": lng, "date": month}
        rows = await self._get_list("crimes-street/all-crime", params=params)
        return [self._validate_model(Crime, row) for row in rows]

    async def locate_neighbourhood(self, lat: float, lng: float) -> Neighbourhood:
        """Resolve the policing neighbourhood covering a coordinate."""

        params = {"q": f"{lat},{lng}"}
        payload = await self._get("locate-neighbourhood", params=params)
        return self._validate_model(Neighbourhood, payload)

    async def crimes_no_location(self, *, category: str, force: str, month: str) -> list[Crime]:
        """Return crimes without location for a force/category/month."""

        params = {"category": category, "force": force, "date": month}
        rows = await self._get_list("crimes-no-location", params=params)
        return [self._validate_model(Crime, row) for row in rows]

    async def forces(self) -> list[Force]:
        """List all police forces."""

        rows = await self._get_list("forces")
        return [self._validate_model(Force, row) for row in rows]

    async def crime_categories(self, *, month: str) -> list[CrimeCategory]:
        """Return crime categories valid for ``month`` (``YYYY-MM``)."""

        params = {"date": month}
        rows = await self._get_list("crime-categories", params=params)
        return [self._validate_model(CrimeCategory, row) for row in rows]

    async def crimes_at_location(self, *, month: str, location_id: int) -> list[Crime]:
        """Return crimes recorded at a named snap-to-grid location."""

        params = {"date": month, "location_id": location_id}
        rows = await self._get_list("crimes-at-location", params=params)
        return [self._validate_model(Crime, row) for row in rows]

    async def crime_stats_near(self, lat: float, lng: float, *, months_back: int = 12) -> CrimeStatsSummary:
        """Aggregate street crimes by category and month for the last ``months_back`` months."""

        def _prior_months(start: date, count: int) -> list[str]:
            y, m = start.year, start.month
            labels: list[str] = []
            for _ in range(count):
                labels.append(f"{y:04d}-{m:02d}")
                if m == 1:
                    y -= 1
                    m = 12
                else:
                    m -= 1
            return labels

        # Data is typically published with a 1-2 month lag, so the current month
        # often 404s. Request ``months_back + 1`` labels starting from last month
        # and keep whichever ones return data.
        last_month_start = date.today().replace(day=1)
        if last_month_start.month == 1:
            last_month_start = last_month_start.replace(year=last_month_start.year - 1, month=12)
        else:
            last_month_start = last_month_start.replace(month=last_month_start.month - 1)
        candidate_months = _prior_months(last_month_start, months_back)

        by_category: dict[str, int] = defaultdict(int)
        detail: list[CrimeMonthCategoryCount] = []
        per_month_counts: dict[tuple[str, str], int] = defaultdict(int)
        available_months: list[str] = []

        for m in candidate_months:
            try:
                crimes = await self.street_crimes(lat, lng, month=m)
            except NotFoundError:
                # Month not yet published for this area; skip.
                continue
            available_months.append(m)
            for c in crimes:
                by_category[c.category] += 1
                per_month_counts[(m, c.category)] += 1

        for (m, cat), n in sorted(per_month_counts.items()):
            detail.append(CrimeMonthCategoryCount(month=m, category=cat, count=n))

        return CrimeStatsSummary(
            months=available_months,
            by_category=dict(by_category),
            by_month_category=detail,
        )


async def crime_stats_near(lat: float, lng: float, *, months_back: int = 12) -> CrimeStatsSummary:
    """Convenience wrapper around :meth:`PoliceClient.crime_stats_near`."""

    async with PoliceClient() as client:
        return await client.crime_stats_near(lat, lng, months_back=months_back)
