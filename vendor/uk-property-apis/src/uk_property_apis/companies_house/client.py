"""Async client for Companies House REST API."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.companies_house.models import (
    ChargesResponse,
    Company,
    CompanySearchResponse,
    FilingHistoryResponse,
    OfficerAppointmentsResponse,
    OfficerSearchResponse,
    OfficersResponse,
    PSCListResponse,
)


def _api_key_from_env() -> str | None:
    return os.environ.get("COMPANIES_HOUSE_API_KEY")


class CompaniesHouseClient(BaseAPIClient):
    """Client for https://api.company-information.service.gov.uk/ (API key Basic auth)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        auth: httpx.Auth | None = None,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        key = api_key if api_key is not None else _api_key_from_env()
        resolved_auth = auth if auth is not None else (httpx.BasicAuth(key, "") if key else None)
        if resolved_auth is None:
            msg = "Companies House API key required: pass api_key=... or set COMPANIES_HOUSE_API_KEY"
            raise ValueError(msg)
        super().__init__(
            base_url="https://api.company-information.service.gov.uk/",
            auth=resolved_auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    async def search_companies(self, query: str, *, items_per_page: int = 20) -> CompanySearchResponse:
        """Search companies by free-text ``query``."""

        params = {"q": query, "items_per_page": items_per_page}
        payload = await self._get("search/companies", params=params)
        return self._validate_model(CompanySearchResponse, payload)

    async def get_company(self, company_number: str) -> Company:
        """Return the full profile for ``company_number``."""

        payload = await self._get(f"company/{company_number}")
        return self._validate_model(Company, payload)

    async def get_officers(self, company_number: str, *, items_per_page: int = 35) -> OfficersResponse:
        """Return officers for a company."""

        params = {"items_per_page": items_per_page}
        payload = await self._get(f"company/{company_number}/officers", params=params)
        return self._validate_model(OfficersResponse, payload)

    async def get_psc(self, company_number: str, *, items_per_page: int = 35) -> PSCListResponse:
        """Return persons with significant control."""

        params = {"items_per_page": items_per_page}
        payload = await self._get(f"company/{company_number}/persons-with-significant-control", params=params)
        return self._validate_model(PSCListResponse, payload)

    async def get_filing_history(self, company_number: str, *, items_per_page: int = 25) -> FilingHistoryResponse:
        """Return filing history."""

        params = {"items_per_page": items_per_page}
        payload = await self._get(f"company/{company_number}/filing-history", params=params)
        return self._validate_model(FilingHistoryResponse, payload)

    async def get_charges(self, company_number: str, *, items_per_page: int = 25) -> ChargesResponse:
        """Return registered charges (mortgages / debentures)."""

        params = {"items_per_page": items_per_page}
        payload = await self._get(f"company/{company_number}/charges", params=params)
        return self._validate_model(ChargesResponse, payload)

    async def search_officers(
        self, query: str, *, items_per_page: int = 20
    ) -> OfficerSearchResponse:
        """Free-text officer search across all companies.

        The only way to resolve ``"John Smith"`` to a specific officer ID
        before calling :meth:`get_officer_appointments`.
        """

        params = {"q": query, "items_per_page": items_per_page}
        payload = await self._get("search/officers", params=params)
        return self._validate_model(OfficerSearchResponse, payload)

    async def get_officer_appointments(
        self, officer_id: str, *, items_per_page: int = 35
    ) -> OfficerAppointmentsResponse:
        """Return every company this officer is / was appointed to.

        The officer ID is the stable identifier from ``/officers/<id>`` —
        extract it from :attr:`Officer.officer_id` or
        :attr:`OfficerSearchItem.officer_id`.
        """

        params = {"items_per_page": items_per_page}
        payload = await self._get(
            f"officers/{officer_id}/appointments", params=params
        )
        return self._validate_model(OfficerAppointmentsResponse, payload)
