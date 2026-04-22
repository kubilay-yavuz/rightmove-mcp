"""Async client for the IDOX ArcGIS Planning FeatureServer transport.

Many Idox Public Access councils publish their planning register as a
public Esri FeatureServer alongside the HTML portal::

    https://<council-host>/server/rest/services/PALIVE/LIVEUniformPA_Planning/FeatureServer

The service is unauthenticated, supports rich ``where`` filters
(``ADDRESS LIKE '%X%'``, ``DATEMODIFIED > TIMESTAMP '2026-04-01 00:00:00'``,
bbox intersections), paginates via ``resultOffset`` + ``resultRecordCount``
(capped at 2,000), and returns geometry in WGS84 when ``outSR=4326`` is
set. This makes it a dramatically cleaner discovery surface than scraping
the HTML ``simpleSearchResults.do`` form — no CSRF, no cookies, no
reCAPTCHA, and full-history access.

The :class:`HTMLPlanningClient` (to be implemented) sits alongside this
class as the fallback for councils that don't expose the FeatureServer
publicly (observed: Westminster, Manchester, Southwark, Leeds).

Both clients ultimately yield the same :class:`PlanningApplication`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any, Final

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import ValidationError
from uk_property_apis.idox.arcgis_models import (
    ArcGISFeature,
    ArcGISFeatureServerInfo,
    ArcGISQueryResult,
)
from uk_property_apis.idox.models import (
    Coordinates,
    CouncilConfig,
    PlanningApplication,
    _epoch_ms_to_utc,
)

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (compatible; uk-property-apis/0.1; "
    "+https://github.com/kubilay-yavuz/uk-property-intel)"
)

# Layer IDs in the standard LIVEUniformPA_Planning FeatureServer.
APPLICATION_POINTS: Final = 0
APPEAL_POINTS: Final = 1
APPLICATION_POLYGONS: Final = 2
APPEAL_POLYGONS: Final = 3
ENFORCEMENT_POLYGONS: Final = 4

_WGS84: Final = 4326
# Hard safety cap on pagination loops: 2,000 pages * 2,000 rows = 4M, well
# beyond any single council's register. Guards against runaway loops if a
# service never returns ``exceededTransferLimit=false``.
_MAX_PAGES: Final = 2_000


def _sql_escape(value: str) -> str:
    """Escape single quotes for inclusion in an ArcGIS ``where`` clause."""

    return value.replace("'", "''")


def _timestamp_literal(moment: datetime) -> str:
    """Format a UTC timestamp as an ArcGIS SQL ``TIMESTAMP`` literal."""

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    as_utc = moment.astimezone(UTC)
    return f"TIMESTAMP '{as_utc.strftime('%Y-%m-%d %H:%M:%S')}'"


def _feature_to_application(
    feature: ArcGISFeature,
    *,
    council: CouncilConfig,
) -> PlanningApplication | None:
    """Map one raw ArcGIS feature into the canonical PlanningApplication.

    Returns ``None`` when required fields are missing (defensive: occasional
    junk rows appear in very old datasets).
    """

    attrs = feature.attributes
    reference = attrs.get("REFVAL")
    key_val = attrs.get("KEYVAL")
    if not isinstance(reference, str) or not reference.strip():
        return None
    if not isinstance(key_val, str) or not key_val.strip():
        return None
    address_raw = attrs.get("ADDRESS")
    address = address_raw.strip() if isinstance(address_raw, str) else ""
    description_raw = attrs.get("DESCRIPTION")
    description = description_raw.strip() if isinstance(description_raw, str) else ""

    coordinates: Coordinates | None = None
    geom = feature.geometry
    if geom is not None and geom.x is not None and geom.y is not None:
        try:
            coordinates = Coordinates(lat=geom.y, lon=geom.x)
        except ValidationError:
            coordinates = None
        except Exception:
            coordinates = None

    return PlanningApplication(
        council=council.slug,
        reference=reference.strip(),
        key_val=key_val.strip(),
        address=address,
        description=description,
        last_modified=_epoch_ms_to_utc(attrs.get("DATEMODIFIED")),
        coordinates=coordinates,
        detail_url=council.detail_url(key_val.strip()),
    )


class ArcGISPlanningClient(BaseAPIClient):
    """Client for the IDOX ArcGIS Planning FeatureServer.

    One instance is bound to exactly one council via :class:`CouncilConfig`.
    Multi-council fan-out is the aggregator's job — each council has its
    own FeatureServer and its own rate budget, so sharing a client across
    councils would muddle both.

    The client prefers :data:`APPLICATION_POINTS` (layer 0) because it has
    100% coverage — every application shows up as a pin whereas polygon
    layers only carry footprints where the council digitised them.
    """

    def __init__(
        self,
        council: CouncilConfig,
        *,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
        user_agent: str | None = None,
    ) -> None:
        if council.arcgis_base_url is None:
            msg = (
                f"Council {council.slug!r} has no arcgis_base_url; use "
                "HTMLPlanningClient for this council instead."
            )
            raise ValidationError(msg)
        merged_headers: dict[str, str] = {
            "User-Agent": user_agent or _DEFAULT_USER_AGENT,
            "Accept": "application/json",
        }
        if headers:
            merged_headers.update(headers)
        super().__init__(
            base_url=council.arcgis_base_url,
            auth=None,
            timeout=timeout,
            semaphore=semaphore,
            headers=merged_headers,
        )
        self._council = council
        self._service_path = council.arcgis_planning_service_path.strip("/")

    @property
    def council(self) -> CouncilConfig:
        """The council this client is bound to."""

        return self._council

    def _layer_path(self, layer_id: int, *suffix: str) -> str:
        parts: list[str] = [self._service_path, str(layer_id)]
        parts.extend(s.strip("/") for s in suffix if s)
        return "/".join(p for p in parts if p)

    async def _raw_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """GET an ArcGIS path, parse JSON, and raise on Esri error envelopes."""

        payload = await self._get(path, params=params)
        if "error" in payload:
            err = payload.get("error") or {}
            if not isinstance(err, dict):
                raise ValidationError(f"ArcGIS error envelope malformed: {payload!r}")
            message = err.get("message") or "ArcGIS query failed"
            details = err.get("details")
            if isinstance(details, list) and details:
                message = f"{message}: {'; '.join(map(str, details))}"
            raise ValidationError(message)
        return payload

    async def get_service_info(self) -> ArcGISFeatureServerInfo:
        """Return top-level metadata for the Planning FeatureServer."""

        payload = await self._raw_json(self._service_path, params={"f": "json"})
        return self._validate_model(ArcGISFeatureServerInfo, payload)

    async def count(
        self,
        *,
        where: str = "1=1",
        layer_id: int = APPLICATION_POINTS,
    ) -> int:
        """Return the number of features matching ``where`` (server-side)."""

        params: dict[str, Any] = {
            "f": "json",
            "where": where,
            "returnCountOnly": "true",
        }
        payload = await self._raw_json(
            self._layer_path(layer_id, "query"), params=params
        )
        value = payload.get("count")
        if not isinstance(value, int):
            raise ValidationError(
                f"ArcGIS count response missing integer 'count': {payload!r}"
            )
        return value

    async def _query_page(
        self,
        *,
        layer_id: int,
        where: str,
        out_fields: str,
        return_geometry: bool,
        out_sr: int,
        order_by_fields: str | None,
        result_record_count: int,
        result_offset: int,
        geometry_envelope: tuple[float, float, float, float] | None,
        in_sr: int,
    ) -> ArcGISQueryResult:
        params: dict[str, Any] = {
            "f": "json",
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "true" if return_geometry else "false",
            "outSR": out_sr,
            "resultRecordCount": result_record_count,
            "resultOffset": result_offset,
        }
        if order_by_fields:
            params["orderByFields"] = order_by_fields
        if geometry_envelope is not None:
            xmin, ymin, xmax, ymax = geometry_envelope
            params["geometry"] = json.dumps(
                {
                    "xmin": xmin,
                    "ymin": ymin,
                    "xmax": xmax,
                    "ymax": ymax,
                    "spatialReference": {"wkid": in_sr},
                }
            )
            params["geometryType"] = "esriGeometryEnvelope"
            params["inSR"] = in_sr
            params["spatialRel"] = "esriSpatialRelIntersects"

        payload = await self._raw_json(
            self._layer_path(layer_id, "query"), params=params
        )
        return self._validate_model(ArcGISQueryResult, payload)

    async def iter_applications(
        self,
        *,
        where: str = "1=1",
        layer_id: int = APPLICATION_POINTS,
        order_by_fields: str | None = "DATEMODIFIED DESC",
        page_size: int = 2000,
        max_results: int | None = None,
        visible_only: bool = True,
        return_geometry: bool = True,
        geometry_envelope: tuple[float, float, float, float] | None = None,
        in_sr: int = _WGS84,
    ) -> AsyncIterator[PlanningApplication]:
        """Stream matching applications, paging transparently.

        Pagination uses ``resultOffset`` + ``resultRecordCount`` and stops
        when the server clears ``exceededTransferLimit`` or the caller's
        ``max_results`` cap is reached. An ``orderByFields`` is strongly
        recommended — without deterministic ordering ArcGIS pagination
        can duplicate or skip rows.
        """

        effective_where = where.strip() or "1=1"
        if visible_only:
            if effective_where == "1=1":
                effective_where = "ISPAVISIBLE = 1"
            else:
                effective_where = f"({effective_where}) AND ISPAVISIBLE = 1"

        page_size = max(1, min(page_size, 2000))
        yielded = 0
        offset = 0
        pages = 0
        while pages < _MAX_PAGES:
            result = await self._query_page(
                layer_id=layer_id,
                where=effective_where,
                out_fields="*",
                return_geometry=return_geometry,
                out_sr=_WGS84,
                order_by_fields=order_by_fields,
                result_record_count=page_size,
                result_offset=offset,
                geometry_envelope=geometry_envelope,
                in_sr=in_sr,
            )
            if not result.features:
                break
            for feat in result.features:
                app = _feature_to_application(feat, council=self._council)
                if app is None:
                    continue
                yield app
                yielded += 1
                if max_results is not None and yielded >= max_results:
                    return
            if not result.exceeded_transfer_limit:
                break
            offset += len(result.features)
            pages += 1
        if pages >= _MAX_PAGES:
            logger.warning(
                "ArcGIS iter_applications hit _MAX_PAGES=%d safety cap for %s",
                _MAX_PAGES,
                self._council.slug,
            )

    async def list_applications(
        self,
        *,
        where: str = "1=1",
        layer_id: int = APPLICATION_POINTS,
        order_by_fields: str | None = "DATEMODIFIED DESC",
        max_results: int | None = None,
        visible_only: bool = True,
    ) -> list[PlanningApplication]:
        """Materialise :meth:`iter_applications` into a list."""

        return [
            app
            async for app in self.iter_applications(
                where=where,
                layer_id=layer_id,
                order_by_fields=order_by_fields,
                max_results=max_results,
                visible_only=visible_only,
            )
        ]

    async def search_by_address(
        self,
        substring: str,
        *,
        max_results: int | None = 200,
    ) -> list[PlanningApplication]:
        """Text search against ``ADDRESS`` with a substring LIKE."""

        cleaned = substring.strip()
        if not cleaned:
            raise ValidationError("search_by_address requires a non-empty substring")
        where = f"ADDRESS LIKE '%{_sql_escape(cleaned)}%'"
        return await self.list_applications(
            where=where,
            order_by_fields=None,
            max_results=max_results,
        )

    async def search_by_description(
        self,
        substring: str,
        *,
        max_results: int | None = 200,
    ) -> list[PlanningApplication]:
        """Text search against ``DESCRIPTION`` with a substring LIKE."""

        cleaned = substring.strip()
        if not cleaned:
            raise ValidationError(
                "search_by_description requires a non-empty substring"
            )
        where = f"DESCRIPTION LIKE '%{_sql_escape(cleaned)}%'"
        return await self.list_applications(
            where=where,
            order_by_fields=None,
            max_results=max_results,
        )

    async def recent_applications(
        self,
        *,
        since: datetime,
        max_results: int | None = None,
    ) -> list[PlanningApplication]:
        """Applications whose ``DATEMODIFIED`` is after ``since``.

        Perfect for daily aggregator polls: pass yesterday's timestamp and
        ingest the delta.
        """

        where = f"DATEMODIFIED > {_timestamp_literal(since)}"
        return await self.list_applications(
            where=where,
            order_by_fields="DATEMODIFIED DESC",
            max_results=max_results,
        )

    async def applications_in_bbox(
        self,
        *,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        max_results: int | None = None,
    ) -> list[PlanningApplication]:
        """Applications whose point pin intersects a WGS84 bbox."""

        apps: list[PlanningApplication] = []
        async for app in self.iter_applications(
            where="1=1",
            order_by_fields="DATEMODIFIED DESC",
            max_results=max_results,
            geometry_envelope=(min_lon, min_lat, max_lon, max_lat),
            in_sr=_WGS84,
        ):
            apps.append(app)
        return apps

    async def get_by_reference(self, reference: str) -> PlanningApplication | None:
        """Exact match on REFVAL, returning a single app or ``None``."""

        cleaned = reference.strip()
        if not cleaned:
            raise ValidationError("get_by_reference requires a non-empty reference")
        where = f"REFVAL = '{_sql_escape(cleaned)}'"
        apps = await self.list_applications(
            where=where,
            order_by_fields=None,
            max_results=1,
            visible_only=False,
        )
        return apps[0] if apps else None

    async def get_by_key_val(self, key_val: str) -> PlanningApplication | None:
        """Exact match on the IDOX internal KEYVAL."""

        cleaned = key_val.strip()
        if not cleaned:
            raise ValidationError("get_by_key_val requires a non-empty key_val")
        where = f"KEYVAL = '{_sql_escape(cleaned)}'"
        apps = await self.list_applications(
            where=where,
            order_by_fields=None,
            max_results=1,
            visible_only=False,
        )
        return apps[0] if apps else None


__all__ = [
    "APPEAL_POINTS",
    "APPEAL_POLYGONS",
    "APPLICATION_POINTS",
    "APPLICATION_POLYGONS",
    "ENFORCEMENT_POLYGONS",
    "ArcGISPlanningClient",
]
