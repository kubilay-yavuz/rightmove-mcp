"""Async client for the VOA check-council-tax-band service.

The public VOA service is a standard GOV.UK form-backed flow:

1. GET the search form to pick up session cookies + a CSRF token.
2. POST the form with ``{csrfToken, postcode}``; the response body is page 0
   of the results.
3. Follow opaque pagination links (``?postcode=<token>&page=N``) as GETs to
   read the remaining pages.

The service is light on rate limits ("polite scrape" per the design plan) but
every request carries a full desktop User-Agent to avoid the default
``httpx`` fingerprint, and we throttle with an optional semaphore.

Only England and Wales are covered by VOA; Scottish postcodes return an
empty list.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import Final

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import ValidationError
from uk_property_apis.voa.models import CouncilTaxBand, CouncilTaxSearchPage
from uk_property_apis.voa.parser import (
    extract_csrf_token,
    is_no_results_page,
    parse_search_page,
)

logger = logging.getLogger(__name__)

_DEFAULT_USER_AGENT: Final = (
    "Mozilla/5.0 (compatible; uk-property-apis/0.1; "
    "+https://github.com/kubilay-yavuz/uk-property-intel)"
)
_SEARCH_PATH: Final = "check-council-tax-band/search"
_POSTCODE_RE: Final = re.compile(r"^[A-Z]{1,2}\d[A-Z0-9]?\s?\d[A-Z]{2}$", re.IGNORECASE)

# VOA is England + Wales only; every Scottish postcode starts with one of these.
_SCOTTISH_PREFIXES: Final = frozenset(
    {"AB", "DD", "DG", "EH", "FK", "G", "HS", "IV", "KA", "KW", "KY", "ML", "PA", "PH", "TD", "ZE"}
)


def _is_scottish(postcode: str) -> bool:
    """Best-effort check for Scottish postcodes, which VOA does not cover."""

    compact = postcode.strip().upper().replace(" ", "")
    if len(compact) < 5:
        return False
    outward = compact[:-3]
    for length in (2, 1):
        if len(outward) >= length and outward[:length].isalpha() and outward[:length] in _SCOTTISH_PREFIXES:
            return True
    return False


class VOAClient(BaseAPIClient):
    """Scrape the public VOA ``check-council-tax-band`` service.

    Instances maintain a cookie jar per client, so the initial GET (which
    issues session cookies) and the subsequent POST reuse the same session.
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
        user_agent: str | None = None,
    ) -> None:
        merged_headers: dict[str, str] = {
            "User-Agent": user_agent or _DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-GB,en;q=0.9",
        }
        if headers:
            merged_headers.update(headers)
        super().__init__(
            base_url="https://www.tax.service.gov.uk/",
            auth=None,
            timeout=timeout,
            semaphore=semaphore,
            headers=merged_headers,
        )

    async def _get_html(self, path: str, *, params: Mapping[str, object] | None = None) -> str:
        response = await self._raw_request("GET", path, params=params, follow_redirects=True)
        if response.status_code >= 400:
            self._map_http_error(response)
        return response.text

    async def _post_form_html(self, path: str, *, data: Mapping[str, str]) -> str:
        response = await self._raw_request(
            "POST",
            path,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=True,
        )
        if response.status_code >= 400:
            self._map_http_error(response)
        return response.text

    def _normalise_postcode(self, raw: str) -> str:
        stripped = raw.strip().upper()
        if not _POSTCODE_RE.match(stripped):
            msg = f"Invalid UK postcode: {raw!r}"
            raise ValidationError(msg)
        compact = stripped.replace(" ", "")
        return f"{compact[:-3]} {compact[-3:]}"

    async def fetch_page(
        self,
        postcode: str,
        *,
        page: int = 0,
        postcode_token: str | None = None,
    ) -> CouncilTaxSearchPage:
        """Fetch one page of VOA search results.

        Page 0 uses the POST flow (with a CSRF token). Pages ≥1 follow the
        opaque ``postcode_token`` that the page-0 response disclosed in its
        pagination links.
        """

        normalised = self._normalise_postcode(postcode)
        if page == 0:
            form_html = await self._get_html(_SEARCH_PATH)
            csrf = extract_csrf_token(form_html)
            if not csrf:
                raise ValidationError(
                    "VOA search form missing csrfToken; page shape may have changed"
                )
            html = await self._post_form_html(
                _SEARCH_PATH,
                data={"csrfToken": csrf, "postcode": normalised},
            )
        else:
            if not postcode_token:
                raise ValidationError("postcode_token is required for page > 0")
            html = await self._get_html(
                _SEARCH_PATH,
                params={"postcode": postcode_token, "page": page},
            )
        if is_no_results_page(html):
            return CouncilTaxSearchPage(rows=[], total_results=0)
        return parse_search_page(html)

    async def search_by_postcode(
        self,
        postcode: str,
        *,
        max_pages: int = 10,
    ) -> list[CouncilTaxBand]:
        """Return every council-tax band in ``postcode`` across all pages.

        Scottish postcodes short-circuit to ``[]`` (VOA doesn't cover them).
        ``max_pages`` caps cost: a postcode with 200+ rows is extraordinary
        (a whole block of flats) and the default limit of 10 already yields
        200 rows.
        """

        if _is_scottish(postcode):
            logger.debug("VOA has no data for Scottish postcode %s; returning []", postcode)
            return []

        first = await self.fetch_page(postcode, page=0)
        if not first.rows:
            return []
        rows: list[CouncilTaxBand] = list(first.rows)
        token = first.next_postcode_token
        has_next = first.has_next_page
        page = 1
        while has_next and token and page < max_pages:
            result = await self.fetch_page(postcode, page=page, postcode_token=token)
            rows.extend(result.rows)
            has_next = result.has_next_page
            token = result.next_postcode_token or token
            page += 1
        return rows


__all__ = ["VOAClient"]
