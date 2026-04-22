"""Pure per-portal search-URL builders.

Kept deliberately pure (no network, no dependencies beyond stdlib) so both
public crawlers and the private production :class:`Crawler` can share them.
"""

from __future__ import annotations

from urllib.parse import quote

from uk_property_listings.types import SearchQuery


def build_zoopla_search_url(query: SearchQuery, *, page: int = 1) -> str:
    """Construct a Zoopla search URL for ``query``.

    Zoopla expects slugified location in the path, plus price/beds as query
    params. ``pn`` is the 1-indexed page number. See
    :func:`build_zoopla_search_url_fallback` for the slugless variant we use
    when an unrecognised location slug serves an empty "no listings" page.
    """
    slug = _slugify(query.location)
    path = "/for-sale/property" if query.transaction == "sale" else "/to-rent/property"
    qs = _zoopla_query_string(query, page=page)
    return f"https://www.zoopla.co.uk{path}/{slug}/?{qs}"


def build_zoopla_search_url_fallback(query: SearchQuery, *, page: int = 1) -> str:
    """Slug-less Zoopla search URL that relies purely on the ``q=`` parameter.

    Zoopla's location dictionary doesn't cover every UK place (coastal
    hamlets, new-build developments, non-standard neighbourhood names), so
    our primary slug-based URL can 200 with an empty results grid when the
    slug doesn't match a known location. This helper returns the same
    query with a slugless path (``/for-sale/?q=<location>``) so the caller
    can retry on the portal's free-text search. The query string shape is
    otherwise identical to :func:`build_zoopla_search_url` so downstream
    parsers don't need a separate code path.
    """
    path = "/for-sale" if query.transaction == "sale" else "/to-rent"
    qs = _zoopla_query_string(query, page=page)
    return f"https://www.zoopla.co.uk{path}/?{qs}"


def _zoopla_query_string(query: SearchQuery, *, page: int) -> str:
    """Shared query-string builder for Zoopla primary + fallback URLs."""
    params: list[str] = []
    if query.min_price is not None:
        params.append(f"price_min={query.min_price}")
    if query.max_price is not None:
        params.append(f"price_max={query.max_price}")
    if query.min_beds is not None:
        params.append(f"beds_min={query.min_beds}")
    if query.max_beds is not None:
        params.append(f"beds_max={query.max_beds}")
    params.append(f"q={quote(query.location)}")
    params.append(
        "search_source=for-sale" if query.transaction == "sale" else "search_source=to-rent"
    )
    if page > 1:
        params.append(f"pn={page}")
    return "&".join(params)


def build_rightmove_search_url(query: SearchQuery, *, page: int = 1) -> str:
    """Construct a Rightmove search URL.

    Rightmove's current production search takes the form
    ``/property-for-sale/{Location}.html`` (or ``/property-to-rent/…``).
    The legacy ``find.html?searchLocation=X`` variant now serves a
    "we couldn't find that place" disambiguation page, so we use the
    path-based format here. The location slug is title-cased so
    ``"milton keynes"`` becomes ``"Milton-Keynes"`` — Rightmove is
    case-insensitive but the canonical URLs it emits use this shape.
    """
    path = "property-for-sale" if query.transaction == "sale" else "property-to-rent"
    slug = _rightmove_location_slug(query.location)
    qs: list[str] = []
    if query.min_price is not None:
        qs.append(f"minPrice={query.min_price}")
    if query.max_price is not None:
        qs.append(f"maxPrice={query.max_price}")
    if query.min_beds is not None:
        qs.append(f"minBedrooms={query.min_beds}")
    if query.max_beds is not None:
        qs.append(f"maxBedrooms={query.max_beds}")
    if page > 1:
        qs.append(f"index={(page - 1) * 24}")
    query_string = f"?{'&'.join(qs)}" if qs else ""
    return f"https://www.rightmove.co.uk/{path}/{slug}.html{query_string}"


def _rightmove_location_slug(value: str) -> str:
    """Slugify ``value`` for Rightmove's ``/property-for-sale/{slug}.html`` path.

    Normalises to ``Title-Case-Hyphenated`` which mirrors how Rightmove's
    own navigation emits the canonical URL (``/property-for-sale/Milton-Keynes.html``
    rather than the all-lower ``milton-keynes``).
    """
    slug = _slugify(value)
    return "-".join(part.capitalize() for part in slug.split("-") if part)


def build_onthemarket_search_url(query: SearchQuery, *, page: int = 1) -> str:
    """Construct an OnTheMarket search URL."""
    slug = _slugify(query.location)
    path = "for-sale/property" if query.transaction == "sale" else "to-rent/property"
    qs: list[str] = []
    if query.min_price is not None:
        qs.append(f"min-price={query.min_price}")
    if query.max_price is not None:
        qs.append(f"max-price={query.max_price}")
    if query.min_beds is not None:
        qs.append(f"min-bedrooms={query.min_beds}")
    if query.max_beds is not None:
        qs.append(f"max-bedrooms={query.max_beds}")
    if page > 1:
        qs.append(f"page={page}")
    query_string = f"?{'&'.join(qs)}" if qs else ""
    return f"https://www.onthemarket.com/{path}/{slug}/{query_string}"


def _slugify(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c == " " else " " for c in value)
    return "-".join(part for part in cleaned.lower().split() if part)
