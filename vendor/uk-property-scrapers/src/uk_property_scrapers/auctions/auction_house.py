"""Parser for Auction House UK (``www.auctionhouse.co.uk``).

Auction House UK is a federated network of regional auctioneers
(Auction House London, Birmingham, Manchester, etc.) sharing a single
web platform. The site is server-rendered ASP.NET MVC (not an SPA),
so we parse the HTML directly for both catalogue discovery and per-lot
data.

Discovery shape
---------------

``/auction/future-auction-dates`` is a single-page HTML table listing
every regional auction for roughly the next three months. Each row
carries a ``<a href="/{branch}/auction/lots/{auction_id}">`` link and
the human-readable date + time + venue we lift into
:class:`AuctionSummary`.

Catalogue shape
---------------

``/{branch}/auction/lots/{auction_id}`` renders every lot as a
``.lot-search-result`` ``<div>`` containing:

* ``<a href="/{branch}/auction/lot/{lot_id}">`` — lot detail URL.
* ``<div class="... lotbg-{residential|commercial}">Lot N</div>`` —
  lot number + residential/commercial classification.
* ``<div class="... grid-view-guide">*Guide | £200,000+ (plus fees)</div>`` —
  the catalogue guide. Values include "Sold Prior", "Withdrawn",
  "Unsold", etc. in the same node when the lot has been resolved.
* ``<p class="fw-bold blue-text">3 Bed Terraced House</p>`` —
  property-type byline.
* ``<p class="fw-medium blue-text grid-address">...</p>`` —
  address string (typically line 1, town, postcode).

This parser handles the search-results HTML; per-lot detail pages
(tenure, tenancy, income) are a future enrichment step and aren't
required for catalogue-level analysis.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Final, cast

from pydantic import HttpUrl, TypeAdapter, ValidationError
from selectolax.parser import HTMLParser, Node

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

_ORIGIN: Final = "https://www.auctionhouse.co.uk"
_URL_ADAPTER: Final = TypeAdapter(HttpUrl)

_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b"
)

_CATALOGUE_URL_RE: Final = re.compile(
    r"^/(?P<branch>[a-z]+)/auction/lots/(?P<auction_id>\d+)"
)
_LOT_URL_RE: Final = re.compile(
    r"^/(?P<branch>[a-z]+)/auction/lot/(?P<lot_id>\d+)"
)
_DATE_HEADER_RE: Final = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})"
)
_FUTURE_DATE_RE: Final = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

_STATUS_TEXT_MAP: Final[dict[str, AuctionLotStatus]] = {
    "sold prior": AuctionLotStatus.SOLD_PRIOR,
    "sold pre": AuctionLotStatus.SOLD_PRIOR,
    "sold post": AuctionLotStatus.SOLD,
    "sold": AuctionLotStatus.SOLD,
    "withdrawn": AuctionLotStatus.WITHDRAWN,
    "unsold": AuctionLotStatus.UNSOLD,
    "under offer": AuctionLotStatus.UNDER_OFFER,
    "postponed": AuctionLotStatus.POSTPONED,
    "for sale by auction": AuctionLotStatus.AVAILABLE,
}

_PROPERTY_TYPE_HINTS: Final[tuple[tuple[str, PropertyType], ...]] = (
    ("end of terrace", PropertyType.END_OF_TERRACE),
    ("end-of-terrace", PropertyType.END_OF_TERRACE),
    ("semi-detached", PropertyType.SEMI_DETACHED),
    ("semi detached", PropertyType.SEMI_DETACHED),
    ("detached house", PropertyType.DETACHED),
    ("detached bungalow", PropertyType.BUNGALOW),
    ("mid terrace", PropertyType.TERRACED),
    ("terraced house", PropertyType.TERRACED),
    ("terraced", PropertyType.TERRACED),
    ("apartment", PropertyType.APARTMENT),
    ("maisonette", PropertyType.MAISONETTE),
    ("bungalow", PropertyType.BUNGALOW),
    ("cottage", PropertyType.COTTAGE),
    ("studio", PropertyType.STUDIO),
    ("land", PropertyType.LAND),
    ("block of apartments", PropertyType.FLAT),
    ("block of flats", PropertyType.FLAT),
    ("flat", PropertyType.FLAT),
    ("commercial", PropertyType.COMMERCIAL),
    ("retail", PropertyType.COMMERCIAL),
    ("office", PropertyType.COMMERCIAL),
    ("warehouse", PropertyType.COMMERCIAL),
    ("industrial", PropertyType.COMMERCIAL),
    ("mixed use", PropertyType.COMMERCIAL),
    ("hotel", PropertyType.COMMERCIAL),
)


def parse_catalogue_html(
    html: str,
    *,
    auction_url: str,
    auction_meta: dict[str, Any] | None = None,
) -> list[AuctionLot]:
    """Parse one catalogue page into canonical :class:`AuctionLot` rows.

    ``auction_url`` is the page URL (``https://www.auctionhouse.co.uk/london/auction/lots/9232``)
    — it anchors relative lot URLs and lets us tag each ``AuctionLot``
    with the correct ``catalogue_id`` when ``auction_meta`` is absent.
    """

    tree = HTMLParser(html)
    parsed: list[AuctionLot] = []

    for node in tree.css(".lot-search-result"):
        try:
            lot = _parse_lot_card(node, auction_url=auction_url, auction_meta=auction_meta)
        except ValidationError:
            continue
        if lot is not None:
            parsed.append(lot)
    return parsed


def parse_auction_metadata(html: str, *, auction_url: str) -> dict[str, Any]:
    """Extract catalogue-level metadata (auction_id, date, venue, name).

    We pull the auction date from the catalogue ``<h1>`` because that's
    the most reliable source — the ``<title>`` contains similar info
    but occasionally lags the actual sale date.
    """

    meta: dict[str, Any] = {
        "source": AuctionHouse.AUCTION_HOUSE_UK,
        "auction_url": auction_url,
    }

    m = _CATALOGUE_URL_RE.search(auction_url.replace(_ORIGIN, ""))
    if m:
        meta["auction_id"] = m.group("auction_id")
        meta["branch"] = m.group("branch")

    tree = HTMLParser(html)

    title_el = tree.css_first("title")
    if title_el is not None:
        title = (title_el.text() or "").strip()
        meta["title"] = title
        date_val = _parse_title_date(title)
        if date_val is not None:
            meta["date_day1"] = date_val
        branch_name = _parse_title_branch(title)
        if branch_name:
            meta["name"] = branch_name

    if "date_day1" not in meta:
        h1 = tree.css_first("h1")
        if h1 is not None:
            date_val = _parse_header_date(h1.text() or "")
            if date_val is not None:
                meta["date_day1"] = date_val

    if "date_day1" in meta:
        meta["reference"] = f"AH-{meta.get('branch','?')}-{meta.get('auction_id','?')}"

    return meta


def parse_future_auctions(
    html: str,
) -> list[dict[str, Any]]:
    """Parse ``/auction/future-auction-dates`` into auction summaries.

    Returns a list of dicts with ``auction_id``, ``branch``, ``href``,
    ``name`` (e.g. "Auction House London"), ``auction_date`` (``date``
    or None), and ``venue`` (e.g. "Live Stream"). The Apify actor's
    discovery phase converts these into :class:`AuctionSummary`
    objects.
    """

    tree = HTMLParser(html)
    summaries: list[dict[str, Any]] = []
    for link in tree.css("a[href*='/auction/lots/']"):
        href = (link.attributes.get("href") or "").strip()
        m = _CATALOGUE_URL_RE.search(href)
        if not m:
            continue
        row = _nearest_ancestor(link, "tr")
        if row is None:
            continue
        cells = [c.text(strip=True) for c in row.css("td")]
        name, venue, auction_date = _extract_row_fields(cells)
        summaries.append(
            {
                "auction_id": m.group("auction_id"),
                "branch": m.group("branch"),
                "href": href,
                "name": name,
                "venue": venue,
                "auction_date": auction_date.isoformat() if auction_date else None,
            }
        )

    seen: set[str] = set()
    dedup: list[dict[str, Any]] = []
    for s in summaries:
        aid = s["auction_id"]
        if aid in seen:
            continue
        seen.add(aid)
        dedup.append(s)
    return dedup


def _parse_lot_card(
    node: Node,
    *,
    auction_url: str,
    auction_meta: dict[str, Any] | None,
) -> AuctionLot | None:
    link_node = node.css_first("a.home-lot-wrapper-link") or node.css_first("a[href*='/auction/lot/']")
    if link_node is None:
        return None
    href = (link_node.attributes.get("href") or "").strip()
    lot_match = _LOT_URL_RE.search(href)
    if not lot_match:
        return None
    lot_id = lot_match.group("lot_id")
    branch = lot_match.group("branch")
    source_url = _coerce_url(f"{_ORIGIN}{href}")
    if source_url is None:
        return None

    sticker_node = node.css_first(".image-sticker")
    lot_number, lot_type = _parse_lot_sticker(sticker_node)

    guide_node = node.css_first(".grid-view-guide")
    guide_text = guide_node.text(strip=True) if guide_node is not None else ""
    status, guide = _parse_guide_or_status(guide_text)

    summary_root = node.css_first(".summary-info-wrapper")
    byline = address_raw = ""
    if summary_root is not None:
        ps = summary_root.css("p")
        if ps:
            byline = (ps[0].text() or "").strip()
        if len(ps) > 1:
            address_raw = (ps[1].text() or "").strip()

    if not address_raw:
        return None

    address = _build_address(address_raw)
    if address is None:
        return None

    property_type = _guess_property_type(byline)
    image = _parse_image(node)

    catalogue_id = None
    auction_date = None
    if auction_meta:
        catalogue_id = auction_meta.get("reference") or auction_meta.get("auction_id")
        d = auction_meta.get("date_day1")
        if isinstance(d, date):
            auction_date = d

    try:
        return AuctionLot(
            auction_house=AuctionHouse.AUCTION_HOUSE_UK,
            source_id=lot_id,
            source_url=source_url,
            catalogue_id=catalogue_id,
            lot_number=lot_number,
            auction_date=auction_date,
            sale_method=AuctionSaleMethod.TRADITIONAL,
            status=status,
            guide_price=guide,
            property_type=property_type,
            property_type_raw=byline or None,
            tenure=Tenure.UNKNOWN,
            address=address,
            title=byline or None,
            summary=byline or None,
            image_urls=[image] if image is not None else [],
            raw_site_fields={
                "branch": branch,
                "auction_url": auction_url,
                "sticker_text": (sticker_node.text(strip=True) if sticker_node is not None else ""),
                "guide_text": guide_text,
                "lot_type_hint": lot_type or "",
            },
        )
    except ValidationError:
        return None


def _parse_lot_sticker(sticker: Node | None) -> tuple[str | None, str | None]:
    """Return (``lot_number``, ``lot_type``) from the sticker div."""

    if sticker is None:
        return None, None
    text = sticker.text(strip=True)
    lot_type: str | None = None
    class_attr = " ".join(sticker.attributes.get("class", "").split())
    for token in class_attr.split():
        if token.startswith("lotbg-"):
            lot_type = token.removeprefix("lotbg-")
            break

    lot_number: str | None = None
    m = re.search(r"lot\s*([\w-]+)", text, re.IGNORECASE)
    if m:
        raw = m.group(1).strip("-")
        # Accept any non-empty token except bare dash / en-dash placeholders
        # the portal uses when a lot number is TBC.
        if raw and raw not in {"-", "\u2013"}:
            lot_number = raw
    return lot_number, lot_type


def _parse_guide_or_status(
    text: str,
) -> tuple[AuctionLotStatus, AuctionGuidePrice | None]:
    """Decode the hybrid guide/status text found in ``.grid-view-guide``.

    The same HTML slot holds either a guide ("*Guide | £200,000+ (plus fees)")
    or a post-auction status ("Sold", "Sold Prior", "Withdrawn"). We
    prefer status when the text exactly matches one of the known
    tokens, otherwise we decode as a guide.
    """

    normalised = " ".join((text or "").split()).strip()
    lower = normalised.lower()

    for token, status in _STATUS_TEXT_MAP.items():
        if lower.startswith(token) or lower == token:
            return status, None

    if "£" not in normalised:
        return AuctionLotStatus.AVAILABLE, None

    guide = _parse_guide_price_text(normalised)
    return AuctionLotStatus.AVAILABLE, guide


def _parse_guide_price_text(text: str) -> AuctionGuidePrice | None:
    """Parse "*Guide | £200,000+ (plus fees)" and variants into AuctionGuidePrice."""

    amounts = re.findall(r"£\s*([\d,]+(?:\.\d+)?)(\+|[Kk]|[Mm])?", text)
    if not amounts:
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

    low_pence = _to_pence(amounts[0][0], amounts[0][1])
    high_pence = (
        _to_pence(amounts[1][0], amounts[1][1]) if len(amounts) >= 2 else None
    )

    qualifier = PriceQualifier.GUIDE_PRICE
    if any(suffix == "+" for _, suffix in amounts):
        qualifier = PriceQualifier.OFFERS_IN_EXCESS_OF
    elif len(amounts) >= 2:
        qualifier = PriceQualifier.GUIDE_PRICE
    elif re.search(r"offers?\s+in\s+excess", text, re.IGNORECASE):
        qualifier = PriceQualifier.OFFERS_IN_EXCESS_OF
    elif re.search(r"offers?\s+over", text, re.IGNORECASE):
        qualifier = PriceQualifier.OFFERS_OVER

    return AuctionGuidePrice(
        low_pence=low_pence,
        high_pence=high_pence,
        qualifier=qualifier,
        raw=text,
    )


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


def _guess_property_type(byline: str) -> PropertyType:
    lower = (byline or "").lower()
    for needle, mapped in _PROPERTY_TYPE_HINTS:
        if needle in lower:
            return mapped
    return PropertyType.UNKNOWN


def _parse_image(node: Node) -> Image | None:
    img = node.css_first("img.lot-image")
    if img is None:
        return None
    src = (img.attributes.get("src") or "").strip()
    if not src:
        return None
    if src.startswith("/"):
        src = f"{_ORIGIN}{src}"
    try:
        return Image(url=cast("HttpUrl", _URL_ADAPTER.validate_python(src)), caption=None)
    except ValidationError:
        return None


def _nearest_ancestor(node: Node, tag: str) -> Node | None:
    current = node.parent
    while current is not None:
        if (current.tag or "").lower() == tag.lower():
            return current
        current = current.parent
    return None


def _extract_row_fields(cells: list[str]) -> tuple[str | None, str | None, date | None]:
    name: str | None = None
    venue: str | None = None
    parsed_date: date | None = None

    for cell in cells:
        lower = cell.lower()
        if "auction house" in lower and name is None:
            name = cell
        elif any(tok in lower for tok in ("stream", "ballroom", "room", "hotel", "online")):
            if venue is None:
                venue = cell
        else:
            if parsed_date is None:
                parsed_date = _parse_row_date(cell)
    return name, venue, parsed_date


def _parse_title_date(title: str) -> date | None:
    m = _DATE_HEADER_RE.search(title)
    if not m:
        return None
    day = int(m.group(1))
    month_name = m.group(2)
    year = int(m.group(3))
    try:
        return datetime.strptime(f"{day} {month_name} {year}", "%d %B %Y").date()
    except ValueError:
        try:
            return datetime.strptime(f"{day} {month_name} {year}", "%d %b %Y").date()
        except ValueError:
            return None


def _parse_title_branch(title: str) -> str | None:
    m = re.search(r"Auction in\s+([A-Z][A-Za-z '&-]+?)\s+on", title)
    if m:
        return f"Auction House {m.group(1).strip()}"
    return None


def _parse_header_date(text: str) -> date | None:
    cleaned = re.sub(r"[A-Za-z]+day\s+", "", text).strip()
    return _parse_title_date(cleaned)


def _parse_row_date(text: str) -> date | None:
    m = _FUTURE_DATE_RE.search(text)
    if not m:
        return _parse_title_date(text)
    day, month, year = map(int, m.groups())
    try:
        return date(year=year, month=month, day=day)
    except ValueError:
        return None


def _coerce_url(url: str) -> HttpUrl | None:
    try:
        return cast("HttpUrl", _URL_ADAPTER.validate_python(url))
    except ValidationError:
        return None


__all__ = [
    "parse_auction_metadata",
    "parse_catalogue_html",
    "parse_future_auctions",
]
