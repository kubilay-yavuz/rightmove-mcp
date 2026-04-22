"""Transport-agnostic models for UK council planning applications (Idox).

These types are the common currency of both IDOX transports:

* :class:`~uk_property_apis.idox.arcgis_client.ArcGISPlanningClient`
  (preferred, used when the council publishes a public
  ``.../server/rest/services/PALIVE/LIVEUniformPA_Planning/FeatureServer``).
* :class:`~uk_property_apis.idox.html_client.HTMLPlanningClient` — the
  form-POST HTML fallback used when the ArcGIS service is not reachable.

Both transports return the same :class:`PlanningApplication`, so call sites
can switch between them without changing downstream code. The richer
:class:`ApplicationDetail` is only populated by the HTML detail-page
fetcher (the ArcGIS service doesn't expose fields like case officer,
decision, or document counts).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Coordinates(BaseModel):
    """WGS84 lat/lon pin for a planning application.

    IDOX ArcGIS FeatureServers natively store geometry in British National
    Grid (EPSG:27700); the client requests ``outSR=4326`` on every query so
    callers always receive WGS84 degrees here.
    """

    model_config = ConfigDict(extra="forbid")

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class PlanningApplication(BaseModel):
    """Canonical planning-application record, unified across transports.

    The ``status`` / ``received_date`` / ``validated_date`` trio only comes
    back from the HTML transport's list page — ArcGIS features don't carry
    them — and stays ``None`` when the ArcGIS transport is used. For the
    full case-officer / decision / documents detail, use
    :class:`ApplicationDetail` via
    ``HTMLPlanningClient.get_detail_by_key_val``.
    """

    model_config = ConfigDict(extra="forbid")

    council: str = Field(
        ...,
        description="Council slug, e.g. 'lambeth' — matches CouncilConfig.slug.",
    )
    reference: str = Field(
        ...,
        description="Human-readable planning reference, e.g. '03/00318/ADV'.",
    )
    key_val: str = Field(
        ...,
        description=(
            "Idox internal primary key that addresses the detail page at "
            "``/online-applications/applicationDetails.do?keyVal=<KEYVAL>``."
        ),
    )
    address: str = Field(
        ...,
        description=(
            "Subject-property address. ArcGIS delivers it with ``\\r`` line "
            "separators (preserved as-is); HTML listings deliver it on one "
            "line — parsers normalise whitespace on the HTML side only."
        ),
    )
    description: str = Field(
        default="",
        description="Free-text proposal / works description.",
    )
    last_modified: datetime | None = Field(
        default=None,
        description="Council's ``DATEMODIFIED`` in UTC (ArcGIS only).",
    )
    coordinates: Coordinates | None = Field(
        default=None,
        description="Point geometry, when known. Polygon footprints live on layer 2+.",
    )
    detail_url: str = Field(
        ...,
        description="Absolute URL of the IDOX applicationDetails.do HTML page.",
    )
    status: str | None = Field(
        default=None,
        description=(
            "Free-text case status from the HTML list page (e.g. "
            "'Awaiting decision', 'Granted'). None when the transport is "
            "ArcGIS — follow ``detail_url`` if status is required."
        ),
    )
    received_date: date | None = Field(
        default=None,
        description="Application Received date from the HTML list page.",
    )
    validated_date: date | None = Field(
        default=None,
        description="Application Validated date from the HTML list page.",
    )


class ApplicationDetail(PlanningApplication):
    """Richer detail-page fields, only populated by the HTML detail fetcher.

    ArcGIS does not expose case-officer, decision, or document metadata;
    callers that need those attributes have to follow ``detail_url``
    regardless of which transport produced the initial listing.
    """

    model_config = ConfigDict(extra="forbid")

    proposal: str | None = Field(
        default=None,
        description=(
            "Full proposal text from the detail page. Richer than "
            "``description`` from listings (which is frequently truncated)."
        ),
    )
    appeal_status: str | None = Field(default=None)
    appeal_decision: str | None = Field(default=None)
    decision: str | None = Field(default=None)
    decision_date: date | None = Field(default=None)
    case_officer: str | None = Field(default=None)
    ward: str | None = Field(default=None)
    parish: str | None = Field(default=None)
    applicant_name: str | None = Field(default=None)
    agent_name: str | None = Field(default=None)
    document_count: int | None = Field(
        default=None,
        description="Attached documents, when the detail page advertises them.",
    )
    related_case_count: int | None = Field(default=None)
    related_property_count: int | None = Field(default=None)


class CouncilConfig(BaseModel):
    """Per-council transport configuration.

    A council may expose either transport, both, or (rarely) neither:

    * ``arcgis_base_url`` set → prefer :class:`ArcGISPlanningClient`.
    * ``arcgis_base_url`` unset → fall back to :class:`HTMLPlanningClient`
      against ``public_access_base_url``.

    ``public_access_base_url`` is always required because even the ArcGIS
    transport uses it to build per-application detail URLs (the HTML detail
    page carries everything the FeatureServer doesn't — case officer,
    documents, consultation periods, etc.).
    """

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(..., description="Short machine identifier (e.g. 'lambeth').")
    name: str = Field(..., description="Human council name (e.g. 'Lambeth').")
    public_access_base_url: str = Field(
        ...,
        description=(
            "Origin of the Idox Public Access installation — "
            "``https://planning.lambeth.gov.uk``. '/online-applications/…' paths "
            "are appended by the client."
        ),
    )
    arcgis_base_url: str | None = Field(
        default=None,
        description=(
            "ArcGIS Server origin + mount point, e.g. "
            "``https://planning.lambeth.gov.uk/server``. None when the council "
            "does not publish one."
        ),
    )
    arcgis_planning_service_path: str = Field(
        default="rest/services/PALIVE/LIVEUniformPA_Planning/FeatureServer",
        description=(
            "Path under ``arcgis_base_url`` to the Planning FeatureServer. "
            "The default matches the standard IDOX deployment; councils that "
            "rename it can override."
        ),
    )

    @field_validator("slug", mode="before")
    @classmethod
    def _lower_slug(cls, v: object) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v

    def detail_url(self, key_val: str, *, active_tab: str = "summary") -> str:
        """Absolute URL of the IDOX HTML detail page for ``key_val``.

        Tabs follow the IDOX convention: ``summary`` (default), ``details``,
        ``dates``, ``contacts``, ``documents``, ``constraints``,
        ``relatedCases``, ``makeComment``.
        """

        origin = self.public_access_base_url.rstrip("/")
        return (
            f"{origin}/online-applications/applicationDetails.do"
            f"?activeTab={active_tab}&keyVal={key_val}"
        )


def _epoch_ms_to_utc(value: object) -> datetime | None:
    """Coerce IDOX ``DATEMODIFIED`` (UTC epoch ms) into a timezone-aware datetime."""

    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=UTC)
    return None


__all__ = [
    "ApplicationDetail",
    "Coordinates",
    "CouncilConfig",
    "PlanningApplication",
    "_epoch_ms_to_utc",
]
