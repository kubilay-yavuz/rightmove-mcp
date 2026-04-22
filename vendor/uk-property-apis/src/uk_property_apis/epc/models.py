"""Pydantic models for EPC Open Data Communities API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EPCCertificateRow(BaseModel):
    """One certificate row as returned by search or certificate endpoints."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    lmk_key: str | None = Field(default=None, alias="lmk-key")
    uprn: str | None = None
    address: str | None = None
    postcode: str | None = None
    current_energy_rating: str | None = Field(default=None, alias="current-energy-rating")
    current_energy_efficiency: str | None = Field(default=None, alias="current-energy-efficiency")
    co2_emissions_current: str | None = Field(default=None, alias="co2-emissions-current")
    total_floor_area: str | None = Field(default=None, alias="total-floor-area")
    property_type: str | None = Field(default=None, alias="property-type")
    built_form: str | None = Field(default=None, alias="built-form")
    inspection_date: str | None = Field(default=None, alias="inspection-date")
    lodgement_date: str | None = Field(default=None, alias="lodgement-date")
    tenure: str | None = None
    construction_age_band: str | None = Field(default=None, alias="construction-age-band")


class EPCSearchPage(BaseModel):
    """One page of EPC search results with pagination token."""

    rows: list[EPCCertificateRow]
    next_search_after: str | None = None
