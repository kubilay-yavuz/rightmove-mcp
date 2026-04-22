"""Pydantic models for Companies House REST API."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_OFFICER_ID_RE = re.compile(r"/officers/([^/]+)")
_PSC_ID_RE = re.compile(r"/persons-with-significant-control/[^/]+/([^/?#]+)")


class AddressSnippet(BaseModel):
    """Registered office or service address fragment."""

    model_config = ConfigDict(extra="allow")

    address_line_1: str | None = Field(default=None, alias="address_line_1")
    address_line_2: str | None = Field(default=None, alias="address_line_2")
    locality: str | None = None
    region: str | None = None
    postal_code: str | None = Field(default=None, alias="postal_code")
    country: str | None = None


class CompanySearchItem(BaseModel):
    """One company hit from ``/search/companies``."""

    model_config = ConfigDict(extra="allow")

    company_number: str
    title: str | None = None
    company_status: str | None = Field(default=None, alias="company_status")
    company_type: str | None = Field(default=None, alias="company_type")
    address_snippet: str | None = Field(default=None, alias="address_snippet")
    description: str | None = None
    date_of_creation: str | None = Field(default=None, alias="date_of_creation")


class CompanySearchResponse(BaseModel):
    """Paginated company search results."""

    items: list[CompanySearchItem] = Field(default_factory=list)
    total_count: int | None = Field(default=None, alias="total_count")
    items_per_page: int | None = Field(default=None, alias="items_per_page")
    start_index: int | None = Field(default=None, alias="start_index")


class Company(BaseModel):
    """Full company profile from ``/company/{number}``."""

    model_config = ConfigDict(extra="allow")

    company_name: str
    company_number: str
    company_status: str | None = Field(default=None, alias="company_status")
    type: str | None = None
    jurisdiction: str | None = None
    date_of_creation: str | None = Field(default=None, alias="date_of_creation")
    sic_codes: list[str] | None = Field(default=None, alias="sic_codes")
    registered_office_address: AddressSnippet | dict[str, Any] | None = Field(
        default=None,
        alias="registered_office_address",
    )
    etag: str | None = None


class OfficerLinks(BaseModel):
    """Hypermedia links on an officer record."""

    model_config = ConfigDict(extra="allow")

    officer: dict[str, Any] | None = None


class Officer(BaseModel):
    """Company officer list item."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    officer_role: str | None = Field(default=None, alias="officer_role")
    appointed_on: str | None = Field(default=None, alias="appointed_on")
    resigned_on: str | None = Field(default=None, alias="resigned_on")
    nationality: str | None = None
    occupation: str | None = None
    links: OfficerLinks | dict[str, Any] | None = None

    @property
    def officer_id(self) -> str | None:
        """Best-effort extraction of the officer ID from ``links.officer.appointments``.

        Companies House embeds the stable officer identifier in the URL that
        fans out to an officer's appointments across companies
        (``/officers/<id>/appointments``). That ID is the only reliable way
        to join one officer record across multiple ``/company/*/officers``
        responses, so the graph primitives lean on it heavily.
        """

        links = self.links
        if links is None:
            return None
        officer = links.officer if isinstance(links, OfficerLinks) else links.get("officer")
        if not isinstance(officer, dict):
            return None
        appointments = officer.get("appointments")
        if not isinstance(appointments, str):
            return None
        match = _OFFICER_ID_RE.search(appointments)
        return match.group(1) if match else None


class OfficersResponse(BaseModel):
    """Paginated officers response."""

    items: list[Officer] = Field(default_factory=list)
    total_results: int | None = Field(default=None, alias="total_results")
    items_per_page: int | None = Field(default=None, alias="items_per_page")
    start_index: int | None = Field(default=None, alias="start_index")


class OfficerSearchItem(BaseModel):
    """One officer hit from ``/search/officers``."""

    model_config = ConfigDict(extra="allow")

    title: str | None = None
    description: str | None = None
    address_snippet: str | None = Field(default=None, alias="address_snippet")
    appointment_count: int | None = Field(default=None, alias="appointment_count")
    date_of_birth: dict[str, Any] | None = Field(default=None, alias="date_of_birth")
    links: dict[str, Any] | None = None

    @property
    def officer_id(self) -> str | None:
        """Extract the officer ID from ``links.self`` (``/officers/<id>``)."""

        if not isinstance(self.links, dict):
            return None
        target = self.links.get("self")
        if not isinstance(target, str):
            return None
        match = _OFFICER_ID_RE.search(target)
        return match.group(1) if match else None


class OfficerSearchResponse(BaseModel):
    """Paginated officer-search response."""

    items: list[OfficerSearchItem] = Field(default_factory=list)
    total_results: int | None = Field(default=None, alias="total_results")
    items_per_page: int | None = Field(default=None, alias="items_per_page")
    start_index: int | None = Field(default=None, alias="start_index")


class AppointedCompany(BaseModel):
    """The company an officer is / was appointed to."""

    model_config = ConfigDict(extra="allow")

    company_number: str | None = Field(default=None, alias="company_number")
    company_name: str | None = Field(default=None, alias="company_name")
    company_status: str | None = Field(default=None, alias="company_status")


class OfficerAppointment(BaseModel):
    """One appointment in an officer's portfolio."""

    model_config = ConfigDict(extra="allow")

    appointed_to: AppointedCompany | dict[str, Any] | None = Field(
        default=None, alias="appointed_to"
    )
    appointed_on: str | None = Field(default=None, alias="appointed_on")
    resigned_on: str | None = Field(default=None, alias="resigned_on")
    officer_role: str | None = Field(default=None, alias="officer_role")
    name: str | None = None
    occupation: str | None = None
    links: dict[str, Any] | None = None

    @property
    def company_number(self) -> str | None:
        """Pull the company number from ``appointed_to`` or ``links.company``."""

        if isinstance(self.appointed_to, AppointedCompany):
            if self.appointed_to.company_number:
                return self.appointed_to.company_number
        elif isinstance(self.appointed_to, dict):
            value = self.appointed_to.get("company_number")
            if isinstance(value, str) and value:
                return value
        if isinstance(self.links, dict):
            company = self.links.get("company")
            if isinstance(company, str):
                parts = [p for p in company.split("/") if p]
                if parts and parts[0] == "company" and len(parts) >= 2:
                    return parts[1]
        return None


class OfficerAppointmentsResponse(BaseModel):
    """Paginated appointments for one officer."""

    items: list[OfficerAppointment] = Field(default_factory=list)
    total_results: int | None = Field(default=None, alias="total_results")
    items_per_page: int | None = Field(default=None, alias="items_per_page")
    start_index: int | None = Field(default=None, alias="start_index")
    name: str | None = None
    date_of_birth: dict[str, Any] | None = Field(default=None, alias="date_of_birth")


class PSC(BaseModel):
    """Person with significant control entry."""

    model_config = ConfigDict(extra="allow")

    kind: str | None = None
    name: str | None = None
    notified_on: str | None = Field(default=None, alias="notified_on")
    ceased_on: str | None = Field(default=None, alias="ceased_on")
    natures_of_control: list[str] | None = Field(default=None, alias="natures_of_control")
    nationality: str | None = None
    country_of_residence: str | None = Field(default=None, alias="country_of_residence")
    identification: dict[str, Any] | None = None
    links: dict[str, Any] | None = None

    @property
    def psc_id(self) -> str | None:
        """Extract the stable PSC ID from ``links.self``.

        Companies House returns links like
        ``/company/<n>/persons-with-significant-control/individual/<psc_id>``.
        We capture ``<psc_id>`` so two fetches of the same PSC dedupe
        cleanly in the landlord graph.
        """

        if not isinstance(self.links, dict):
            return None
        target = self.links.get("self")
        if not isinstance(target, str):
            return None
        match = _PSC_ID_RE.search(target)
        return match.group(1) if match else None

    @property
    def is_corporate(self) -> bool:
        """Whether this PSC is another legal entity (traversable in the graph)."""

        kind = (self.kind or "").lower()
        if not kind:
            return False
        if "corporate" in kind or "legal-person" in kind:
            return True
        if kind.startswith("super-secure"):
            return False
        return False

    @property
    def corporate_company_number(self) -> str | None:
        """Best-effort company number when ``identification`` is a UK company."""

        if not self.is_corporate:
            return None
        ident = self.identification
        if not isinstance(ident, dict):
            return None
        candidate = ident.get("registration_number")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        return None


class PSCListResponse(BaseModel):
    """Paginated PSC response."""

    items: list[PSC] = Field(default_factory=list)
    total_results: int | None = Field(default=None, alias="total_results")
    items_per_page: int | None = Field(default=None, alias="items_per_page")
    start_index: int | None = Field(default=None, alias="start_index")


class FilingHistoryItem(BaseModel):
    """Single filing history transaction."""

    model_config = ConfigDict(extra="allow")

    transaction_id: str | None = Field(default=None, alias="transaction_id")
    type: str | None = None
    date: str | None = None
    description: str | None = None
    category: str | None = None
    links: dict[str, Any] | None = None


class FilingHistoryResponse(BaseModel):
    """Paginated filing history."""

    items: list[FilingHistoryItem] = Field(default_factory=list)
    total_count: int | None = Field(default=None, alias="total_count")
    items_per_page: int | None = Field(default=None, alias="items_per_page")
    start_index: int | None = Field(default=None, alias="start_index")


class ChargeLinks(BaseModel):
    """Links on a charge."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    self_href: str | None = Field(default=None, alias="self")


class Charge(BaseModel):
    """Registered charge (mortgage / security)."""

    model_config = ConfigDict(extra="allow")

    charge_code: str | None = Field(default=None, alias="charge_code")
    charge_number: int | None = Field(default=None, alias="charge_number")
    classification: dict[str, Any] | None = None
    created_on: str | None = Field(default=None, alias="created_on")
    delivered_on: str | None = Field(default=None, alias="delivered_on")
    status: str | None = None
    particulars: dict[str, Any] | None = None
    links: ChargeLinks | dict[str, Any] | None = None


class ChargesResponse(BaseModel):
    """Paginated charges response."""

    items: list[Charge] = Field(default_factory=list)
    total_count: int | None = Field(default=None, alias="total_count")
    items_per_page: int | None = Field(default=None, alias="items_per_page")
    start_index: int | None = Field(default=None, alias="start_index")
