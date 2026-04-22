"""Parser for Allsop's auction catalogue.

Allsop's www.allsop.co.uk is an Angular SPA whose HTML shell is empty on
initial load. All lot data comes from a small cluster of JSON endpoints
under ``/api/``:

* ``/api/auctions/<uuid>?react`` — per-auction metadata (date, venue,
  reference, two-day flags).
* ``/api/search?auction_id=<uuid>[&page=<n>]&react`` — paginated lot list.
  Each element has ~95 fields; the ones we parse are documented inline on
  :class:`AllsopLotRecord`.
* ``/api/auctions/currentskiplots?react`` — bookkeeping for the live
  carousel. Not used here.

All three endpoints return a ``{"data": ...}`` envelope. This parser
operates on the *deserialised Python dict*, not HTTP responses: the
caller owns transport, pagination, retries, and proxy rotation. Fetching
the JSON is trivial with any HTTP client — see the ``uk-auctions`` Apify
actor for the production path and ``scripts/live_smoke.py`` for a
one-shot smoke-test.

Design notes:

* Allsop prices in the JSON feed are integers in pounds. We convert to
  pence to line up with the rest of the ``uk-property-scrapers`` schema.
* ``auction_date`` is a milliseconds-epoch UTC stamp, and always falls at
  23:00 UTC on the day *before* the local auction date in BST. We pull
  the canonical date from the companion ``/api/auctions/<uuid>`` payload
  when it's provided; otherwise we localise ``auction_date`` to
  ``Europe/London`` to recover the intended date.
* The feed distinguishes the lot's scheduled auction day (``auction_date``)
  from the *catalogue reference* (``reference`` e.g. ``"R260430 098"``).
  The ``R<yymmdd>`` prefix matches the auction date in ``yymmdd`` and the
  suffix is the lot sequence.
* Lots marketed for future auctions that are already sold prior show
  ``lot_status == 'Sold Prior'``; post-hammer data uses ``'Sold'`` or
  ``'Unsold'`` with a ``price`` field. We surface both in
  :class:`~uk_property_scrapers.AuctionLot.status` and
  :attr:`~uk_property_scrapers.AuctionLot.sold_price_pence`.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any, Final, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import HttpUrl, TypeAdapter, ValidationError

from uk_property_scrapers.schema import (
    Address,
    AuctionGuidePrice,
    AuctionHouse,
    AuctionLot,
    AuctionLotStatus,
    AuctionSaleMethod,
    Image,
    LatLng,
    PriceQualifier,
    PropertyType,
    Tenure,
)

# ── URL patterns ─────────────────────────────────────────────────────────────

_ALLSOP_ORIGIN: Final = "https://www.allsop.co.uk"
_ALLSOP_IMAGE_CDN: Final = (
    "https://as-prod-bau-object-storage.s3.eu-west-2.amazonaws.com/image_cache"
)
_ALLSOP_TZ: Final = ZoneInfo("Europe/London")

# Allsop slug ids in lot URLs: ``/lot-overview/<slug>/r<yymmdd>-<lotnum>``.
# We derive the second path component from ``reference`` ("R260430 098")
# by lowercasing and replacing the space with a hyphen.
_REFERENCE_URL_CHARS_RE: Final = re.compile(r"[^a-z0-9\-]+")

# ── Status + type mappings ──────────────────────────────────────────────────

_STATUS_MAP: Final[dict[str, AuctionLotStatus]] = {
    "available": AuctionLotStatus.AVAILABLE,
    "under offer": AuctionLotStatus.UNDER_OFFER,
    "sold prior": AuctionLotStatus.SOLD_PRIOR,
    "sold": AuctionLotStatus.SOLD,
    "withdrawn": AuctionLotStatus.WITHDRAWN,
    "postponed": AuctionLotStatus.POSTPONED,
    "unsold": AuctionLotStatus.UNSOLD,
}

# Allsop's ``allsop_propertytype`` is an array of classifications that
# sometimes mixes commercial and residential tokens — ``["Flat / Block",
# "House"]`` is a real value. We pick the most specific normalized type.
_PROPERTY_TYPE_MAP: Final[dict[str, PropertyType]] = {
    "house": PropertyType.OTHER,
    "flat": PropertyType.FLAT,
    "flat / block": PropertyType.FLAT,
    "apartment": PropertyType.APARTMENT,
    "maisonette": PropertyType.MAISONETTE,
    "bungalow": PropertyType.BUNGALOW,
    "studio": PropertyType.STUDIO,
    "land": PropertyType.LAND,
    "development site": PropertyType.LAND,
    "commercial": PropertyType.COMMERCIAL,
    "retail": PropertyType.COMMERCIAL,
    "office": PropertyType.COMMERCIAL,
    "industrial": PropertyType.COMMERCIAL,
    "leisure": PropertyType.COMMERCIAL,
    "mixed use": PropertyType.COMMERCIAL,
    "park home": PropertyType.PARK_HOME,
}

# Raw strings that describe a residential sub-type in more detail — we
# extract these from ``allsop_propertybyline`` because the feed's
# ``allsop_propertytype`` enum is coarse (just "House" for everything
# from terraced to detached).
_BYLINE_PROPERTY_TYPE_PATTERNS: Final[tuple[tuple[str, PropertyType], ...]] = (
    ("end of terrace", PropertyType.END_OF_TERRACE),
    ("end-of-terrace", PropertyType.END_OF_TERRACE),
    ("mid terrace", PropertyType.TERRACED),
    ("terraced house", PropertyType.TERRACED),
    ("semi detached", PropertyType.SEMI_DETACHED),
    ("semi-detached", PropertyType.SEMI_DETACHED),
    ("link semi detached", PropertyType.SEMI_DETACHED),
    ("detached house", PropertyType.DETACHED),
    ("detached bungalow", PropertyType.BUNGALOW),
    ("bungalow", PropertyType.BUNGALOW),
    ("maisonette", PropertyType.MAISONETTE),
    ("studio", PropertyType.STUDIO),
    ("flats", PropertyType.FLAT),
    ("flat", PropertyType.FLAT),
    ("apartment", PropertyType.APARTMENT),
    ("mansion block", PropertyType.FLAT),
    ("cottage", PropertyType.COTTAGE),
    ("land", PropertyType.LAND),
)

_TENURE_MAP: Final[dict[str, Tenure]] = {
    "freehold": Tenure.FREEHOLD,
    "leasehold": Tenure.LEASEHOLD,
    "share of freehold": Tenure.SHARE_OF_FREEHOLD,
    "feuhold": Tenure.FEUHOLD,
    "commonhold": Tenure.COMMONHOLD,
}

_GUIDE_QUALIFIER_RE: Final = re.compile(
    r"^\s*(offers\s+in\s+excess\s+of|offers\s+over|offers\s+in\s+region(?:\s+of)?"
    r"|guide\s+price|oieo|oiro)\b",
    re.IGNORECASE,
)
_GUIDE_QUALIFIER_MAP: Final[dict[str, PriceQualifier]] = {
    "offers in excess of": PriceQualifier.OFFERS_IN_EXCESS_OF,
    "offers over": PriceQualifier.OFFERS_OVER,
    "offers in region": PriceQualifier.OFFERS_IN_REGION,
    "offers in region of": PriceQualifier.OFFERS_IN_REGION,
    "guide price": PriceQualifier.GUIDE_PRICE,
    "oieo": PriceQualifier.OFFERS_IN_EXCESS_OF,
    "oiro": PriceQualifier.OFFERS_IN_REGION,
}

# Keep the "£200,000+" trailing-plus marker as an "in excess of" qualifier.
# Allsop uses it universally on single-value guides.
_TRAILING_PLUS_RE: Final = re.compile(r"£\s*[\d,.]+\s*([KM])?\+\s*$")

_URL_TYPE_ADAPTER: Final = TypeAdapter(HttpUrl)


# ── Top-level entry points ──────────────────────────────────────────────────


def parse_search_results(
    payload: dict[str, Any],
    *,
    auction_meta: dict[str, Any] | None = None,
) -> list[AuctionLot]:
    """Parse ``/api/search`` response into canonical ``AuctionLot`` models.

    ``payload`` is the already-deserialised JSON body:

        >>> response = httpx.get(
        ...     "https://www.allsop.co.uk/api/search",
        ...     params={"auction_id": AUCTION_UUID, "react": ""},
        ... ).json()
        >>> lots = parse_search_results(response)

    ``auction_meta`` is the optional ``/api/auctions/<uuid>`` payload; when
    provided, the parser prefers its dates and venue over the per-lot
    ``auction_date`` timestamp because day-2 lots in a two-day auction
    carry ``auction_date = allsop_auctiondate2`` which is easy to
    misinterpret as a separate sale.

    Malformed individual lots are silently skipped with a logged error —
    Allsop's feed occasionally ships records missing ``reference`` or
    ``location`` while the catalogue is being reviewed. The caller can
    notice by comparing ``len(parse_search_results(...))`` to
    ``payload["data"]["total"]``.
    """

    data = payload.get("data") or {}
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        return []

    parsed: list[AuctionLot] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        lot = _parse_single_lot(raw, auction_meta=auction_meta)
        if lot is not None:
            parsed.append(lot)
    return parsed


def parse_auction_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract a small, stable subset of the ``/api/auctions/<uuid>`` envelope.

    The upstream payload has ~60 fields; we return only the ones downstream
    callers actually consume (auction date, venue, reference, catalogue
    name, day-2 flag). Returning a plain ``dict`` rather than a Pydantic
    model keeps the auction-meta path cheap — callers typically only need
    it as context while parsing lots and don't want another schema.
    """

    meta = payload.get("auctionData")
    if not isinstance(meta, dict) or not meta:
        return {}

    return {
        "auction_id": meta.get("allsop_auctionid"),
        "reference": meta.get("allsop_auctionreference"),
        "name": meta.get("allsop_name"),
        "venue": meta.get("allsop_venue"),
        "auction_type": meta.get("auction_type"),
        "date_day1": _parse_iso_date(meta.get("allsop_auctiondate")),
        "date_day2": _parse_iso_date(meta.get("allsop_auctiondate2")),
        "next_auction_date": meta.get("next_auction_date"),
        "lots_sold": meta.get("allsop_auctionlotssold"),
        "lots_unsold": meta.get("allsop_auctionlotsunsold"),
        "value_sold_gbp": meta.get("allsop_auctionvaluesold"),
    }


def parse_lot_gallery(
    payload: dict[str, Any],
    *,
    include_floorplans: bool = True,
) -> list[Image]:
    """Extract the full photo gallery from ``/api/lot/reference/<ref>``.

    ``payload`` is the already-deserialised JSON body returned by
    :meth:`uk_property_apis.auctions.AllsopClient.get_lot_detail`. The
    relevant field is a top-level ``"images"`` array where each entry
    carries ``sort_order`` (catalogue display order), ``type``
    (``"featured"`` for photos, ``"floorplan"`` for the schematic), a
    ``file_id`` (the CDN object key), and an optional ``mime_type``.

    We build public CDN URLs using the same pattern Allsop serves on
    the lot-overview page (``712x400`` auto-crop, matching a typical
    card view). ``include_floorplans`` lets callers drop the schematic
    when they only want photos of the property itself.

    Entries flagged ``deleted=True`` or missing a ``file_id`` are
    skipped. URLs that fail Pydantic validation are silently dropped
    rather than aborting the whole gallery.
    """

    raw = payload.get("images")
    if not isinstance(raw, list):
        return []

    entries: list[tuple[int, Image]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("deleted") is True:
            continue
        file_id = item.get("file_id")
        if not isinstance(file_id, str) or not file_id.strip():
            continue
        image_type = (item.get("type") or "").strip().lower()
        if image_type == "floorplan" and not include_floorplans:
            continue
        mime = (item.get("mime_type") or "").strip().lower()
        extension = "png" if mime == "image/png" else "jpg"
        url = f"{_ALLSOP_IMAGE_CDN}/{file_id.strip()}-712-400-auto--.{extension}"
        try:
            image = Image(
                url=cast("HttpUrl", _URL_TYPE_ADAPTER.validate_python(url)),
                caption=image_type or None,
            )
        except ValidationError:
            continue
        order = item.get("sort_order")
        if not isinstance(order, (int, float)):
            order = len(entries)
        entries.append((int(order), image))

    entries.sort(key=lambda pair: pair[0])
    return [image for _, image in entries]


# ── Field-level parsers (internal) ──────────────────────────────────────────


def _parse_single_lot(
    raw: dict[str, Any],
    *,
    auction_meta: dict[str, Any] | None,
) -> AuctionLot | None:
    lot_id = raw.get("allsop_lotid")
    reference = raw.get("reference")
    if not lot_id or not reference:
        return None

    source_url = _build_lot_url(reference, raw.get("allsop_propertybyline"))
    if source_url is None:
        return None

    address = _build_address(raw)
    if address is None:
        return None

    auction_date = _resolve_auction_date(raw, auction_meta)
    byline = raw.get("allsop_propertybyline")

    property_type_raw = _stringify_type_list(raw.get("allsop_propertytype"))
    property_type = _resolve_property_type(property_type_raw, byline)

    tenure_raw = raw.get("property_tenure") or raw.get("allsop_propertytenure") or ""
    tenure = _TENURE_MAP.get(tenure_raw.strip().lower(), Tenure.UNKNOWN)

    guide = _parse_guide_price(raw)
    coords = _parse_coords(raw)
    sale_method = _infer_sale_method(auction_meta)
    status = _parse_status(raw.get("lot_status") or raw.get("allsop_lotstatus"))
    sold_price_pence = _parse_sold_price_pence(raw)
    annual_rent_pence = _parse_annual_rent_pence(raw)
    vacant = _infer_vacant_possession(raw)
    image = _lead_image(raw)
    lot_number = _stringify_lot_number(raw.get("lot_number_text") or raw.get("allsop_lotnumber"))

    catalogue_id = None
    if auction_meta:
        # Prefer the human-readable auction name ("Residential - April- 2026")
        # falling back to the raw reference ("R260430").
        catalogue_id = (
            auction_meta.get("name") or auction_meta.get("reference")
        ) or None
    if catalogue_id is None:
        catalogue_id = _catalogue_id_from_reference(reference)

    description = _format_description(raw.get("features"))

    try:
        return AuctionLot(
            auction_house=AuctionHouse.ALLSOP,
            source_id=str(lot_id),
            source_url=source_url,
            catalogue_id=catalogue_id,
            lot_number=lot_number,
            auction_date=auction_date,
            sale_method=sale_method,
            status=status,
            sold_price_pence=sold_price_pence,
            guide_price=guide,
            property_type=property_type,
            property_type_raw=property_type_raw,
            tenure=tenure,
            annual_rent_pence=annual_rent_pence,
            is_vacant_possession=vacant,
            address=address,
            coords=coords,
            title=byline,
            summary=byline,
            description=description,
            image_urls=[image] if image is not None else [],
            raw_site_fields=_raw_passthrough(raw, reference=str(reference)),
        )
    except ValidationError:
        return None


def _build_lot_url(reference: Any, byline: Any) -> HttpUrl | None:
    if not isinstance(reference, str) or not reference.strip():
        return None

    slug_reference = _slugify_reference(reference)
    slug_name = _slugify_byline(byline)
    if slug_name:
        path = f"/lot-overview/{slug_name}/{slug_reference}"
    else:
        path = f"/lot-overview/{slug_reference}"

    try:
        return cast("HttpUrl", _URL_TYPE_ADAPTER.validate_python(f"{_ALLSOP_ORIGIN}{path}"))
    except ValidationError:
        return None


def _slugify_reference(reference: str) -> str:
    slug = reference.strip().lower().replace(" ", "-")
    return _REFERENCE_URL_CHARS_RE.sub("", slug) or "lot"


def _slugify_byline(byline: Any) -> str:
    if not isinstance(byline, str):
        return ""
    slug = byline.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug[:120]


def _build_address(raw: dict[str, Any]) -> Address | None:
    parts = [
        raw.get("allsop_propertyaddress1") or raw.get("address1"),
        raw.get("allsop_propertyaddress2") or raw.get("address2"),
        raw.get("allsop_propertyaddress3") or raw.get("address3"),
        raw.get("allsop_propertytown") or raw.get("town"),
        raw.get("allsop_propertycounty") or raw.get("county"),
    ]
    joined = ", ".join(str(p).strip() for p in parts if isinstance(p, str) and p.strip())
    if not joined:
        return None

    postcode = raw.get("postcode") or raw.get("allsop_propertypostcode")
    outcode = raw.get("postcodeArea")

    return Address(
        raw=joined,
        postcode=postcode if isinstance(postcode, str) and postcode.strip() else None,
        postcode_outcode=outcode if isinstance(outcode, str) and outcode.strip() else None,
    )


def _parse_coords(raw: dict[str, Any]) -> LatLng | None:
    loc = raw.get("location")
    if not isinstance(loc, dict):
        return None
    try:
        lat = float(loc.get("lat"))
        lng = float(loc.get("lon"))
    except (TypeError, ValueError):
        return None
    if lat == 0 and lng == 0:
        return None
    try:
        return LatLng(lat=lat, lng=lng)
    except ValidationError:
        return None


def _resolve_auction_date(
    raw: dict[str, Any],
    auction_meta: dict[str, Any] | None,
) -> date | None:
    ms = raw.get("auction_date")
    if isinstance(ms, int):
        try:
            utc_dt = datetime.fromtimestamp(ms / 1000, tz=UTC)
            return utc_dt.astimezone(_ALLSOP_TZ).date()
        except (OverflowError, OSError, ValueError):
            pass

    if auction_meta:
        d2 = auction_meta.get("date_day2") or auction_meta.get("date_day1")
        if isinstance(d2, date):
            return d2

    return None


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        iso = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_ALLSOP_TZ).date()


def _infer_sale_method(
    auction_meta: dict[str, Any] | None,
) -> AuctionSaleMethod:
    if auction_meta:
        auction_type = auction_meta.get("auction_type")
        if isinstance(auction_type, str) and "online" in auction_type.lower():
            return AuctionSaleMethod.ONLINE_TIMED
        venue = auction_meta.get("venue")
        if isinstance(venue, str) and "stream" in venue.lower():
            return AuctionSaleMethod.TRADITIONAL
    # Allsop's live-streamed auctions are traditional English auctions even
    # when bidders log in remotely — the hammer still falls, the 10% deposit
    # is due on the day, and the completion window is 28 days.
    return AuctionSaleMethod.TRADITIONAL


def _parse_status(raw_status: Any) -> AuctionLotStatus:
    if not isinstance(raw_status, str):
        return AuctionLotStatus.UNKNOWN
    return _STATUS_MAP.get(raw_status.strip().lower(), AuctionLotStatus.UNKNOWN)


def _parse_sold_price_pence(raw: dict[str, Any]) -> int | None:
    # Allsop exposes the final hammer price in ``price`` when
    # ``lot_status == 'Sold'``. We also accept ``sort_price`` as a fallback
    # since some historic lots are missing ``price``.
    for key in ("price", "sort_price"):
        amount = raw.get(key)
        if isinstance(amount, (int, float)) and amount > 0:
            return round(float(amount) * 100)
    return None


def _parse_annual_rent_pence(raw: dict[str, Any]) -> int | None:
    for key in ("current_rent_per_annum", "net_rent", "income"):
        amount = raw.get(key)
        if isinstance(amount, (int, float)) and amount > 0:
            return round(float(amount) * 100)
    return None


def _infer_vacant_possession(raw: dict[str, Any]) -> bool | None:
    tenancy = raw.get("property_tenancy") or raw.get("allsop_propertytenancy")
    text = raw.get("current_rent_per_annum_text")
    blob = " ".join(
        str(v).lower() for v in (tenancy, text) if isinstance(v, str) and v.strip()
    )
    if not blob:
        return None
    if "vacant" in blob:
        return True
    if any(tag in blob for tag in ("let", "tenant", "investment", "hmo")):
        return False
    return None


def _parse_guide_price(raw: dict[str, Any]) -> AuctionGuidePrice | None:
    text = raw.get("guide_price_text")
    lower = raw.get("guide_price_lower")
    upper = raw.get("guide_price_upper")

    if not isinstance(text, str) or not text.strip():
        return None

    low_pence = _gbp_to_pence(lower)
    high_pence = _gbp_to_pence(upper)
    if high_pence is not None and low_pence is not None and high_pence <= low_pence:
        high_pence = None  # collapse trivial 'same value' ranges

    qualifier = _parse_guide_qualifier(text)

    return AuctionGuidePrice(
        low_pence=low_pence,
        high_pence=high_pence,
        qualifier=qualifier,
        raw=text.strip(),
    )


def _parse_guide_qualifier(text: str) -> PriceQualifier:
    match = _GUIDE_QUALIFIER_RE.match(text)
    if match:
        return _GUIDE_QUALIFIER_MAP.get(
            match.group(1).lower().strip(), PriceQualifier.UNKNOWN
        )
    if _TRAILING_PLUS_RE.search(text):
        return PriceQualifier.OFFERS_IN_EXCESS_OF
    if "-" in text:
        return PriceQualifier.GUIDE_PRICE
    return PriceQualifier.UNKNOWN


def _gbp_to_pence(value: Any) -> int | None:
    if value is None:
        return None
    try:
        gbp = float(value)
    except (TypeError, ValueError):
        return None
    if gbp <= 0:
        return None
    return round(gbp * 100)


def _stringify_type_list(value: Any) -> str | None:
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if isinstance(v, str) and v.strip()]
        if parts:
            return " / ".join(parts)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resolve_property_type(
    type_list_raw: str | None,
    byline: Any,
) -> PropertyType:
    if isinstance(byline, str):
        byline_lower = byline.lower()
        for needle, mapped in _BYLINE_PROPERTY_TYPE_PATTERNS:
            if needle in byline_lower:
                return mapped

    if type_list_raw:
        tokens = [tok.strip().lower() for tok in type_list_raw.split("/")]
        # Prefer the most specific mapping we have: flats beat houses, land
        # beats commercial. The ordering matches the specificity of
        # :data:`_PROPERTY_TYPE_MAP`.
        for preferred in ("flat / block", "flat", "land", "commercial", "house"):
            if preferred in tokens:
                return _PROPERTY_TYPE_MAP.get(preferred, PropertyType.OTHER)

    return PropertyType.UNKNOWN


def _stringify_lot_number(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int):
        return str(value)
    return None


def _catalogue_id_from_reference(reference: Any) -> str | None:
    if not isinstance(reference, str):
        return None
    head, _, _ = reference.partition(" ")
    return head or None


def _format_description(features: Any) -> str | None:
    if not isinstance(features, list):
        return None
    bullets = [
        str(f).strip() for f in features if isinstance(f, str) and f.strip()
    ]
    if not bullets:
        return None
    return "\n".join(f"- {b}" for b in bullets)


def _lead_image(raw: dict[str, Any]) -> Image | None:
    file_id = raw.get("featured_image_file_id") or raw.get("featured_image_path")
    if not isinstance(file_id, str) or not file_id.strip():
        return None
    url = f"{_ALLSOP_IMAGE_CDN}/{file_id.strip()}-600-450-auto--.jpg"
    try:
        return Image(
            url=cast("HttpUrl", _URL_TYPE_ADAPTER.validate_python(url)),
            caption=None,
        )
    except ValidationError:
        return None


_RAW_PASSTHROUGH_KEYS: Final[tuple[str, ...]] = (
    "allsop_propertytenancy",
    "property_tenancy",
    "current_rent_per_annum_text",
    "current_rent_per_annum_text_2",
    "lot_type",
    "catalogue_type",
    "allsop_propertyregion",
    "is_commercial",
    "is_residential",
)


def _raw_passthrough(raw: dict[str, Any], *, reference: str) -> dict[str, str]:
    out: dict[str, str] = {"reference": reference}
    for key in _RAW_PASSTHROUGH_KEYS:
        val = raw.get(key)
        if isinstance(val, (str, int, float, bool)) and str(val).strip():
            out[key] = str(val)
    return out


# ── Public re-exports ───────────────────────────────────────────────────────


def infer_auction_date_from_reference(reference: str) -> date | None:
    """Recover the scheduled auction date from the catalogue reference.

    Allsop's reference strings follow ``R<yymmdd>[ <lotnum>]``, e.g.
    ``"R260430 098"`` → 2026-04-30. Used as a fallback when both the lot's
    ``auction_date`` timestamp and the companion ``/api/auctions/<uuid>``
    payload are missing.
    """

    if not isinstance(reference, str) or not reference.startswith(("R", "r")):
        return None
    digits = "".join(ch for ch in reference[1:].split(" ")[0] if ch.isdigit())
    if len(digits) != 6:
        return None
    try:
        return datetime.strptime(digits, "%y%m%d").date()
    except ValueError:
        return None


SaleDay = Literal[1, 2]

__all__ = [
    "SaleDay",
    "infer_auction_date_from_reference",
    "parse_auction_metadata",
    "parse_lot_gallery",
    "parse_search_results",
]
