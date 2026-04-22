"""Async client for the Find a Tender Service (FTS) OCDS API.

Target: ``https://www.find-tender.service.gov.uk/``.

FTS replaced TED (OJEU) for UK-national above-threshold procurement
post-Brexit. Every above-threshold UK public-sector tender publishes here
first, which makes it the richer signal for property-adjacent contracts
at scale: housing-association framework agreements, major regen scheme
build packages, authority-wide FM contracts, Network Rail estate works.

The API speaks OCDS 1.1.5 (Open Contracting Data Standard) release
packages. The one endpoint we care about is
``GET /api/1.0/ocdsReleasePackages`` which returns paginated release
packages filtered by ``updatedFrom`` / ``updatedTo``. Pagination uses a
``cursor`` query-string parameter (opaque token, max 300 chars); the
response ``links.next`` carries the cursor for the next page.

Unlike Contracts Finder, FTS **requires authentication**: a ``CDP-Api-Key``
HTTP header. Keys are issued per organisation via the Commercial Digital
Platform and land in ``FTS_API_KEY`` (or the equivalent env var) in our
deploys. Anonymous calls return 401.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.tenders._normalise import normalise_fts_release
from uk_property_apis.tenders.models import (
    Tender,
    TenderQuery,
)

_BASE_URL = "https://www.find-tender.service.gov.uk/"
_PACKAGES_PATH = "api/1.0/ocdsReleasePackages"


def _api_key_from_env() -> str | None:
    return os.environ.get("FTS_API_KEY") or os.environ.get("CDP_API_KEY")


def _isoformat(value: object) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        rendered = iso()
        if isinstance(rendered, str):
            stripped = rendered
            if stripped.endswith("+00:00"):
                stripped = stripped.replace("+00:00", "Z")
            return stripped
    if isinstance(value, str):
        return value
    return None


def _extract_cursor(link: object) -> str | None:
    """Pull the ``cursor`` query-string value out of an OCDS ``links.next`` URL."""

    if not isinstance(link, str) or not link:
        return None
    try:
        parts = urlsplit(link)
    except ValueError:
        return None
    query = parse_qs(parts.query)
    cursors = query.get("cursor")
    if not cursors:
        return None
    value = cursors[0]
    return value if value else None


class FTSClient(BaseAPIClient):
    """Client for the Find a Tender Service OCDS API."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = _BASE_URL,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        key = api_key if api_key is not None else _api_key_from_env()
        if key is None:
            msg = (
                "Find a Tender API key required: pass api_key=... or set "
                "FTS_API_KEY / CDP_API_KEY"
            )
            raise ValueError(msg)
        default_headers: dict[str, str] = {"CDP-Api-Key": key}
        if headers:
            default_headers.update(headers)
        super().__init__(
            base_url=base_url,
            timeout=timeout,
            semaphore=semaphore,
            headers=default_headers,
        )

    async def get_release_packages_raw(
        self,
        *,
        updated_from: object = None,
        updated_to: object = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """GET one OCDS release package and return the raw JSON envelope."""

        params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
        from_iso = _isoformat(updated_from)
        if from_iso:
            params["updatedFrom"] = from_iso
        to_iso = _isoformat(updated_to)
        if to_iso:
            params["updatedTo"] = to_iso
        if cursor:
            params["cursor"] = cursor
        return await self._get(_PACKAGES_PATH, params=params)

    async def iter_release_packages(
        self,
        *,
        updated_from: object = None,
        updated_to: object = None,
        page_size: int = 100,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield release-package envelopes, following ``links.next`` cursors.

        Stops when the API stops returning a next cursor, when it returns
        an empty page, or when ``max_pages`` pages have been visited
        (whichever comes first).
        """

        cursor: str | None = None
        page_count = 0
        while True:
            payload = await self.get_release_packages_raw(
                updated_from=updated_from,
                updated_to=updated_to,
                cursor=cursor,
                limit=page_size,
            )
            yield payload
            page_count += 1
            if max_pages is not None and page_count >= max_pages:
                return
            releases = payload.get("releases")
            if not isinstance(releases, list) or not releases:
                return
            links = payload.get("links")
            next_link = links.get("next") if isinstance(links, dict) else None
            next_cursor = _extract_cursor(next_link)
            if not next_cursor or next_cursor == cursor:
                return
            cursor = next_cursor

    async def iter_releases(
        self,
        *,
        updated_from: object = None,
        updated_to: object = None,
        page_size: int = 100,
        max_pages: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield individual OCDS ``release`` dicts across all paginated pages."""

        async for package in self.iter_release_packages(
            updated_from=updated_from,
            updated_to=updated_to,
            page_size=page_size,
            max_pages=max_pages,
        ):
            releases = package.get("releases")
            if isinstance(releases, list):
                for release in releases:
                    if isinstance(release, dict):
                        yield release

    async def iter_tenders(
        self,
        query: TenderQuery | None = None,
        *,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> AsyncIterator[Tender]:
        """Yield normalised :class:`Tender` rows for the given filters.

        Honours ``query.updated_from`` / ``query.updated_to`` (FTS-native)
        and stops when ``query.limit`` rows have been emitted.
        """

        q = query or TenderQuery()
        budget = q.limit
        size = page_size if page_size is not None else min(100, budget)
        emitted = 0
        async for release in self.iter_releases(
            updated_from=q.updated_from,
            updated_to=q.updated_to,
            page_size=size,
            max_pages=max_pages,
        ):
            tender = normalise_fts_release(release)
            if not _matches_query(tender, q):
                continue
            yield tender
            emitted += 1
            if emitted >= budget:
                return

    async def search_tenders(
        self,
        query: TenderQuery | None = None,
        *,
        page_size: int | None = None,
        max_pages: int | None = None,
    ) -> list[Tender]:
        """Materialise :meth:`iter_tenders` into a list."""

        return [
            t
            async for t in self.iter_tenders(
                query, page_size=page_size, max_pages=max_pages
            )
        ]


def _matches_query(tender: Tender, query: TenderQuery) -> bool:
    """Apply the client-side filters FTS doesn't natively support.

    FTS's ``ocdsReleasePackages`` endpoint only filters by
    ``updatedFrom`` / ``updatedTo`` server-side. Everything else
    (keyword, CPV, regions, notice types, statuses, value bounds,
    published-date bounds) has to be applied in memory against the
    normalised :class:`Tender`.
    """

    if query.keyword:
        needle = query.keyword.lower()
        haystack = " ".join(
            part
            for part in (tender.title, tender.description or "")
            if part
        ).lower()
        if needle not in haystack:
            return False
    if query.cpv_codes:
        codes = {c.upper() for c in query.cpv_codes}
        tender_codes = {
            c.code.upper() for c in tender.classifications if c.scheme.upper() == "CPV"
        }
        if not codes & tender_codes and not any(
            any(tc.startswith(c) for tc in tender_codes) for c in codes
        ):
            return False
    if query.regions:
        wanted = {r.lower() for r in query.regions}
        buyer_region = (tender.buyer.region.lower() if tender.buyer and tender.buyer.region else "")
        loc_region = tender.location.region.lower() if tender.location and tender.location.region else ""
        if not (buyer_region in wanted or loc_region in wanted):
            return False
    if query.notice_types:
        wanted = {t.lower() for t in query.notice_types}
        if tender.notice_type is None or tender.notice_type.lower() not in wanted:
            return False
    if query.statuses:
        wanted = {s.lower() for s in query.statuses}
        if tender.status.value.lower() not in wanted:
            return False
    if query.value_low is not None:
        amount = (
            tender.value.amount
            if tender.value and tender.value.amount is not None
            else tender.value.amount_high
            if tender.value
            else None
        )
        if amount is None or amount < query.value_low:
            return False
    if query.value_high is not None:
        amount = (
            tender.value.amount
            if tender.value and tender.value.amount is not None
            else tender.value.amount_low
            if tender.value
            else None
        )
        if amount is None or amount > query.value_high:
            return False
    if query.published_from is not None and (
        tender.published_date is None
        or tender.published_date < query.published_from
    ):
        return False
    return not (
        query.published_to is not None
        and (
            tender.published_date is None
            or tender.published_date > query.published_to
        )
    )


async def search_tenders(
    query: TenderQuery | None = None,
    *,
    api_key: str | None = None,
    page_size: int | None = None,
    max_pages: int | None = None,
) -> list[Tender]:
    """Convenience wrapper: one-shot paginated search over FTS."""

    async with FTSClient(api_key=api_key) as client:
        return await client.search_tenders(
            query, page_size=page_size, max_pages=max_pages
        )


__all__ = [
    "FTSClient",
    "search_tenders",
]
