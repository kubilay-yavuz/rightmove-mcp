"""Parser for Savills Auctions (``auctions.savills.co.uk``).

Savills' auction portal is a Vue.js-enhanced but server-rendered PHP
site — every lot is already present in the HTML, with the Vue layer
only hydrating interactive bidding widgets. That makes HTML scraping
reliable: we get the full catalogue from a single GET of the auction
page.

Discovery shape
---------------

``/upcoming-auctions`` lists every upcoming auction as an
``.upcoming-calendar__row`` card with:

* ``<h3 class="upcoming-calendar-content__auction_name">21 & 22 April 2026</h3>`` —
  name / date header.
* ``<p class="upcoming-calendar-content__auction_date">Tuesday 21st April 2026</p>`` —
  canonical day-1 date.
* ``<p class="upcoming-calendar-content__auction_location">Remote bidding only…</p>`` —
  venue.
* ``<p class="upcoming-calendar-content__auction_properties">306 properties for sale</p>`` —
  lot count.
* ``<a class="sv-button upcoming-calendar-content__button" href="…/auctions/{slug}-{id}">View catalogue</a>`` —
  catalogue URL. The trailing integer (``-221``) is the stable
  auction id.

Catalogue shape
---------------

Each lot is rendered as ``<li class="lot" id="lot-{id}" data-lot_id="{id}">``
containing:

* ``<ul class="lot-image-list" data-lot_number="{N}">`` — lot number.
* ``<a class="lot-name" href="…/auctions/{slug}/{property-slug}-{id}" title="{address}">{address}</a>`` —
  lot page URL + address. When the slot is a *section marker* (e.g.
  "Commercial Section") the ``href`` collapses to ``…/-{id}`` and the
  title is empty; we skip those.
* ``<p class="price-container guide-price"><span class="value">£575,000</span></p>`` —
  guide price. Values include ``TBA``, point values, ``£X - £Y``
  ranges, and ``Offers in excess of …``.
* ``<div class="lot-details"><ul><li>…</li></ul></div>`` — bullet list
  with property type, tenure, tenancy, and day-of-sale announcement.
* ``<div class="lot-status">…</div>`` — post-auction status (Sold,
  Withdrawn, Sold Prior) when populated.

We derive the catalogue-wide auction date from ``<title>`` in
:func:`parse_auction_metadata`.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Final, cast

from pydantic import HttpUrl, TypeAdapter, ValidationError
from selectolax.parser import HTMLParser, Node

if TYPE_CHECKING:
    from collections.abc import Iterable

from uk_property_scrapers.schema import (
    Address,
    AuctionGuidePrice,
    AuctionHouse,
    AuctionLot,
    AuctionLotStatus,
    AuctionSaleMethod,
    Image,
    PriceQualifier,
    PropertyType,
    Tenure,
)

_ORIGIN: Final = "https://auctions.savills.co.uk"
_URL_ADAPTER: Final = TypeAdapter(HttpUrl)

_AUCTION_URL_RE: Final = re.compile(
    r"auctions/(?P<slug>[a-z0-9-]+?)-(?P<auction_id>\d+)(?:$|/)"
)
_LOT_URL_RE: Final = re.compile(
    r"auctions/(?P<slug>[a-z0-9-]+)/(?P<lot_slug>[a-z0-9-]+)-(?P<lot_id>\d+)(?:$|/)"
)
_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b"
)

_DATE_PATTERNS: Final[tuple[str, ...]] = (
    "%A %d %B %Y",
    "%A %d %b %Y",
    "%d %B %Y",
    "%d %b %Y",
)

_STATUS_TOKENS: Final[dict[str, AuctionLotStatus]] = {
    "sold prior": AuctionLotStatus.SOLD_PRIOR,
    "sold": AuctionLotStatus.SOLD,
    "withdrawn": AuctionLotStatus.WITHDRAWN,
    "postponed": AuctionLotStatus.POSTPONED,
    "unsold": AuctionLotStatus.UNSOLD,
    "under offer": AuctionLotStatus.UNDER_OFFER,
}

_TENURE_HINTS: Final[tuple[tuple[str, Tenure], ...]] = (
    ("long leasehold", Tenure.LEASEHOLD),
    ("leasehold", Tenure.LEASEHOLD),
    ("freehold", Tenure.FREEHOLD),
    ("share of freehold", Tenure.SHARE_OF_FREEHOLD),
    ("feuhold", Tenure.FEUHOLD),
)

_PROPERTY_TYPE_HINTS: Final[tuple[tuple[str, PropertyType], ...]] = (
    ("end of terrace", PropertyType.END_OF_TERRACE),
    ("semi-detached", PropertyType.SEMI_DETACHED),
    ("semi detached", PropertyType.SEMI_DETACHED),
    ("detached house", PropertyType.DETACHED),
    ("detached bungalow", PropertyType.BUNGALOW),
    ("terraced house", PropertyType.TERRACED),
    ("terraced", PropertyType.TERRACED),
    ("mid terrace", PropertyType.TERRACED),
    ("three-storey", PropertyType.TERRACED),
    ("apartment", PropertyType.APARTMENT),
    ("maisonette", PropertyType.MAISONETTE),
    ("bungalow", PropertyType.BUNGALOW),
    ("cottage", PropertyType.COTTAGE),
    ("studio", PropertyType.STUDIO),
    ("block of flats", PropertyType.FLAT),
    ("block of apartments", PropertyType.FLAT),
    ("flat", PropertyType.FLAT),
    ("land", PropertyType.LAND),
    ("development site", PropertyType.LAND),
    ("commercial", PropertyType.COMMERCIAL),
    ("retail", PropertyType.COMMERCIAL),
    ("office", PropertyType.COMMERCIAL),
    ("warehouse", PropertyType.COMMERCIAL),
    ("industrial", PropertyType.COMMERCIAL),
    ("mixed use", PropertyType.COMMERCIAL),
    ("investment", PropertyType.COMMERCIAL),
    ("hotel", PropertyType.COMMERCIAL),
)


def parse_catalogue_html(
    html: str,
    *,
    auction_url: str,
    auction_meta: dict[str, Any] | None = None,
) -> list[AuctionLot]:
    """Parse the catalogue HTML into canonical :class:`AuctionLot`."""

    tree = HTMLParser(html)
    parsed: list[AuctionLot] = []

    for node in tree.css("li.lot[id^='lot-']"):
        try:
            lot = _parse_lot_card(node, auction_url=auction_url, auction_meta=auction_meta)
        except ValidationError:
            continue
        if lot is not None:
            parsed.append(lot)
    return parsed


def parse_auction_metadata(html: str, *, auction_url: str) -> dict[str, Any]:
    """Extract catalogue metadata from a Savills auction page."""

    meta: dict[str, Any] = {
        "source": AuctionHouse.SAVILLS_AUCTIONS,
        "auction_url": auction_url,
    }

    m = _AUCTION_URL_RE.search(auction_url)
    if m:
        meta["auction_id"] = m.group("auction_id")
        meta["slug"] = m.group("slug")

    tree = HTMLParser(html)

    title_el = tree.css_first("title")
    if title_el is not None:
        title = (title_el.text() or "").strip()
        meta["title"] = title
        meta["name"] = _extract_title_name(title) or title
        date_val = _parse_title_date(title)
        if date_val is not None:
            meta["date_day1"] = date_val
            meta["reference"] = f"SV-{meta.get('auction_id','?')}-{date_val.isoformat()}"

    lot_nodes = tree.css("li.lot[id^='lot-']")
    meta["lot_count_rendered"] = len(lot_nodes)

    return meta


def parse_upcoming_auctions(html: str) -> list[dict[str, Any]]:
    """Parse ``/upcoming-auctions`` into auction discovery summaries."""

    tree = HTMLParser(html)
    out: list[dict[str, Any]] = []

    for row in tree.css(".upcoming-calendar__row"):
        link = row.css_first("a.upcoming-calendar-content__button")
        if link is None:
            link = row.css_first("a[href*='/auctions/']")
        if link is None:
            continue
        href = (link.attributes.get("href") or "").strip()
        m = _AUCTION_URL_RE.search(href)
        if not m:
            continue

        name_el = row.css_first(".upcoming-calendar-content__auction_name")
        date_el = row.css_first(".upcoming-calendar-content__auction_date")
        venue_el = row.css_first(".upcoming-calendar-content__auction_location")
        props_el = row.css_first(".upcoming-calendar-content__auction_properties")

        summary: dict[str, Any] = {
            "auction_id": m.group("auction_id"),
            "slug": m.group("slug"),
            "href": href,
        }
        if name_el is not None:
            summary["name"] = (name_el.text() or "").strip() or None
        if venue_el is not None:
            summary["venue"] = (venue_el.text() or "").strip() or None

        auction_date: date | None = None
        if date_el is not None:
            auction_date = _parse_savills_calendar_date(date_el.text(strip=True))
        if auction_date is None and name_el is not None:
            auction_date = _parse_title_date(name_el.text(strip=True))
        summary["auction_date"] = auction_date.isoformat() if auction_date else None

        if props_el is not None:
            prop_text = (props_el.text() or "").strip()
            count_match = re.search(r"(\d[\d,]*)", prop_text)
            if count_match:
                summary["lot_count_hint"] = int(count_match.group(1).replace(",", ""))

        out.append(summary)
    return out


def _parse_lot_card(
    node: Node,
    *,
    auction_url: str,
    auction_meta: dict[str, Any] | None,
) -> AuctionLot | None:
    lot_id = (node.attributes.get("data-lot_id") or "").strip()
    if not lot_id:
        node_id = (node.attributes.get("id") or "").strip()
        if node_id.startswith("lot-"):
            lot_id = node_id.removeprefix("lot-")
    if not lot_id:
        return None

    link = node.css_first("a.lot-name")
    if link is None:
        return None
    href = (link.attributes.get("href") or "").strip()
    if not href:
        return None
    lot_match = _LOT_URL_RE.search(href)
    if lot_match is None:
        return None

    title_attr = (link.attributes.get("title") or "").strip()
    address_raw = title_attr or (link.text() or "").strip()
    if not address_raw:
        return None

    source_url = _coerce_url(href if href.startswith("http") else f"{_ORIGIN}{href}")
    if source_url is None:
        return None

    lot_number_attr = None
    img_list = node.css_first("ul.lot-image-list")
    if img_list is not None:
        ln_attr = (img_list.attributes.get("data-lot_number") or "").strip()
        if ln_attr:
            lot_number_attr = ln_attr

    if lot_number_attr is None:
        ln_el = node.css_first("p.lot-number")
        if ln_el is not None:
            m = re.search(r"\d+[A-Za-z]?", (ln_el.text() or "").strip())
            if m:
                lot_number_attr = m.group(0)

    guide_el = node.css_first("p.price-container.guide-price .value")
    guide_text = (guide_el.text() or "").strip() if guide_el is not None else ""
    guide = _parse_guide_text(guide_text)

    status_el = node.css_first("div.lot-status")
    status_text = (status_el.text() or "").strip() if status_el is not None else ""
    status, sold_price_pence = _parse_status_text(status_text)

    details_el = node.css_first("div.lot-details")
    detail_bullets: list[str] = []
    if details_el is not None:
        for li in details_el.css("li"):
            txt = (li.text() or "").strip()
            if txt:
                detail_bullets.append(txt)

    property_type, property_type_raw = _guess_property_type(detail_bullets)
    tenure = _guess_tenure(detail_bullets)
    is_vacant = _guess_vacant(detail_bullets)

    image = _parse_first_image(node)

    address = _build_address(address_raw)
    if address is None:
        return None

    auction_date = None
    catalogue_id = None
    if auction_meta:
        d = auction_meta.get("date_day1")
        if isinstance(d, date):
            auction_date = d
        catalogue_id = auction_meta.get("reference") or auction_meta.get("auction_id")

    sale_method = AuctionSaleMethod.TRADITIONAL
    joined = " ".join(detail_bullets).lower()
    if "conditional" in joined:
        sale_method = AuctionSaleMethod.CONDITIONAL

    try:
        return AuctionLot(
            auction_house=AuctionHouse.SAVILLS_AUCTIONS,
            source_id=lot_id,
            source_url=source_url,
            catalogue_id=catalogue_id,
            lot_number=lot_number_attr,
            auction_date=auction_date,
            sale_method=sale_method,
            status=status,
            sold_price_pence=sold_price_pence,
            guide_price=guide,
            property_type=property_type,
            property_type_raw=property_type_raw,
            tenure=tenure,
            is_vacant_possession=is_vacant,
            address=address,
            title=address_raw,
            summary=detail_bullets[0] if detail_bullets else None,
            description="\n".join(detail_bullets) if detail_bullets else None,
            image_urls=[image] if image is not None else [],
            raw_site_fields={
                "auction_url": auction_url,
                "lot_url": href,
                "guide_text": guide_text,
                "status_text": status_text,
                "lot_slug": lot_match.group("lot_slug"),
            },
        )
    except ValidationError:
        return None


def _parse_guide_text(raw: str) -> AuctionGuidePrice | None:
    text = (raw or "").strip()
    if not text or text.upper() == "TBA":
        return AuctionGuidePrice(raw=text or "TBA", qualifier=PriceQualifier.POA)

    amounts = re.findall(r"£\s*([\d,]+(?:\.\d+)?)(\+|[Kk]|[Mm])?", text)
    if not amounts:
        if re.search(r"no\s+reserve", text, re.IGNORECASE):
            return AuctionGuidePrice(raw=text, qualifier=PriceQualifier.POA)
        return None

    def _to_pence(raw: str, suffix: str) -> int | None:
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            return None
        if suffix.lower() == "k":
            value *= 1_000
        elif suffix.lower() == "m":
            value *= 1_000_000
        return round(value * 100)

    low = _to_pence(amounts[0][0], amounts[0][1])
    high = _to_pence(amounts[1][0], amounts[1][1]) if len(amounts) >= 2 else None

    qualifier = PriceQualifier.GUIDE_PRICE
    lowered = text.lower()
    if any(sfx == "+" for _, sfx in amounts) or "excess" in lowered:
        qualifier = PriceQualifier.OFFERS_IN_EXCESS_OF
    elif "offers over" in lowered:
        qualifier = PriceQualifier.OFFERS_OVER
    elif len(amounts) >= 2:
        qualifier = PriceQualifier.GUIDE_PRICE

    return AuctionGuidePrice(low_pence=low, high_pence=high, qualifier=qualifier, raw=text)


def _parse_status_text(text: str) -> tuple[AuctionLotStatus, int | None]:
    lower = (text or "").lower()
    sold_price: int | None = None
    status = AuctionLotStatus.AVAILABLE
    for token, mapped in _STATUS_TOKENS.items():
        if token in lower:
            status = mapped
            break

    m = re.search(r"£\s*([\d,]+(?:\.\d+)?)([KkMm])?", text or "")
    if m and status in (AuctionLotStatus.SOLD, AuctionLotStatus.SOLD_PRIOR):
        try:
            val = float(m.group(1).replace(",", ""))
        except ValueError:
            val = 0.0
        suffix = (m.group(2) or "").lower()
        if suffix == "k":
            val *= 1_000
        elif suffix == "m":
            val *= 1_000_000
        if val > 0:
            sold_price = round(val * 100)

    return status, sold_price


def _guess_property_type(bullets: Iterable[str]) -> tuple[PropertyType, str | None]:
    for bullet in bullets:
        lower = bullet.lower()
        for needle, mapped in _PROPERTY_TYPE_HINTS:
            if needle in lower:
                return mapped, bullet
    return PropertyType.UNKNOWN, None


def _guess_tenure(bullets: Iterable[str]) -> Tenure:
    for bullet in bullets:
        lower = bullet.lower()
        for needle, mapped in _TENURE_HINTS:
            if needle in lower:
                return mapped
    return Tenure.UNKNOWN


def _guess_vacant(bullets: Iterable[str]) -> bool | None:
    for bullet in bullets:
        lower = bullet.lower()
        if "vacant possession" in lower or lower.strip() == "vacant":
            return True
        if "tenanted" in lower or "sitting tenant" in lower or "investment" in lower:
            return False
    return None


def _parse_first_image(node: Node) -> Image | None:
    img = node.css_first("img")
    if img is None:
        return None
    src = (img.attributes.get("src") or "").strip()
    if not src:
        return None
    try:
        return Image(url=cast("HttpUrl", _URL_ADAPTER.validate_python(src)), caption=None)
    except ValidationError:
        return None


def _build_address(raw: str) -> Address | None:
    raw = raw.strip()
    if not raw:
        return None
    postcode = None
    outcode = None
    m = _POSTCODE_RE.search(raw)
    if m:
        postcode = f"{m.group(1)} {m.group(2)}"
        outcode = m.group(1)
    return Address(raw=raw, postcode=postcode, postcode_outcode=outcode)


def _extract_title_name(title: str) -> str | None:
    """Extract auction name from ``<title>``.

    Example: "Savills Property Auctions | UK & London | 21 & 22 April 2026"
    → "UK & London 21 & 22 April 2026".
    """

    parts = [p.strip() for p in title.split("|")]
    if len(parts) >= 3:
        return " ".join(parts[1:]).strip()
    if len(parts) == 2:
        return parts[1]
    return None


def _parse_title_date(text: str) -> date | None:
    """Parse the first date token from an auction title."""

    if not text:
        return None
    cleaned = text.replace("&amp;", "&")
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", cleaned)
    # \u2013 is the Unicode en-dash Savills uses in date ranges like "12-13".
    tokens = re.search(
        r"(\d{1,2})(?:\s*(?:&|and|-|\u2013|to)\s*\d{1,2})?\s+([A-Za-z]+)\s+(\d{4})",
        cleaned,
    )
    if tokens is None:
        return None
    day_str, month_str, year_str = tokens.groups()
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(
                f"{day_str} {month_str} {year_str}",
                fmt.replace("%A ", "").replace("%a ", ""),
            ).date()
        except ValueError:
            continue
    return None


def _parse_savills_calendar_date(text: str) -> date | None:
    if not text:
        return None
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", text.strip())
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _coerce_url(url: str) -> HttpUrl | None:
    try:
        return cast("HttpUrl", _URL_ADAPTER.validate_python(url))
    except ValidationError:
        return None


__all__ = [
    "parse_auction_metadata",
    "parse_catalogue_html",
    "parse_upcoming_auctions",
]
