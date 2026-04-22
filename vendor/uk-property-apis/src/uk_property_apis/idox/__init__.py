"""IDOX Public Access planning data — dual-transport clients.

Two interchangeable transports are exposed from this subpackage; they both
return the canonical :class:`PlanningApplication` so downstream code is
transport-agnostic.

* :class:`ArcGISPlanningClient` — preferred transport for councils that
  publish a public ``LIVEUniformPA_Planning`` FeatureServer (observed on
  Lambeth and Barnet as of Apr 2026). Unauthenticated, supports SQL-style
  ``where`` filters, pagination, and WGS84 geometry.

* :class:`HTMLPlanningClient` — form-POST fallback that scrapes
  ``/online-applications``. Works against every IDOX Public Access
  deployment (observed on Westminster, Manchester, Southwark, Leeds,
  plus Lambeth/Barnet themselves), but is slower, has no geometry, and
  IDOX refuses overly broad queries with :class:`TooManyResults`.

The HTML detail page (``applicationDetails.do?keyVal=<KEYVAL>``) is the
only way to reach case-officer, decision, and document metadata — the
ArcGIS feed deliberately omits those — so the HTML transport's
:meth:`HTMLPlanningClient.get_by_key_val` is useful even when ArcGIS is
the primary source for listings.
"""

from __future__ import annotations

from uk_property_apis.idox.arcgis_client import (
    APPEAL_POINTS,
    APPEAL_POLYGONS,
    APPLICATION_POINTS,
    APPLICATION_POLYGONS,
    ENFORCEMENT_POLYGONS,
    ArcGISPlanningClient,
)
from uk_property_apis.idox.arcgis_models import (
    ArcGISFeature,
    ArcGISFeatureServerInfo,
    ArcGISLayerInfo,
    ArcGISPointGeometry,
    ArcGISQueryResult,
)
from uk_property_apis.idox.councils import (
    KNOWN_COUNCILS,
    arcgis_enabled_councils,
    get_council,
)
from uk_property_apis.idox.html_client import HTMLPlanningClient
from uk_property_apis.idox.html_parser import (
    ResultsPage,
    TooManyResultsError,
    parse_detail_page,
    parse_results_page,
)
from uk_property_apis.idox.models import (
    ApplicationDetail,
    Coordinates,
    CouncilConfig,
    PlanningApplication,
)

__all__ = [
    "APPEAL_POINTS",
    "APPEAL_POLYGONS",
    "APPLICATION_POINTS",
    "APPLICATION_POLYGONS",
    "ENFORCEMENT_POLYGONS",
    "KNOWN_COUNCILS",
    "ApplicationDetail",
    "ArcGISFeature",
    "ArcGISFeatureServerInfo",
    "ArcGISLayerInfo",
    "ArcGISPlanningClient",
    "ArcGISPointGeometry",
    "ArcGISQueryResult",
    "Coordinates",
    "CouncilConfig",
    "HTMLPlanningClient",
    "PlanningApplication",
    "ResultsPage",
    "TooManyResultsError",
    "arcgis_enabled_councils",
    "get_council",
    "parse_detail_page",
    "parse_results_page",
]
