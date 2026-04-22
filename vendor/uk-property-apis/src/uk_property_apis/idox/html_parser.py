"""Pure HTML parsers for the IDOX Public Access planning portal.

Every function here is a ``html: str -> T`` transform with no HTTP I/O,
so they unit-test cleanly against fixtures and are reusable from Apify
actors or other callers that already have the HTML in hand.

The IDOX HTML layout we target (as observed on Lambeth, Apr 2026 — the
template is shared across all IDOX Public Access installations because
they're generated from the same WAR):

* **Search form** at ``/online-applications/search.do?action=simple``
  contains ``<form id="simpleSearchForm">`` with a hidden ``_csrf`` token
  that must be POSTed alongside the search query.
* **Results page** (``simpleSearchResults.do?action=firstPage`` for page 1,
  ``pagedSearchResults.do?action=page&searchCriteria.page=N`` for later
  pages) renders matches as ``<ul id="searchresults"><li class="searchresult">``
  items plus a ``<p class="pager …">`` with paging controls.
* **Empty state** lives in ``<div class="messagebox"><li>No results found.</li></div>``.
* **Too-many state** (the server refuses to return results when the match
  set is very large) lives in ``<div class="messagebox errors"><li>Too many
  results found…</li></div>`` — parsers surface this as
  :class:`TooManyResults`.
* **Detail page** (``applicationDetails.do?keyVal=…``) carries a simple
  ``<table id="simpleDetailsTable">`` with ``<tr><th scope="row">Label</th>
  <td>Value</td></tr>`` rows plus a ``<div id="summaryInfo">`` block that
  advertises document/related-case counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final
from urllib.parse import parse_qs, urlsplit

from selectolax.parser import HTMLParser, Node

from uk_property_apis.idox.models import (
    ApplicationDetail,
    CouncilConfig,
    PlanningApplication,
)

# Order matters: more specific patterns (e.g. "Appeal Status") must match
# before their shorter counterparts would swallow them. We lowercase both
# sides before comparing so the table is case-insensitive.
_LABELS: Final = {
    "reference": "reference",
    "application received": "received_date",
    "application validated": "validated_date",
    "address": "address",
    "proposal": "proposal",
    "status": "status",
    "appeal status": "appeal_status",
    "appeal decision": "appeal_decision",
    "decision": "decision",
    "decision issued date": "decision_date",
    "decision printed date": "decision_date",
    "decision made date": "decision_date",
    "case officer": "case_officer",
    "ward": "ward",
    "parish": "parish",
    "applicant name": "applicant_name",
    "agent name": "agent_name",
}

_KEYVAL_HREF_RE: Final = re.compile(
    r"keyVal=([A-Za-z0-9]+)",
    re.IGNORECASE,
)
_REF_RE: Final = re.compile(r"Ref\.?\s*No\.?:", re.IGNORECASE)
_PAGER_TOTAL_RE: Final = re.compile(
    r"Showing\s+\d+\s*[\-\u2013]\s*\d+\s+of\s+(\d+)",
    re.IGNORECASE,
)
_PAGE_PARAM_RE: Final = re.compile(r"searchCriteria\.page=(\d+)")
_ASSOC_DOCS_RE: Final = re.compile(r"(\d+)\s+documents?", re.IGNORECASE)
_ASSOC_CASE_RE: Final = re.compile(r"(\d+)\s+cases?", re.IGNORECASE)
_ASSOC_PROP_RE: Final = re.compile(r"(\d+)\s+propert(?:y|ies)", re.IGNORECASE)


# ── Message-box states ──────────────────────────────────────────────────────


class TooManyResultsError(Exception):
    """Raised when IDOX refuses to list results because the match set is huge.

    The aggregator should re-issue with a narrower query (add a postcode
    or pick a shorter date window); silently returning ``[]`` here would
    misrepresent a large match set as an empty one.
    """


def _message_box_text(html: str) -> tuple[str | None, str]:
    """Return ``(classes, text)`` for the first status ``messagebox``, if any.

    IDOX surfaces both informational ('No results found') and error
    ('Too many results found') states via the same ``<div
    class="messagebox…">`` block; the class list distinguishes them. The
    welcome banner ``<div class="messagebox announcement">`` that sits in
    the left rail on every page is filtered out here, otherwise it would
    dominate the lookup.
    """

    tree = HTMLParser(html)
    for node in tree.css("div.messagebox"):
        classes = (node.attributes.get("class") or "").strip()
        if "announcement" in classes:
            continue
        li = node.css_first("li")
        return classes or None, (li.text(strip=True) if li else node.text(strip=True))
    return None, ""


def is_no_results_page(html: str) -> bool:
    """True when IDOX rendered 'No results found.'."""

    classes, text = _message_box_text(html)
    if classes is None:
        return False
    return "errors" not in classes and "no results" in text.lower()


def is_too_many_results_page(html: str) -> bool:
    """True when IDOX refused to list results ('Too many results found…')."""

    classes, text = _message_box_text(html)
    if classes is None:
        return False
    return "errors" in classes and "too many" in text.lower()


# ── Search-form parser ──────────────────────────────────────────────────────


def extract_csrf_token(html: str) -> str | None:
    """Return the ``_csrf`` hidden input from the simple-search form.

    IDOX Public Access uses a standard Spring Security CSRF token scoped
    to the session cookie. We pick it up from the initial GET on the
    search form, then POST it back alongside the query.
    """

    tree = HTMLParser(html)
    node = tree.css_first('form#simpleSearchForm input[name="_csrf"]')
    if node is None:
        node = tree.css_first('input[name="_csrf"]')
    if node is None:
        return None
    value = node.attributes.get("value")
    return value.strip() if value else None


# ── Results-page parser ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResultsPage:
    """Parsed IDOX results page: rows + pagination breadcrumbs."""

    applications: list[PlanningApplication]
    current_page: int
    total_pages: int
    total_results: int | None


def _normalise_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_short_date(raw: str) -> date | None:
    """Parse IDOX's 'Wed 14 Jan 2026' (or 'Thu 15 Jan 2026') into a date.

    Falls back to ``None`` when the label is absent or unparseable
    (e.g. '' or 'Not Available'); never raises.
    """

    cleaned = _normalise_ws(raw)
    if not cleaned or cleaned.lower() in {"not available", "unknown"}:
        return None
    for fmt in ("%a %d %b %Y", "%d %b %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _extract_keyval_from_href(href: str) -> str | None:
    match = _KEYVAL_HREF_RE.search(href)
    return match.group(1) if match else None


def _parse_meta_info(meta: Node) -> dict[str, str]:
    """Parse the ``<p class="metaInfo">`` block into a label-value dict.

    IDOX renders the block as:

        Ref. No: 26/00115/VOC | Received: Wed 14 Jan 2026 | Validated:
        Thu 15 Jan 2026 | Status: Awaiting decision

    with ``<span class="divider">|</span>`` between each pair. We rebuild
    the plain-text form, split on the pipe, and then split each chunk on
    the first ':'.
    """

    parts: list[str] = []
    for node in meta.iter(include_text=True):
        if node.tag == "span" and "divider" in (node.attributes.get("class") or ""):
            parts.append("|")
            continue
        parts.append(node.text(strip=False))
    flattened = _normalise_ws("".join(parts))
    fields: dict[str, str] = {}
    for chunk in (chunk.strip() for chunk in flattened.split("|")):
        if not chunk or ":" not in chunk:
            continue
        label, _, value = chunk.partition(":")
        fields[_normalise_ws(label).lower()] = _normalise_ws(value)
    return fields


def _parse_search_result(
    li: Node,
    *,
    council: CouncilConfig,
) -> PlanningApplication | None:
    link = li.css_first("a[href*='applicationDetails.do']")
    if link is None:
        return None
    href = link.attributes.get("href") or ""
    key_val = _extract_keyval_from_href(href)
    if not key_val:
        return None
    description = _normalise_ws(link.text(strip=False))

    address_node = li.css_first("p.address")
    address = _normalise_ws(address_node.text()) if address_node else ""

    meta_node = li.css_first("p.metaInfo")
    fields: dict[str, str] = _parse_meta_info(meta_node) if meta_node else {}
    reference = fields.get("ref. no") or fields.get("ref no") or fields.get("reference")
    if not reference:
        return None

    received = _parse_short_date(fields.get("received", ""))
    validated = _parse_short_date(fields.get("validated", ""))
    status = fields.get("status") or None

    return PlanningApplication(
        council=council.slug,
        reference=reference,
        key_val=key_val,
        address=address,
        description=description,
        detail_url=council.detail_url(key_val),
        status=status,
        received_date=received,
        validated_date=validated,
    )


def _parse_pager(html: str) -> tuple[int, int, int | None]:
    """Return ``(current_page, total_pages, total_results)``.

    When there's a single page of results IDOX omits the pager entirely,
    in which case we return ``(1, 1, None)``.
    """

    tree = HTMLParser(html)
    pager = tree.css_first("p.pager")
    if pager is None:
        return 1, 1, None

    total_match = _PAGER_TOTAL_RE.search(pager.text())
    total_results = int(total_match.group(1)) if total_match else None

    current_page = 1
    current = pager.css_first("strong:not(.showing strong)")
    # The 'Showing 1-10' strong lives inside a span.showing so we skip it.
    for strong in pager.css("strong"):
        parent = strong.parent
        parent_class = (parent.attributes.get("class") or "") if parent else ""
        if parent and parent.tag == "span" and "showing" in parent_class:
            continue
        text = _normalise_ws(strong.text())
        if text.isdigit():
            current_page = int(text)
            current = strong
            break

    max_page_number = current_page
    for anchor in pager.css("a.page"):
        href = anchor.attributes.get("href") or ""
        match = _PAGE_PARAM_RE.search(href)
        if match:
            max_page_number = max(max_page_number, int(match.group(1)))

    # ``total_pages`` = max of what we can see in the pager. IDOX normally
    # shows the first N pages plus a 'next 10' link; if the caller needs
    # more than that they can follow ``has_next_page``/``next_page_number``.
    total_pages = max(current_page, max_page_number)
    _ = current  # retained for readability; value unused outside the loop
    return current_page, total_pages, total_results


def parse_results_page(html: str, *, council: CouncilConfig) -> ResultsPage:
    """Parse one IDOX results page into a :class:`ResultsPage`.

    Raises :class:`TooManyResults` when the HTML contains the 'Too many
    results found' error, because silently returning ``[]`` would hide a
    very different condition from the caller.
    """

    if is_too_many_results_page(html):
        raise TooManyResultsError(
            "IDOX refused to list results — too many matches; narrow the query."
        )
    if is_no_results_page(html):
        return ResultsPage(applications=[], current_page=1, total_pages=1, total_results=0)

    tree = HTMLParser(html)
    results: list[PlanningApplication] = []
    results_ul = tree.css_first("ul#searchresults")
    if results_ul is not None:
        for li in results_ul.css("li.searchresult"):
            parsed = _parse_search_result(li, council=council)
            if parsed is not None:
                results.append(parsed)

    current_page, total_pages, total_results = _parse_pager(html)
    return ResultsPage(
        applications=results,
        current_page=current_page,
        total_pages=total_pages,
        total_results=total_results,
    )


def next_page_number(html: str) -> int | None:
    """Return the next page number from the pager, or ``None`` if there isn't one."""

    current_page, total_pages, _ = _parse_pager(html)
    if total_pages > current_page:
        return current_page + 1
    # Check for a '…next 10' link that may advertise a higher page number
    # beyond the currently-rendered numeric anchors.
    tree = HTMLParser(html)
    for anchor in tree.css("p.pager a"):
        href = anchor.attributes.get("href") or ""
        match = _PAGE_PARAM_RE.search(href)
        if not match:
            continue
        page = int(match.group(1))
        if page > current_page:
            return page
    return None


def all_keyvals_on_page(html: str) -> list[str]:
    """Return every ``keyVal`` referenced on the page (de-duplicated, ordered).

    Useful for tests and for the aggregator's cheap "have I seen this
    already?" deltas.
    """

    seen: set[str] = set()
    ordered: list[str] = []
    tree = HTMLParser(html)
    for anchor in tree.css("a[href*='keyVal=']"):
        href = anchor.attributes.get("href") or ""
        key_val = _extract_keyval_from_href(href)
        if key_val and key_val not in seen:
            seen.add(key_val)
            ordered.append(key_val)
    return ordered


def paged_search_params(next_url: str) -> dict[str, str]:
    """Return the query parameters of an IDOX pagedSearchResults link.

    Only the ``searchCriteria.page`` param is material to the client, but
    we return the full set for debugging and forwards-compatibility.
    """

    query = urlsplit(next_url).query
    return {k: v[0] for k, v in parse_qs(query).items() if v}


# ── Detail-page parser ──────────────────────────────────────────────────────


def _extract_table_fields(html: str) -> dict[str, str]:
    """Return a ``{lowercased label: cell text}`` map for the summary table."""

    tree = HTMLParser(html)
    fields: dict[str, str] = {}
    table = tree.css_first("table#simpleDetailsTable")
    if table is None:
        return fields
    for row in table.css("tr"):
        label = row.css_first("th[scope='row']")
        value = row.css_first("td")
        if label is None or value is None:
            continue
        label_text = _normalise_ws(label.text()).lower()
        # Strip span wrappers (e.g. ``<span class="caseDetailsStatus">``)
        # but preserve line breaks so multi-line proposals read naturally.
        value_text = _normalise_ws(
            value.text(separator="\n", strip=True).replace("\u00a0", " ")
        )
        if label_text:
            fields[label_text] = value_text
    return fields


def _extract_summary_counts(html: str) -> dict[str, int]:
    """Return ``{'documents': N, 'cases': M, 'properties': P}`` where present.

    IDOX advertises document counts in a ``<div id="summaryInfo">`` block
    via ``<p class="associateddocument">There are N documents…</p>``.
    """

    tree = HTMLParser(html)
    counts: dict[str, int] = {}
    summary = tree.css_first("div#summaryInfo")
    if summary is None:
        return counts
    text = summary.text()
    for key, pattern in (
        ("documents", _ASSOC_DOCS_RE),
        ("cases", _ASSOC_CASE_RE),
        ("properties", _ASSOC_PROP_RE),
    ):
        match = pattern.search(text)
        if match:
            counts[key] = int(match.group(1))
    return counts


def parse_detail_page(
    html: str,
    *,
    council: CouncilConfig,
    key_val: str | None = None,
) -> ApplicationDetail:
    """Parse ``applicationDetails.do?activeTab=summary`` into ApplicationDetail.

    ``key_val`` is honoured when provided (the caller usually knows it);
    when omitted we try to recover it from the 'Print summary' link near
    the top of the page.
    """

    tree = HTMLParser(html)
    if key_val is None:
        for anchor in tree.css("a[href*='keyVal=']"):
            href = anchor.attributes.get("href") or ""
            recovered = _extract_keyval_from_href(href)
            if recovered:
                key_val = recovered
                break
    if not key_val:
        msg = "detail page is missing keyVal (unexpected IDOX shape)"
        raise ValueError(msg)

    fields = _extract_table_fields(html)
    counts = _extract_summary_counts(html)

    reference = fields.get("reference", "")
    address = fields.get("address", "")
    proposal = fields.get("proposal")

    return ApplicationDetail(
        council=council.slug,
        reference=reference,
        key_val=key_val,
        address=address,
        description=proposal or "",
        detail_url=council.detail_url(key_val),
        status=fields.get("status"),
        received_date=_parse_short_date(fields.get("application received", "")),
        validated_date=_parse_short_date(fields.get("application validated", "")),
        proposal=proposal,
        appeal_status=_none_if_unknown(fields.get("appeal status")),
        appeal_decision=_none_if_unknown(fields.get("appeal decision")),
        decision=_none_if_unknown(fields.get("decision")),
        decision_date=_parse_short_date(
            fields.get("decision issued date")
            or fields.get("decision printed date")
            or fields.get("decision made date", "")
        ),
        case_officer=_none_if_unknown(fields.get("case officer")),
        ward=_none_if_unknown(fields.get("ward")),
        parish=_none_if_unknown(fields.get("parish")),
        applicant_name=_none_if_unknown(fields.get("applicant name")),
        agent_name=_none_if_unknown(fields.get("agent name")),
        document_count=counts.get("documents"),
        related_case_count=counts.get("cases"),
        related_property_count=counts.get("properties"),
    )


def _none_if_unknown(value: str | None) -> str | None:
    """Collapse IDOX's 'Unknown' / 'Not Available' placeholders to ``None``."""

    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped.lower() in {"unknown", "not available", "n/a"}:
        return None
    return stripped


__all__ = [
    "ResultsPage",
    "TooManyResultsError",
    "all_keyvals_on_page",
    "extract_csrf_token",
    "is_no_results_page",
    "is_too_many_results_page",
    "next_page_number",
    "paged_search_params",
    "parse_detail_page",
    "parse_results_page",
]
