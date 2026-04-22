"""Pydantic models for postcodes.io responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class PostcodeCodes(BaseModel):
    """ONS / administrative codes returned alongside a postcode lookup."""

    model_config = ConfigDict(extra="allow")

    admin_district: str | None = None
    admin_county: str | None = None
    admin_ward: str | None = None
    parish: str | None = None
    parliamentary_constituency: str | None = None
    ccg: str | None = None
    lsoa: str | None = None
    msoa: str | None = None


class PostcodeResult(BaseModel):
    """Full geospatial and administrative record for a UK postcode."""

    model_config = ConfigDict(extra="allow")

    postcode: str
    quality: int | None = None
    eastings: int | None = None
    northings: int | None = None
    country: str | None = None
    nhs_ha: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    european_electoral_region: str | None = None
    primary_care_trust: str | None = None
    region: str | None = None
    lsoa: str | None = None
    msoa: str | None = None
    incode: str | None = None
    outcode: str | None = None
    parliamentary_constituency: str | None = None
    admin_district: str | None = None
    parish: str | None = None
    admin_county: str | None = None
    admin_ward: str | None = None
    ccg: str | None = None
    nuts: str | None = None
    codes: PostcodeCodes | dict[str, Any] | None = None


class PostcodeLookupResponse(BaseModel):
    """Wrapper for single-postcode lookup."""

    status: int
    result: PostcodeResult | None = None


class PostcodeValidateResponse(BaseModel):
    """Wrapper for postcode validation."""

    status: int
    result: bool


class BulkPostcodeResultItem(BaseModel):
    """One row from a bulk postcode lookup."""

    query: str
    result: PostcodeResult | None = None


class BulkPostcodeResponse(BaseModel):
    """Response from ``POST /postcodes``."""

    status: int
    result: list[BulkPostcodeResultItem]


class NearestPostcodeItem(BaseModel):
    """Nearest postcode hit from reverse geocoding."""

    postcode: str
    quality: int | None = None
    eastings: int | None = None
    northings: int | None = None
    country: str | None = None
    nhs_ha: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    region: str | None = None
    admin_district: str | None = None
    admin_ward: str | None = None
    parish: str | None = None
    admin_county: str | None = None
    parliamentary_constituency: str | None = None
    lsoa: str | None = None
    msoa: str | None = None
    distance: float | None = None


class ReverseGeocodeResponse(BaseModel):
    """Response from ``GET /postcodes`` with lat/lon query."""

    status: int
    result: list[NearestPostcodeItem] | None = None


class OutcodeResult(BaseModel):
    """Outcode-level aggregate geography."""

    model_config = ConfigDict(extra="allow")

    outcode: str
    longitude: float | None = None
    latitude: float | None = None
    northings: int | None = None
    eastings: int | None = None
    admin_district: list[str] | None = None
    parish: list[str] | None = None
    admin_county: list[str] | None = None
    admin_ward: list[str] | None = None
    country: list[str] | None = None


class OutcodeLookupResponse(BaseModel):
    """Wrapper for outcode lookup."""

    status: int
    result: OutcodeResult | None = None


class PlaceResult(BaseModel):
    """OS Open Names place returned by ``GET /places``.

    ``name_1`` is the canonical place name; ``local_type`` discriminates
    between cities, towns, villages, hamlets and other features — useful
    for disambiguating e.g. "Cambridge" (city in Cambridgeshire) from
    "Cambridge" (village in Gloucestershire).
    """

    model_config = ConfigDict(extra="allow")

    code: str | None = None
    name_1: str | None = None
    name_1_lang: str | None = None
    name_2: str | None = None
    name_2_lang: str | None = None
    local_type: str | None = None
    outcode: str | None = None
    county_unitary: str | None = None
    county_unitary_type: str | None = None
    district_borough: str | None = None
    district_borough_type: str | None = None
    region: str | None = None
    country: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    eastings: int | None = None
    northings: int | None = None
    min_eastings: int | None = None
    max_eastings: int | None = None
    min_northings: int | None = None
    max_northings: int | None = None


class PlaceSearchResponse(BaseModel):
    """Wrapper for ``GET /places`` search."""

    status: int
    result: list[PlaceResult] | None = None
