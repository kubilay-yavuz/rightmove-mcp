"""Async HTML-transport client for IDOX Public Access planning portals.

This is the form-POST fallback for councils that don't expose a public
ArcGIS FeatureServer (e.g. Westminster, Manchester, Southwark, Leeds).
It faithfully reproduces the browser's conversation with
``/online-applications``:

1. **GET** ``/online-applications/search.do?action=simple&searchType=Application``
   to pick up the JSESSIONID cookie and the ``_csrf`` hidden input.
2. **POST** ``/online-applications/simpleSearchResults.do?action=firstPage``
   with ``{_csrf, searchType=Application, searchCriteria.simpleSearch=true,
   searchCriteria.simpleSearchString=<query>}`` to land on page 1 of
   results.
3. **GET** ``/online-applications/pagedSearchResults.do?action=page&
   searchCriteria.page=N`` for subsequent pages — IDOX keeps the search
   state in the session cookie, so only the page number travels on the
   URL.
4. **GET** ``/online-applications/applicationDetails.do?keyVal=<K>&
   activeTab=summary`` for the per-application detail page.

The client maintains one ``httpx.AsyncClient`` across these steps so the
session cookie is preserved; each call returns a canonical
:class:`PlanningApplication` (or :class:`ApplicationDetail`) identical
to what the ArcGIS transport produces — callers can swap transports
without any code downstream.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import TYPE_CHECKING, Final

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import NotFoundError, ValidationError
from uk_property_apis.idox.html_parser import (
    TooManyResultsError,
    extract_csrf_token,
    parse_detail_page,
    parse_results_page,
)

if TYPE_CHECKING:
    from uk_property_apis.idox.html_parser import ResultsPage
    from uk_property_apis.idox.models import (
        ApplicationDetail,
        CouncilConfig,
        PlanningApplication,
    )

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (compatible; uk-property-apis/0.1; "
    "+https://github.com/kubilay-yavuz/uk-property-intel)"
)

_SEARCH_FORM_PATH: Final = "online-applications/search.do"
_FIRST_PAGE_PATH: Final = "online-applications/simpleSearchResults.do"
_PAGED_PATH: Final = "online-applications/pagedSearchResults.do"
_DETAIL_PATH: Final = "online-applications/applicationDetails.do"

# Each IDOX results page renders 10 applications. ``max_pages=20`` yields
# up to 200 rows, which matches the recommended ceiling before a search
# needs narrowing (IDOX starts refusing with 'Too many results' beyond
# ~1,000 matches anyway).
_DEFAULT_MAX_PAGES: Final = 20


class HTMLPlanningClient(BaseAPIClient):
    """Scrape IDOX Public Access planning via the HTML transport.

    Mirrors the public method shape of :class:`ArcGISPlanningClient` as
    closely as the HTML surface allows — ``search_by_address`` maps to
    ``simpleSearch`` with the address as the query, ``get_by_reference``
    does the same with the planning reference, ``get_by_key_val`` hits
    the detail page directly.

    Intentionally omitted (ArcGIS-only, not exposed by the HTML UI):

    * ``recent_applications`` — IDOX has a ``weeklyList`` page but it's
      aimed at humans and returns a different result shape; out of
      scope for this fallback.
    * ``applications_in_bbox`` — the HTML portal has a ``spatialDisplay``
      map tool but it doesn't expose a public bounding-box query.
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
        merged_headers: dict[str, str] = {
            "User-Agent": user_agent or _DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9",
            "Accept-Language": "en-GB,en;q=0.9",
        }
        if headers:
            merged_headers.update(headers)
        super().__init__(
            base_url=council.public_access_base_url,
            timeout=timeout,
            semaphore=semaphore,
            headers=merged_headers,
        )
        self._council = council
        # Cached per-session so we only GET the form once per client.
        self._csrf_token: str | None = None

    @property
    def council(self) -> CouncilConfig:
        """The council configuration this client is bound to."""

        return self._council

    # ── Low-level helpers ────────────────────────────────────────────────

    async def _get_html(
        self,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> str:
        response = await self._raw_request(
            "GET",
            path,
            params=params,
            follow_redirects=True,
        )
        if response.status_code >= 400:
            self._map_http_error(response)
        return response.text

    async def _post_form_html(
        self,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        data: Mapping[str, str],
    ) -> str:
        response = await self._raw_request(
            "POST",
            path,
            params=params,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=True,
        )
        if response.status_code >= 400:
            self._map_http_error(response)
        return response.text

    async def _ensure_csrf(self) -> str:
        if self._csrf_token:
            return self._csrf_token
        form_html = await self._get_html(
            _SEARCH_FORM_PATH,
            params={"action": "simple", "searchType": "Application"},
        )
        token = extract_csrf_token(form_html)
        if not token:
            raise ValidationError(
                "IDOX search form is missing the _csrf token — council page "
                "shape may have changed or the session was not issued."
            )
        self._csrf_token = token
        return token

    def _reset_csrf(self) -> None:
        """Drop the cached CSRF so the next call re-fetches the form.

        The token is session-scoped; IDOX rotates it when the session
        expires, so failed POSTs should bin it.
        """

        self._csrf_token = None

    # ── Results pages ────────────────────────────────────────────────────

    async def fetch_first_page(self, query: str) -> ResultsPage:
        """POST page 1 of simple-search results for ``query``.

        Raises :class:`TooManyResultsError` when IDOX refuses to list matches
        because the query is too broad. Returns an empty
        :class:`ResultsPage` when IDOX explicitly says 'No results found'.
        """

        if not query or not query.strip():
            raise ValidationError("IDOX simple-search requires a non-empty query")
        csrf = await self._ensure_csrf()
        html = await self._post_form_html(
            _FIRST_PAGE_PATH,
            params={"action": "firstPage"},
            data={
                "_csrf": csrf,
                "searchType": "Application",
                "action": "firstPage",
                "searchCriteria.simpleSearchString": query.strip(),
                "searchCriteria.simpleSearch": "true",
            },
        )
        return parse_results_page(html, council=self._council)

    async def fetch_next_page(self, page: int) -> ResultsPage:
        """GET ``pagedSearchResults.do?page=N`` using the active session."""

        if page < 2:
            raise ValidationError("fetch_next_page requires page >= 2")
        html = await self._get_html(
            _PAGED_PATH,
            params={"action": "page", "searchCriteria.page": page},
        )
        return parse_results_page(html, council=self._council)

    # ── High-level search ───────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        max_pages: int = _DEFAULT_MAX_PAGES,
        max_results: int | None = None,
    ) -> list[PlanningApplication]:
        """Materialise up to ``max_pages * 10`` matches for ``query``.

        The iterator short-circuits on any of: no further pager link,
        ``max_pages`` reached, or ``max_results`` reached. Always yields
        rows in the order IDOX returns them (most recent first by
        default).
        """

        results: list[PlanningApplication] = []
        async for app in self.iter_search(query, max_pages=max_pages):
            results.append(app)
            if max_results is not None and len(results) >= max_results:
                break
        return results

    async def iter_search(
        self,
        query: str,
        *,
        max_pages: int = _DEFAULT_MAX_PAGES,
    ) -> AsyncIterator[PlanningApplication]:
        """Async-iterate matches for ``query``, one page at a time.

        Use this when you want to stream into a sink (e.g. Apify dataset)
        rather than materialising every row in memory.

        Stops when any of these is true:
        * a page returns no applications (end of results),
        * ``max_pages`` is reached,
        * the pager metadata confirms there's no next page.
        """

        if max_pages <= 0:
            return
        first = await self.fetch_first_page(query)
        for app in first.applications:
            yield app
        if not first.applications:
            return

        current_page = first.current_page
        has_next = first.total_pages > current_page
        fetched_pages = 1

        while has_next and fetched_pages < max_pages:
            target_page = current_page + 1
            try:
                page = await self.fetch_next_page(target_page)
            except TooManyResultsError:
                return
            for app in page.applications:
                yield app
            if not page.applications:
                return
            current_page = page.current_page
            has_next = page.total_pages > current_page
            fetched_pages += 1

    # ── Ergonomic wrappers ─────────────────────────────────────────────

    async def search_by_address(
        self,
        address_or_postcode: str,
        *,
        max_pages: int = _DEFAULT_MAX_PAGES,
        max_results: int | None = None,
    ) -> list[PlanningApplication]:
        """Shortcut for :meth:`search` — same contract, clearer intent."""

        return await self.search(
            address_or_postcode,
            max_pages=max_pages,
            max_results=max_results,
        )

    async def get_by_reference(self, reference: str) -> PlanningApplication | None:
        """Best-effort lookup of ``reference`` via simple-search.

        **Known limitation** — IDOX's simple search only indexes addresses
        and descriptions, *not* planning references. Against live councils
        a query like ``'26/00115/VOC'`` typically returns zero results.
        For reliable by-reference lookups:

        * Prefer :meth:`ArcGISPlanningClient.get_by_reference` when the
          council publishes the FeatureServer.
        * Otherwise, search for the address first and filter client-side,
          or open the detail page directly with :meth:`get_by_key_val`
          if the caller already knows the keyVal.

        Returns ``None`` when simple-search yields no rows that match
        ``reference`` exactly.
        """

        cleaned = reference.strip()
        if not cleaned:
            raise ValidationError("reference must be a non-empty string")
        try:
            page = await self.fetch_first_page(cleaned)
        except TooManyResultsError:
            return None
        for app in page.applications:
            if app.reference.strip().lower() == cleaned.lower():
                return app
        return None

    async def get_by_key_val(self, key_val: str) -> ApplicationDetail:
        """Fetch the full detail page for ``key_val``.

        Raises :class:`NotFoundError` when IDOX returns a 404 for the
        requested keyVal (can happen for purged historical records).
        """

        cleaned = key_val.strip()
        if not cleaned:
            raise ValidationError("key_val must be a non-empty string")
        try:
            html = await self._get_html(
                _DETAIL_PATH,
                params={"activeTab": "summary", "keyVal": cleaned},
            )
        except NotFoundError:
            raise
        return parse_detail_page(html, council=self._council, key_val=cleaned)

    async def get_detail_by_reference(self, reference: str) -> ApplicationDetail | None:
        """Convenience: locate ``reference`` then fetch its detail page."""

        listing = await self.get_by_reference(reference)
        if listing is None:
            return None
        return await self.get_by_key_val(listing.key_val)


__all__ = ["HTMLPlanningClient"]
