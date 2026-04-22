"""Pydantic models for VOA council-tax-band data."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CouncilTaxBand(BaseModel):
    """One row in the VOA ``search-results-table``.

    Represents a single assessed dwelling (a "property" in VOA terms) — which
    may differ from a HMLR/PPD title or an EPC certificate. Flats in the same
    building each have their own row and their own band.
    """

    model_config = ConfigDict(extra="forbid")

    property_id: str = Field(..., description="VOA property UUID (from the detail URL).")
    address: str = Field(..., description="Full VOA-supplied address line including town + postcode.")
    postcode: str = Field(..., description="Postcode parsed out of the VOA address.")
    band: str = Field(..., description="Council Tax band — one of 'A'..'H' (England) or 'A'..'I' (Wales).")
    local_authority: str | None = Field(
        default=None,
        description="Billing authority name (e.g. 'Islington').",
    )
    local_authority_url: str | None = Field(
        default=None,
        description="Billing authority website (informational).",
    )


class CouncilTaxSearchPage(BaseModel):
    """One page of VOA search results plus pagination breadcrumbs."""

    rows: list[CouncilTaxBand]
    total_results: int | None = Field(
        default=None,
        description="Total matches across all pages, if VOA disclosed the count.",
    )
    next_postcode_token: str | None = Field(
        default=None,
        description="Opaque token VOA uses in pagination URLs. Empty means no next page.",
    )
    has_next_page: bool = False
