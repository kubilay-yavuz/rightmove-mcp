"""Async client for the Cabinet Office's Contracts Finder API.

Target: ``https://www.contractsfinder.service.gov.uk/``.

Contracts Finder carries every piece of UK public-sector procurement
advertised at below-threshold value (typically < £139k, though councils
publish above that too). For our property agent, this is where the
council-scale work lives: housing maintenance frameworks, estate
groundskeeping, small refurbs, planning-consultant engagements, HMO
licensing contractors, etc.

The v2 REST API is documented at
``https://www.contractsfinder.service.gov.uk/apidocumentation/V2``. Two
endpoints matter to us:

* ``POST /api/rest/2/search_notices/json`` — filtered search with a JSON
  ``SearchCriteria`` body. Returns a ``NoticeSearchResponse`` envelope
  (``HitCount`` + ``NoticeList``). The API does **not** support cursor /
  offset pagination; callers set ``size`` (the body parameter) to cap the
  rows returned, and narrow the query when ``HitCount`` > ``size``.
* ``GET /api/rest/2/get_published_notice/json/{id}`` — full ``FullNotice``
  for a single ID, which carries the case-officer / award / document
  fields the search response omits.

Authentication is **not** required for the read endpoints. A 403 means we
tripped the rate limiter (there's an implicit per-IP threshold; the
documentation advises waiting 5 minutes before retrying).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.tenders._normalise import normalise_cf_notice
from uk_property_apis.tenders.models import (
    Tender,
    TenderQuery,
)

_BASE_URL = "https://www.contractsfinder.service.gov.uk/"
_SEARCH_PATH = "api/rest/2/search_notices/json"


def _isoformat(value: object) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        rendered = iso()
        if isinstance(rendered, str):
            return rendered
    if isinstance(value, str):
        return value
    return None


def _build_search_criteria(query: TenderQuery) -> dict[str, Any]:
    """Render a :class:`TenderQuery` to CF's ``SearchCriteria`` JSON shape."""

    criteria: dict[str, Any] = {}
    if query.keyword:
        criteria["keyword"] = query.keyword
    if query.cpv_codes:
        criteria["cpvCodes"] = list(query.cpv_codes)
    if query.regions:
        criteria["regions"] = list(query.regions)
    if query.postcode:
        criteria["postcode"] = query.postcode
    if query.radius_km is not None:
        criteria["radius"] = query.radius_km
    if query.notice_types:
        criteria["types"] = list(query.notice_types)
    if query.statuses:
        criteria["statuses"] = list(query.statuses)
    if query.value_low is not None:
        criteria["valueLow"] = query.value_low
    if query.value_high is not None:
        criteria["valueHigh"] = query.value_high
    published_from = _isoformat(query.published_from)
    if published_from:
        criteria["publishedFrom"] = published_from
    published_to = _isoformat(query.published_to)
    if published_to:
        criteria["publishedTo"] = published_to
    return criteria


class ContractsFinderClient(BaseAPIClient):
    """Client for the public Contracts Finder v2 REST API.

    Auth-free, but the upstream throttles by IP; call ``aclose()`` /
    use as an ``async with`` to release the ``httpx.AsyncClient`` pool.
    """

    def __init__(
        self,
        *,
        base_url: str = _BASE_URL,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
        auth: httpx.Auth | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    async def search_notices_raw(
        self,
        query: TenderQuery | None = None,
        *,
        size: int | None = None,
    ) -> dict[str, Any]:
        """Call ``POST /search_notices/json`` and return the raw envelope.

        Use this when you need the aggregation facets (``ByRegion`` /
        ``ByType`` / ``ByStatus``) or ``HitCount`` for truncation
        detection; otherwise prefer :meth:`search_tenders`.
        """

        q = query or TenderQuery()
        criteria = _build_search_criteria(q)
        effective_size = size if size is not None else q.limit
        body: dict[str, Any] = {
            "searchCriteria": criteria,
            "size": effective_size,
        }
        payload = await self._post(_SEARCH_PATH, json=body)
        return payload

    async def search_tenders(
        self,
        query: TenderQuery | None = None,
        *,
        size: int | None = None,
    ) -> list[Tender]:
        """Return normalised :class:`Tender` rows for the given filters.

        Respects the ``limit`` field on :class:`TenderQuery` (mapped to
        CF's ``size`` parameter). Callers that expect more than 1,000
        matches should narrow the query and repeat, rather than
        paginating — CF has no cursor mechanism.
        """

        payload = await self.search_notices_raw(query, size=size)
        notices = payload.get("NoticeList") or payload.get("noticeList") or []
        if not isinstance(notices, list):
            return []
        out: list[Tender] = []
        for entry in notices:
            if isinstance(entry, dict):
                out.append(normalise_cf_notice(entry))
        return out

    async def get_notice_raw(self, notice_id: str) -> dict[str, Any]:
        """Fetch a :class:`FullNotice` JSON dict by its GUID."""

        path = f"api/rest/2/get_published_notice/json/{notice_id}"
        return await self._get(path)

    async def get_notice(self, notice_id: str) -> Tender:
        """Fetch a notice by ID and return it as a canonical :class:`Tender`."""

        payload = await self.get_notice_raw(notice_id)
        return normalise_cf_notice(payload)


async def search_tenders(
    query: TenderQuery | None = None,
    *,
    size: int | None = None,
) -> list[Tender]:
    """Convenience wrapper: one-shot search over the public Contracts Finder API."""

    async with ContractsFinderClient() as client:
        return await client.search_tenders(query, size=size)


__all__ = [
    "ContractsFinderClient",
    "search_tenders",
]
