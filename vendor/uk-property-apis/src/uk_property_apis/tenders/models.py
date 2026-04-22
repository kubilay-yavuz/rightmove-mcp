"""Transport-agnostic models for UK public-sector tenders.

Two transports are exposed from this subpackage and both return the same
canonical :class:`Tender`, so downstream code is source-agnostic:

* :class:`~uk_property_apis.tenders.contracts_finder_client.ContractsFinderClient`
  — the Cabinet Office's *Contracts Finder* (`contractsfinder.service.gov.uk`)
  covering below-threshold UK procurement (typically < £139k). Public read
  access requires no authentication.

* :class:`~uk_property_apis.tenders.fts_client.FTSClient` — the post-Brexit
  *Find a Tender Service* (`find-tender.service.gov.uk`) covering
  above-threshold public procurement. OCDS 1.1.5 release-package payload.
  Requires a ``CDP-Api-Key`` organisation key.

For a property-intelligence agent, the useful CPV prefixes are:

* ``45xxxxxx`` — construction work (buildings, civil engineering, demolition)
* ``70xxxxxx`` — real-estate services (property management, valuation,
  letting, brokerage)
* ``71xxxxxx`` — architectural, engineering, inspection services
* ``77xxxxxx`` — landscaping, grounds maintenance
* ``90xxxxxx`` — waste / cleaning / environmental

The ``cpv_prefix_matches`` helper lets callers filter a :class:`Tender` by
any of these prefixes without unfolding the full CPV tree each time.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TenderSource(StrEnum):
    """The upstream register that produced the notice."""

    CONTRACTS_FINDER = "contracts-finder"
    FIND_A_TENDER = "find-a-tender"


class TenderStatus(StrEnum):
    """Normalised life-cycle state, unified across both sources.

    Contracts Finder uses free-text (``Open``, ``Awarded``, ``Closed``,
    ``Withdrawn``, ``Completed``) and Find a Tender uses OCDS enum values
    (``planning``, ``planned``, ``active``, ``cancelled``, ``unsuccessful``,
    ``complete``, ``withdrawn``). We fold both dialects into this enum so
    callers don't have to special-case either.
    """

    PLANNED = "planned"
    OPEN = "open"
    CLOSED = "closed"
    AWARDED = "awarded"
    CANCELLED = "cancelled"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


class TenderClassification(BaseModel):
    """A single classification code (normally CPV) on the tender."""

    model_config = ConfigDict(extra="forbid")

    scheme: str = Field(
        default="CPV",
        description=(
            "Identifier of the classification scheme. CPV (Common "
            "Procurement Vocabulary) is the default; FTS occasionally "
            "carries others (``UNSPSC``, ``CPA``)."
        ),
    )
    code: str = Field(
        ...,
        description=(
            "Machine code for the classification. CPV codes are 8 digits "
            "(e.g. ``45211000`` for multi-dwelling construction)."
        ),
    )
    description: str | None = Field(
        default=None,
        description="Human-readable label (e.g. 'Construction work for multi-dwelling buildings').",
    )


class TenderOrg(BaseModel):
    """A buyer or supplier party attached to the tender.

    Supplier arrays are typically empty on live/open notices and populate
    once the award notice is published.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Party name.")
    scheme: str | None = Field(
        default=None,
        description=(
            "Identifier scheme (e.g. ``GB-COH`` for Companies House, "
            "``GB-GOVUK`` for central-government departments)."
        ),
    )
    identifier: str | None = Field(
        default=None,
        description="Party ID within ``scheme`` (e.g. a company number).",
    )
    uri: str | None = Field(default=None, description="Canonical URL for the party.")
    address: str | None = Field(default=None, description="Free-text address.")
    region: str | None = Field(default=None, description="UK region name.")
    postcode: str | None = Field(default=None, description="UK postcode when known.")
    country_code: str | None = Field(
        default=None,
        description="ISO 3166-1 alpha-2 (``GB``).",
    )


class TenderValue(BaseModel):
    """Estimated or awarded contract value.

    Contracts Finder advertises ranges (``ValueLow`` / ``ValueHigh``) on
    open notices and a fixed ``awardedValue`` on award notices. FTS uses a
    single ``value.amount``. We carry both shapes without forcing a
    computed midpoint — callers decide.
    """

    model_config = ConfigDict(extra="forbid")

    amount: float | None = Field(
        default=None,
        description="Single point estimate. Prefer ``amount`` over the range when both are present.",
    )
    amount_low: float | None = Field(
        default=None,
        description="Lower bound of the estimated range.",
    )
    amount_high: float | None = Field(
        default=None,
        description="Upper bound of the estimated range.",
    )
    currency: str = Field(
        default="GBP",
        description="ISO 4217 currency code. Almost always ``GBP`` on UK notices.",
    )


class TenderLocation(BaseModel):
    """Geographic scope of the procurement."""

    model_config = ConfigDict(extra="forbid")

    region: str | None = Field(default=None, description="UK region name, e.g. 'London'.")
    postcode: str | None = Field(default=None, description="UK postcode when known.")
    address: str | None = Field(default=None, description="Free-text address.")
    country_code: str | None = Field(
        default=None,
        description="ISO 3166-1 alpha-2. UK-national tenders set this to ``GB``.",
    )


class Tender(BaseModel):
    """Canonical tender record, unified across sources.

    Always set: ``source``, ``source_id``, ``title``.

    Often set, depending on the source and notice life-cycle stage:
    ``description``, ``status``, ``notice_type``, ``published_date``,
    ``closing_date``, ``buyer``, ``value``, ``classifications``,
    ``location``, ``url``.

    Populated on awarded notices only: ``suppliers``, ``start_date`` /
    ``end_date`` (when the award publishes the contract window).

    The ``raw`` dict preserves the original upstream payload so callers
    can reach fields we didn't normalise without re-querying.
    """

    model_config = ConfigDict(extra="forbid")

    source: TenderSource = Field(
        ...,
        description="Which register produced the notice (Contracts Finder vs Find a Tender).",
    )
    source_id: str = Field(
        ...,
        description="Native unique ID within ``source`` (CF GUID; FTS ``tender.id``).",
    )
    ocid: str | None = Field(
        default=None,
        description=(
            "OCDS Procurement Process Identifier (``ocds-XXXX-NNNN`` style). "
            "FTS always sets this; Contracts Finder does not expose it."
        ),
    )
    title: str = Field(..., description="Tender title.")
    description: str | None = Field(
        default=None,
        description="Free-text description / tender summary.",
    )
    status: TenderStatus = Field(
        default=TenderStatus.UNKNOWN,
        description="Normalised life-cycle state.",
    )
    notice_type: str | None = Field(
        default=None,
        description=(
            "Source-specific notice classification — CF uses ``Contract`` / "
            "``Award`` / ``EarlyEngagement``; FTS OCDS tags include ``tender`` / "
            "``award`` / ``contract``."
        ),
    )
    published_date: datetime | None = Field(
        default=None,
        description="When the notice was first published to the register.",
    )
    closing_date: datetime | None = Field(
        default=None,
        description="Bid submission deadline.",
    )
    start_date: date | None = Field(
        default=None,
        description="Contract commencement (on award notices).",
    )
    end_date: date | None = Field(
        default=None,
        description="Contract end date (on award notices).",
    )
    buyer: TenderOrg | None = Field(
        default=None,
        description="The procuring authority issuing the tender.",
    )
    suppliers: list[TenderOrg] = Field(
        default_factory=list,
        description="Awarded suppliers, empty until the award notice publishes.",
    )
    value: TenderValue | None = Field(
        default=None,
        description="Estimated or awarded value.",
    )
    classifications: list[TenderClassification] = Field(
        default_factory=list,
        description="Subject classifications; typically CPV codes.",
    )
    location: TenderLocation | None = Field(
        default=None,
        description="Geographic scope of the procurement.",
    )
    url: str | None = Field(
        default=None,
        description="Human-facing notice page on the source register.",
    )
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="Original upstream JSON payload. Preserved for access to un-normalised fields.",
    )

    def cpv_prefix_matches(self, prefix: str) -> bool:
        """Return ``True`` if any CPV classification starts with ``prefix``.

        The CPV tree is hierarchical — ``45000000`` ("construction work")
        is the parent of every ``45xxxxxx`` code. Pass ``"45"`` to catch
        anything construction-related, ``"4521"`` to narrow to residential
        building construction, etc.
        """

        return any(
            c.scheme.upper() == "CPV" and c.code.startswith(prefix)
            for c in self.classifications
        )


class TenderQuery(BaseModel):
    """Normalised search filters used by both source clients.

    A convenience wrapper so callers don't have to juggle CF's
    ``SearchCriteria`` JSON and FTS's query-string parameters separately.
    Each field is optional; unset filters are omitted on the wire.
    """

    model_config = ConfigDict(extra="forbid")

    keyword: str | None = Field(
        default=None,
        description="Free-text search; applied as a keyword-style match.",
    )
    cpv_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Exact CPV codes to include. 8-digit codes for narrow matches "
            "(e.g. ``45211000``) or parent codes for broad ones (``45000000``). "
            "Callers that want prefix-matching should call "
            ":meth:`Tender.cpv_prefix_matches` on the returned rows."
        ),
    )
    regions: list[str] = Field(
        default_factory=list,
        description="UK region names (CF vocabulary), e.g. 'London', 'North West'.",
    )
    postcode: str | None = Field(
        default=None,
        description="Centroid postcode for radius search (CF only).",
    )
    radius_km: float | None = Field(
        default=None,
        description="Radius in kilometres around ``postcode`` (CF only).",
    )
    notice_types: list[str] = Field(
        default_factory=list,
        description=(
            "Source-specific type names. CF: ``Contract`` / ``Award`` / "
            "``EarlyEngagement`` / ``Pipeline``. FTS: ``tender`` / ``award`` / etc."
        ),
    )
    statuses: list[str] = Field(
        default_factory=list,
        description="Source-specific status names, e.g. ``Open`` / ``Awarded`` / ``Closed``.",
    )
    value_low: float | None = Field(
        default=None,
        description="Inclusive lower bound on contract value (GBP).",
    )
    value_high: float | None = Field(
        default=None,
        description="Inclusive upper bound on contract value (GBP).",
    )
    published_from: datetime | None = Field(
        default=None,
        description="Only return notices published at or after this datetime.",
    )
    published_to: datetime | None = Field(
        default=None,
        description="Only return notices published at or before this datetime.",
    )
    updated_from: datetime | None = Field(
        default=None,
        description=(
            "Only return notices updated at or after this datetime. FTS uses "
            "this natively; CF does not support it (ignored on CF calls)."
        ),
    )
    updated_to: datetime | None = Field(
        default=None,
        description="FTS-only updated-by cap. Ignored on CF calls.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description=(
            "Upper bound on returned rows per source. CF's API is "
            "non-paginating (returns up to ``size`` in a single POST); FTS "
            "paginates transparently until this budget is spent."
        ),
    )

    @field_validator("cpv_codes", "regions", "notice_types", "statuses", mode="before")
    @classmethod
    def _drop_blanks(cls, v: object) -> Any:
        if isinstance(v, (list, tuple)):
            out: list[str] = []
            for item in v:
                if isinstance(item, str) and item.strip():
                    out.append(item.strip())
            return out
        return v

    @field_validator("postcode", mode="before")
    @classmethod
    def _normalise_postcode(cls, v: object) -> Any:
        if isinstance(v, str):
            stripped = v.strip().upper()
            return stripped or None
        return v


__all__ = [
    "Tender",
    "TenderClassification",
    "TenderLocation",
    "TenderOrg",
    "TenderQuery",
    "TenderSource",
    "TenderStatus",
    "TenderValue",
]
