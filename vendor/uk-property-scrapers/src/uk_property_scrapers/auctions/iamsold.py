"""Parser for iamsold (``www.iamsold.co.uk``).

iamsold is the UK's largest *Modern Method of Auction* platform. Its
model is quite different from traditional auctioneers like Allsop or
Savills:

* There's no single ballroom sale date — each property has its own
  30-day marketing window with a rolling close-of-bidding timestamp.
* The "auction" surface is therefore the live feed of available
  properties rather than a dated catalogue.
* Sale method is usually Modern (56 days to complete) with some
  Traditional lots mixed in. We surface both explicitly.

Discovery shape
---------------

``/available-properties/`` (aliased from ``/properties/``) is an HTML
page rendering every currently-marketed property as a
``.c__property.c__propertyAlt`` card with:

* ``<div class="c__tease c__property c__propertyAlt" id="property-{UUID}">`` —
  the outer wrapper carrying the stable property UUID.
* ``<div class="c__property__img" data-bkimage="{URL}">`` — thumbnail.
* ``<div class="c__property__status c__property__status--{state}">{label}</div>`` —
  lifecycle state (``Pre-auction Marketing``, ``Sold``, ``Withdrawn``…).
* ``<ul class="c__property__tags">`` containing ``<li class="modern">Modern Method</li>``
  and ``<li class="tenure">Freehold</li>``.
* ``<h3><a href="/property/{UUID}/">3 bed Terraced</a></h3>`` — the
  byline with bed count + property type.
* ``<p>Street, <span>Town</span>, County, POSTCODE</p>`` — address.
* ``<li class="priceGuide" data-currency="£">Starting bid: <span class="current_price">£240,000</span></li>`` —
  starting bid / guide. Label reads ``Starting bid:`` for MMoA lots and
  ``Guide price:`` for traditional.

The per-property auction close timestamp isn't reliably in the HTML
(the countdown is hydrated client-side), so
:class:`~uk_property_scrapers.AuctionLot.auction_end_at` is left
``None`` at the catalogue level.

Catalogue shape
---------------

Because there's no single catalogue date, we expose a synthetic
"IAS-LIVE-{iso-date}" reference so downstream dedup tooling still has
a catalogue key. Per-property detail pages could enrich completion
window and reserve price, but they're not required for index-level
analysis and are a future step.
"""

from __future__ import annotations

import re
from datetime import date
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

_ORIGIN: Final = "https://www.iamsold.co.uk"
_URL_ADAPTER: Final = TypeAdapter(HttpUrl)

_PROPERTY_URL_RE: Final = re.compile(
    r"^/property/(?P<uuid>[0-9a-f]{32})/"
)
_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b"
)
_BEDROOM_RE: Final = re.compile(
    r"^\s*(?P<beds>\d+)\s*bed(?:room)?s?\s+(?P<kind>[A-Za-z \-]+?)\s*$",
    re.IGNORECASE,
)

_STATUS_LABEL_MAP: Final[dict[str, AuctionLotStatus]] = {
    "pre-auction marketing": AuctionLotStatus.AVAILABLE,
    "pre auction marketing": AuctionLotStatus.AVAILABLE,
    "auction marketing": AuctionLotStatus.AVAILABLE,
    "available": AuctionLotStatus.AVAILABLE,
    "for sale": AuctionLotStatus.AVAILABLE,
    "sold": AuctionLotStatus.SOLD,
    "sold prior": AuctionLotStatus.SOLD_PRIOR,
    "sold stc": AuctionLotStatus.SOLD,
    "under offer": AuctionLotStatus.UNDER_OFFER,
    "withdrawn": AuctionLotStatus.WITHDRAWN,
    "unsold": AuctionLotStatus.UNSOLD,
    "ending soon": AuctionLotStatus.AVAILABLE,
    "auction ends": AuctionLotStatus.AVAILABLE,
}

_PROPERTY_TYPE_MAP: Final[dict[str, PropertyType]] = {
    "terraced": PropertyType.TERRACED,
    "end of terrace": PropertyType.END_OF_TERRACE,
    "end-of-terrace": PropertyType.END_OF_TERRACE,
    "semi-detached": PropertyType.SEMI_DETACHED,
    "semi detached": PropertyType.SEMI_DETACHED,
    "detached": PropertyType.DETACHED,
    "flat": PropertyType.FLAT,
    "apartment": PropertyType.APARTMENT,
    "maisonette": PropertyType.MAISONETTE,
    "bungalow": PropertyType.BUNGALOW,
    "cottage": PropertyType.COTTAGE,
    "studio": PropertyType.STUDIO,
    "park home": PropertyType.PARK_HOME,
    "land": PropertyType.LAND,
    "commercial": PropertyType.COMMERCIAL,
}


def parse_available_properties(
    html: str,
    *,
    list_url: str,
) -> list[AuctionLot]:
    """Parse ``/available-properties/`` HTML into ``AuctionLot`` rows."""

    tree = HTMLParser(html)
    parsed: list[AuctionLot] = []

    for node in tree.css(".c__tease.c__property"):
        try:
            lot = _parse_card(node, list_url=list_url)
        except ValidationError:
            continue
        if lot is not None:
            parsed.append(lot)
    return parsed


def build_synthetic_auction_meta(*, list_url: str) -> dict[str, Any]:
    """Return a catalogue-metadata envelope for the single iamsold feed.

    iamsold doesn't have dated catalogues so the actor treats the
    whole feed as one rolling "auction". The reference field encodes
    the snapshot date to keep downstream dedup deterministic.
    """

    today = date.today().isoformat()
    return {
        "source": AuctionHouse.IAMSOLD,
        "auction_url": list_url,
        "auction_id": "live",
        "name": "iamsold Available Properties",
        "reference": f"IAS-LIVE-{today}",
        "date_day1": date.today(),
    }


def _parse_card(node: Node, *, list_url: str) -> AuctionLot | None:
    node_id = (node.attributes.get("id") or "").strip()
    uuid = node_id.removeprefix("property-")
    if not uuid:
        return None

    link = node.css_first("a[href^='/property/']")
    if link is None:
        return None
    href = (link.attributes.get("href") or "").strip()
    m = _PROPERTY_URL_RE.match(href)
    if m is None:
        return None
    if m.group("uuid") != uuid:
        uuid = m.group("uuid")

    source_url = _coerce_url(f"{_ORIGIN}{href}")
    if source_url is None:
        return None

    address_wrap = node.css_first("div.c__property__address")
    byline = ""
    address_raw = ""
    if address_wrap is not None:
        h3 = address_wrap.css_first("h3")
        if h3 is not None:
            byline = " ".join((h3.text() or "").split())
        p_el = address_wrap.css_first("p")
        if p_el is not None:
            address_raw = " ".join((p_el.text() or "").split())

    if not address_raw:
        return None
    address = _build_address(address_raw)
    if address is None:
        return None

    bedrooms, property_type, property_type_raw = _parse_byline(byline)

    guide, guide_text = _parse_price(node)
    status, status_text = _parse_status(node)
    sale_method, tenure = _parse_tags(node.css("ul.c__property__tags li"))

    image = _parse_image(node)

    try:
        return AuctionLot(
            auction_house=AuctionHouse.IAMSOLD,
            source_id=uuid,
            source_url=source_url,
            catalogue_id=f"IAS-LIVE-{date.today().isoformat()}",
            lot_number=None,
            auction_date=None,
            sale_method=sale_method,
            status=status,
            guide_price=guide,
            property_type=property_type,
            property_type_raw=property_type_raw,
            tenure=tenure,
            bedrooms=bedrooms,
            address=address,
            title=byline or None,
            summary=byline or None,
            image_urls=[image] if image is not None else [],
            raw_site_fields={
                "list_url": list_url,
                "status_text": status_text,
                "guide_text": guide_text,
                "byline": byline,
            },
        )
    except ValidationError:
        return None


def _parse_byline(byline: str) -> tuple[int | None, PropertyType, str | None]:
    byline = (byline or "").strip()
    if not byline:
        return None, PropertyType.UNKNOWN, None

    m = _BEDROOM_RE.match(byline)
    bedrooms = None
    kind = byline
    if m:
        try:
            bedrooms = int(m.group("beds"))
        except ValueError:
            bedrooms = None
        kind = m.group("kind").strip()

    property_type = PropertyType.UNKNOWN
    lower = kind.lower()
    for needle, mapped in _PROPERTY_TYPE_MAP.items():
        if needle in lower:
            property_type = mapped
            break
    return bedrooms, property_type, byline or None


def _parse_price(node: Node) -> tuple[AuctionGuidePrice | None, str]:
    li = node.css_first("li.priceGuide") or node.css_first("li.c__property__price")
    if li is None:
        return None, ""

    label = (li.text() or "").strip()
    value_el = li.css_first("span.current_price") or li.css_first(".current_price")
    value = (value_el.text() or "").strip() if value_el is not None else ""
    full = f"{label}".strip()

    amounts = re.findall(r"£\s*([\d,]+(?:\.\d+)?)", value or full)
    if not amounts:
        return None, full

    try:
        low_pence = round(float(amounts[0].replace(",", "")) * 100)
    except ValueError:
        return None, full

    high_pence = None
    if len(amounts) >= 2:
        try:
            high_pence = round(float(amounts[1].replace(",", "")) * 100)
        except ValueError:
            high_pence = None

    qualifier = PriceQualifier.GUIDE_PRICE
    if re.search(r"starting\s+bid", label, re.IGNORECASE):
        qualifier = PriceQualifier.OFFERS_OVER

    return (
        AuctionGuidePrice(
            low_pence=low_pence,
            high_pence=high_pence,
            qualifier=qualifier,
            raw=full,
        ),
        full,
    )


def _parse_status(node: Node) -> tuple[AuctionLotStatus, str]:
    status_el = node.css_first(".c__property__status")
    if status_el is None:
        return AuctionLotStatus.AVAILABLE, ""
    text = (status_el.text() or "").strip()
    lower = text.lower()
    for key, mapped in _STATUS_LABEL_MAP.items():
        if key in lower:
            return mapped, text
    return AuctionLotStatus.UNKNOWN, text


def _parse_tags(items: Iterable[Node]) -> tuple[AuctionSaleMethod, Tenure]:
    sale = AuctionSaleMethod.UNKNOWN
    tenure = Tenure.UNKNOWN
    for item in items:
        class_attr = (item.attributes.get("class") or "").lower()
        text = (item.text() or "").strip().lower()
        if "modern" in class_attr or "modern method" in text:
            sale = AuctionSaleMethod.MODERN
        elif "traditional" in class_attr or "traditional" in text:
            sale = AuctionSaleMethod.TRADITIONAL
        elif "online" in text:
            sale = AuctionSaleMethod.ONLINE_TIMED

        if "tenure" in class_attr:
            if "freehold" in text:
                tenure = Tenure.FREEHOLD
            elif "leasehold" in text:
                tenure = Tenure.LEASEHOLD
            elif "share of freehold" in text:
                tenure = Tenure.SHARE_OF_FREEHOLD
    return sale, tenure


def _parse_image(node: Node) -> Image | None:
    img_div = node.css_first(".c__property__img[data-bkimage]")
    if img_div is not None:
        src = (img_div.attributes.get("data-bkimage") or "").strip()
    else:
        img = node.css_first("img")
        src = (img.attributes.get("src") or "").strip() if img is not None else ""
    if not src:
        return None
    try:
        return Image(url=cast("HttpUrl", _URL_ADAPTER.validate_python(src)), caption=None)
    except ValidationError:
        return None


def _build_address(raw: str) -> Address | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    postcode = None
    outcode = None
    m = _POSTCODE_RE.search(raw)
    if m:
        postcode = f"{m.group(1)} {m.group(2)}"
        outcode = m.group(1)
    return Address(raw=raw, postcode=postcode, postcode_outcode=outcode)


def _coerce_url(url: str) -> HttpUrl | None:
    try:
        return cast("HttpUrl", _URL_ADAPTER.validate_python(url))
    except ValidationError:
        return None


__all__ = [
    "build_synthetic_auction_meta",
    "parse_available_properties",
]
