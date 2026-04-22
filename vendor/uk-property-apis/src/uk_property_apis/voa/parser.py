"""Pure HTML parsers for the VOA check-council-tax-band service.

Every function in this module is a pure ``html: str -> T`` transform, so they
are unit-testable from saved fixtures and reusable outside the async client
(e.g. from an Apify actor that already has the HTML in hand).
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import parse_qs, urlsplit

from selectolax.parser import HTMLParser, Node

from uk_property_apis.voa.models import CouncilTaxBand, CouncilTaxSearchPage

_PROPERTY_HREF_RE: Final = re.compile(
    r"/check-council-tax-band/property/([0-9a-f-]{8,})",
    re.IGNORECASE,
)
_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}\d[A-Z0-9]?\s?\d[A-Z]{2})\b",
    re.IGNORECASE,
)
_TOTAL_RESULTS_RE: Final = re.compile(
    r"Showing\s+\d+\s*[\-\u2013]\s*\d+\s+of\s+(\d+)\s+results",
    re.IGNORECASE,
)
_NO_RESULTS_RE: Final = re.compile(r"No results for\s+", re.IGNORECASE)


def extract_csrf_token(html: str) -> str | None:
    """Return the ``csrfToken`` hidden-input value from a VOA search form.

    Returns ``None`` if the form is missing (e.g. the page unexpectedly
    changed shape or we were served an error).
    """

    tree = HTMLParser(html)
    node = tree.css_first('input[name="csrfToken"]')
    if node is None:
        return None
    value = node.attributes.get("value")
    return value.strip() if value else None


def is_no_results_page(html: str) -> bool:
    """True when VOA renders the 'No results for X' empty-state heading."""

    tree = HTMLParser(html)
    title = tree.css_first("title")
    if title is not None and title.text(strip=True).lower().startswith("no results for"):
        return True
    heading = tree.css_first("h1.govuk-heading-l")
    return heading is not None and _NO_RESULTS_RE.match(heading.text(strip=True)) is not None


def extract_total_results(html: str) -> int | None:
    """Parse VOA's 'Showing 1 - 20 of 47 results' banner, if rendered."""

    tree = HTMLParser(html)
    for node in tree.css(".govuk-grid-column-one-half"):
        match = _TOTAL_RESULTS_RE.search(node.text(strip=True))
        if match:
            return int(match.group(1))
    match = _TOTAL_RESULTS_RE.search(html)
    if match:
        return int(match.group(1))
    return None


def extract_next_postcode_token(html: str) -> str | None:
    """Return the opaque ``postcode`` param VOA uses in its pagination URLs.

    The initial POST is in plaintext (``postcode=EC1V 3AP``), but subsequent
    pages use a per-session opaque token (e.g.
    ``postcode=FHZgUQHx98Z_Vs3AtRYs2A``). We pull it from the first pagination
    link we can find.
    """

    tree = HTMLParser(html)
    for anchor in tree.css("a.hmrc-vo-pagination__link"):
        href = anchor.attributes.get("href") or ""
        if not href or href.strip() == "#":
            continue
        query = urlsplit(href).query
        params = parse_qs(query)
        token = (params.get("postcode") or [None])[0]
        if token:
            return token
    return None


def _parse_row(row: Node) -> CouncilTaxBand | None:
    cells = row.css("td.govuk-table__cell")
    if len(cells) < 2:
        return None

    address_cell = cells[0]
    band_cell = cells[1]
    authority_cell = cells[2] if len(cells) >= 3 else None

    link = address_cell.css_first("a")
    if link is None:
        return None
    href = link.attributes.get("href") or ""
    match = _PROPERTY_HREF_RE.search(href)
    if not match:
        return None
    property_id = match.group(1)

    address_text = link.text(strip=True)
    title_attr = link.attributes.get("title") or ""
    canonical_address = address_text or title_attr

    postcode_match = _POSTCODE_RE.search(title_attr) or _POSTCODE_RE.search(canonical_address)
    postcode = postcode_match.group(1).upper() if postcode_match else ""

    band = band_cell.text(strip=True).upper()
    if not band:
        return None

    authority_name: str | None = None
    authority_url: str | None = None
    if authority_cell is not None:
        authority_link = authority_cell.css_first("a")
        if authority_link is not None:
            authority_name = authority_link.text(strip=True) or None
            authority_url = (authority_link.attributes.get("href") or "").strip() or None
        else:
            authority_name = authority_cell.text(strip=True) or None

    return CouncilTaxBand(
        property_id=property_id,
        address=canonical_address,
        postcode=postcode,
        band=band,
        local_authority=authority_name,
        local_authority_url=authority_url,
    )


def parse_results_page(html: str) -> list[CouncilTaxBand]:
    """Parse one VOA results page into :class:`CouncilTaxBand` rows."""

    if is_no_results_page(html):
        return []

    tree = HTMLParser(html)
    rows: list[CouncilTaxBand] = []
    table = tree.css_first("table#search-results-table")
    if table is None:
        return []

    for tr in table.css("tbody tr.govuk-table__row"):
        parsed = _parse_row(tr)
        if parsed is not None:
            rows.append(parsed)
    return rows


def parse_search_page(html: str) -> CouncilTaxSearchPage:
    """Parse a VOA results page into a full :class:`CouncilTaxSearchPage`.

    Surfaces both the parsed rows and the pagination state so callers can
    decide whether to fetch more pages.
    """

    rows = parse_results_page(html)
    total = extract_total_results(html) if rows else None
    next_token = extract_next_postcode_token(html)
    has_next = _has_next_page(html)
    return CouncilTaxSearchPage(
        rows=rows,
        total_results=total,
        next_postcode_token=next_token,
        has_next_page=has_next,
    )


def _has_next_page(html: str) -> bool:
    tree = HTMLParser(html)
    for item in tree.css(".hmrc-vo-pagination__item--next"):
        anchor = item.css_first("a.hmrc-vo-pagination__link")
        if anchor is None:
            continue
        href = anchor.attributes.get("href") or ""
        if href and href.strip() != "#":
            return True
    return False


__all__ = [
    "extract_csrf_token",
    "extract_next_postcode_token",
    "extract_total_results",
    "is_no_results_page",
    "parse_results_page",
    "parse_search_page",
]
