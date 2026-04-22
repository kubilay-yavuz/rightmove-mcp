"""Pydantic wrappers for Esri ArcGIS REST responses used by the IDOX transport.

Kept separate from :mod:`uk_property_apis.idox.models` so the canonical
:class:`PlanningApplication` remains transport-agnostic; these types only
exist to validate raw FeatureServer payloads before mapping them into the
canonical form.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ArcGISLayerInfo(BaseModel):
    """One row in the ``layers`` array returned by ``FeatureServer?f=json``."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int
    name: str
    type: str
    geometry_type: str | None = Field(default=None, alias="geometryType")


class ArcGISFeatureServerInfo(BaseModel):
    """Service-level metadata for a Planning FeatureServer.

    We only surface the fields the client needs; everything else is
    ignored so schema drift (new capabilities flags, etc.) is non-fatal.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    current_version: float | None = Field(default=None, alias="currentVersion")
    service_description: str | None = Field(
        default=None, alias="serviceDescription"
    )
    max_record_count: int = Field(default=2000, alias="maxRecordCount")
    capabilities: str | None = None
    layers: list[ArcGISLayerInfo] = Field(default_factory=list)


class ArcGISPointGeometry(BaseModel):
    """``esriGeometryPoint`` shape with ``x`` / ``y`` in the service's output SR."""

    model_config = ConfigDict(extra="ignore")

    x: float | None = None
    y: float | None = None


class ArcGISFeature(BaseModel):
    """One feature from a ``/query`` response.

    ``attributes`` is deliberately loose: IDOX services agree on a core set
    (``REFVAL``, ``KEYVAL``, ``ADDRESS``, ``DESCRIPTION``, ``DATEMODIFIED``)
    but occasionally add council-specific columns (e.g. ``WARD``, ``PARISH``)
    which callers can read opportunistically via ``feature.attributes``.
    """

    model_config = ConfigDict(extra="ignore")

    attributes: dict[str, Any] = Field(default_factory=dict)
    geometry: ArcGISPointGeometry | None = None


class ArcGISQueryResult(BaseModel):
    """Paginated ``/query`` response."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    object_id_field_name: str | None = Field(
        default=None, alias="objectIdFieldName"
    )
    geometry_type: str | None = Field(default=None, alias="geometryType")
    features: list[ArcGISFeature] = Field(default_factory=list)
    exceeded_transfer_limit: bool = Field(
        default=False, alias="exceededTransferLimit"
    )


class ArcGISErrorDetails(BaseModel):
    """Wrapper for the ``error`` object Esri returns on bad queries."""

    model_config = ConfigDict(extra="ignore")

    code: int | None = None
    message: str | None = None
    details: list[str] = Field(default_factory=list)


__all__ = [
    "ArcGISErrorDetails",
    "ArcGISFeature",
    "ArcGISFeatureServerInfo",
    "ArcGISLayerInfo",
    "ArcGISPointGeometry",
    "ArcGISQueryResult",
]
