"""Parser for Zoopla search-results HTML.

Selectors were derived from live Zoopla crawls (April 2026, fixture at
``tests/fixtures/zoopla/search_cambridgeshire_2026-04.html``). Zoopla is a
Next.js app that ships CSS-module class names of the shape
``<componentName>_<cssRule>__<hashSuffix>``. The prefix (``price_priceText``,
``amenities_amenityListSlim``, etc.) is stable across deploys; the trailing
hash rotates. All selectors use ``[class*="<prefix>"]`` substring matching so
the parser is resilient to hash churn.

Zoopla serves three URL patterns for listing detail pages:
    * ``/for-sale/details/{id}/``    — resale properties
    * ``/new-homes/details/{id}/``   — new builds
    * ``/to-rent/details/{id}/``     — rentals

All functions are pure: they accept an HTML ``str`` and return Pydantic models.
Browser orchestration, proxies, rate-limiting, retries — those live in the
caller (MCP server, Apify actor, or CLI).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Final

from pydantic import ValidationError
from selectolax.parser import HTMLParser, Node

from uk_property_scrapers._common import (
    FLOORPLAN_CAPTION,
    extract_uk_coords,
    is_floorplan_url,
)
from uk_property_scrapers.schema import (
    Address,
    Agent,
    BroadbandSpeed,
    BroadbandTier,
    EnergyRating,
    Image,
    LeaseTerms,
    Listing,
    ListingFeature,
    ListingType,
    MaterialInformation,
    MobileCoverageLevel,
    MobileSignal,
    Price,
    PriceQualifier,
    PropertyTimelineEvent,
    PropertyTimelineEventKind,
    PropertyType,
    RentPeriod,
    RentPrice,
    Source,
    Tenure,
    TransactionType,
)

# ── URL patterns ─────────────────────────────────────────────────────────────

_ZOOPLA_ORIGIN: Final = "https://www.zoopla.co.uk"

_DETAIL_HREF_PATTERNS: Final = (
    "/for-sale/details/",
    "/new-homes/details/",
    "/to-rent/details/",
)
_EXCLUDED_HREF_SUBSTRINGS: Final = (
    "/contact/",
    "/enquiry/",
    "/viewing-request/",
)

_DETAIL_ID_RE: Final = re.compile(r"/details/(\d+)/?")
_LISTING_ID_FROM_ATTR_RE: Final = re.compile(r"^listing_(\d+)$")

_TRANSACTION_FROM_URL: Final = {
    "/for-sale/details/": TransactionType.SALE,
    "/new-homes/details/": TransactionType.SALE,
    "/to-rent/details/": TransactionType.RENT,
}

# ── Text-level patterns ──────────────────────────────────────────────────────

_PRICE_AMOUNT_RE: Final = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
_INT_RE: Final = re.compile(r"(\d+)")
_FLOAT_RE: Final = re.compile(r"(\d+(?:[.,]\d+)?)")
_IMAGE_COUNT_RE: Final = re.compile(r"(\d+)\s*/\s*(\d+)")

_QUALIFIER_MAP: Final[dict[str, PriceQualifier]] = {
    "guide price": PriceQualifier.GUIDE_PRICE,
    "offers in excess of": PriceQualifier.OFFERS_IN_EXCESS_OF,
    "offers in the region of": PriceQualifier.OFFERS_IN_REGION,
    "offers over": PriceQualifier.OFFERS_OVER,
    "oieo": PriceQualifier.OFFERS_IN_EXCESS_OF,
    "oiro": PriceQualifier.OFFERS_IN_REGION,
    "from": PriceQualifier.FROM,
    "fixed price": PriceQualifier.FIXED_PRICE,
    "asking price": PriceQualifier.ASKING_PRICE,
    "poa": PriceQualifier.POA,
    "price on application": PriceQualifier.POA,
    "shared ownership from": PriceQualifier.SHARED_OWNERSHIP_FROM,
}

_RENT_PERIOD_MAP: Final[dict[str, RentPeriod]] = {
    "per calendar month": RentPeriod.PER_MONTH,
    "pcm": RentPeriod.PER_MONTH,
    "per month": RentPeriod.PER_MONTH,
    "a month": RentPeriod.PER_MONTH,
    "/ month": RentPeriod.PER_MONTH,
    "per week": RentPeriod.PER_WEEK,
    "a week": RentPeriod.PER_WEEK,
    "pw": RentPeriod.PER_WEEK,
    "/ week": RentPeriod.PER_WEEK,
    "per annum": RentPeriod.PER_YEAR,
    "per year": RentPeriod.PER_YEAR,
    " pa": RentPeriod.PER_YEAR,
    " pd": RentPeriod.PER_DAY,
    "per day": RentPeriod.PER_DAY,
}

# Ordered most-specific-first so "detached house" wins over "detached".
_PROPERTY_TYPE_HINTS: Final[tuple[tuple[str, PropertyType], ...]] = (
    ("end of terrace", PropertyType.END_OF_TERRACE),
    ("end terrace", PropertyType.END_OF_TERRACE),
    ("semi-detached house", PropertyType.SEMI_DETACHED),
    ("semi detached", PropertyType.SEMI_DETACHED),
    ("semi-detached", PropertyType.SEMI_DETACHED),
    ("detached house", PropertyType.DETACHED),
    ("detached home", PropertyType.DETACHED),
    ("detached bungalow", PropertyType.BUNGALOW),
    ("detached", PropertyType.DETACHED),
    ("terraced house", PropertyType.TERRACED),
    ("terraced home", PropertyType.TERRACED),
    ("terrace", PropertyType.TERRACED),
    ("apartment", PropertyType.APARTMENT),
    ("maisonette", PropertyType.MAISONETTE),
    ("bungalow", PropertyType.BUNGALOW),
    ("cottage", PropertyType.COTTAGE),
    ("studio", PropertyType.STUDIO),
    ("park home", PropertyType.PARK_HOME),
    ("houseboat", PropertyType.HOUSEBOAT),
    ("land for sale", PropertyType.LAND),
    ("plot for sale", PropertyType.LAND),
    ("commercial", PropertyType.COMMERCIAL),
    ("flat", PropertyType.FLAT),
)

_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)(?:\s+([0-9][A-Z]{2}))?\b"
)

_FEATURE_TOKEN_MAP: Final[dict[str, ListingFeature]] = {
    "new home": ListingFeature.NEW_HOME,
    "new build": ListingFeature.NEW_HOME,
    "reduced": ListingFeature.REDUCED,
    "chain free": ListingFeature.CHAIN_FREE,
    "chain-free": ListingFeature.CHAIN_FREE,
    "no chain": ListingFeature.CHAIN_FREE,
    "retirement": ListingFeature.RETIREMENT,
    "shared ownership": ListingFeature.SHARED_OWNERSHIP,
    "part buy part rent": ListingFeature.PART_BUY_PART_RENT,
    "part-buy part-rent": ListingFeature.PART_BUY_PART_RENT,
    "auction": ListingFeature.AUCTION,
    "investment": ListingFeature.INVESTMENT,
    "tenanted": ListingFeature.TENANTED_INVESTMENT,
    "new listing": ListingFeature.NEW_LISTING,
    "just added": ListingFeature.NEW_LISTING,
    "featured": ListingFeature.FEATURED,
    "property of the week": ListingFeature.PROPERTY_OF_THE_WEEK,
    "premium": ListingFeature.PREMIUM,
    "sold stc": ListingFeature.SOLD_STC,
    "sold (stc)": ListingFeature.SOLD_STC,
    "under offer": ListingFeature.UNDER_OFFER,
    "open day": ListingFeature.OPEN_DAY,
    "open house": ListingFeature.OPEN_DAY,
    "video tour": ListingFeature.VIDEO_TOUR,
    "virtual tour": ListingFeature.VIRTUAL_TOUR,
    "3d tour": ListingFeature.VIRTUAL_TOUR,
}

_TENURE_TOKEN_MAP: Final[dict[str, Tenure]] = {
    "freehold": Tenure.FREEHOLD,
    "leasehold": Tenure.LEASEHOLD,
    "share of freehold": Tenure.SHARE_OF_FREEHOLD,
    "commonhold": Tenure.COMMONHOLD,
    "feuhold": Tenure.FEUHOLD,
}


# ── Public API ───────────────────────────────────────────────────────────────


def extract_listing_urls(html: str) -> list[str]:
    """Return a de-duplicated list of Zoopla listing-detail URLs found in the HTML.

    Accepts any Zoopla page (search results, area page, saved search, agent page).
    Only returns URLs matching the known detail-page patterns; filters out contact,
    agent, and navigational links.
    """
    tree = HTMLParser(html)
    seen: set[str] = set()
    urls: list[str] = []

    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if not any(pattern in href for pattern in _DETAIL_HREF_PATTERNS):
            continue
        if any(excluded in href for excluded in _EXCLUDED_HREF_SUBSTRINGS):
            continue
        canonical = _strip_query(_absolutize(href))
        if canonical in seen:
            continue
        seen.add(canonical)
        urls.append(canonical)
    return urls


def parse_search_results(
    html: str,
    *,
    transaction_type: TransactionType = TransactionType.UNKNOWN,
) -> list[Listing]:
    """Parse a Zoopla search-results page into a list of SEARCH_CARD listings.

    The ``transaction_type`` hint should reflect the search URL the HTML came
    from (``/for-sale/...`` or ``/to-rent/...``). When ``UNKNOWN``, each card's
    URL is used to infer it.

    Cards that fail :class:`Listing` validation (eg. a block-of-flats entry
    advertising 100+ bedrooms) are silently skipped so one bad card doesn't
    kill the whole page.
    """
    tree = HTMLParser(html)
    cards = _find_listing_cards(tree)
    listings: list[Listing] = []
    for card in cards:
        try:
            listing = _parse_search_card(card, hinted_type=transaction_type)
        except ValidationError:
            continue
        if listing is not None:
            listings.append(listing)
    return listings


def parse_detail_page(
    html: str,
    *,
    source_url: str | None = None,
    transaction_type: TransactionType = TransactionType.UNKNOWN,
) -> Listing | None:
    """Parse a Zoopla property-detail page into a single DETAIL Listing.

    Uses the embedded JSON-LD ``RealEstateListing`` block as the primary data
    source (Zoopla exposes a clean ``schema.org`` payload on every detail page),
    and falls back to CSS-selector extraction for fields JSON-LD omits
    (tenure, agent, full amenities, description).

    Defence in depth: Zoopla ships CSS-module class names (``Price_price__<hash>``,
    ``page_titleWrapper__<hash>``) whose prefix is stable across deploys but
    whose hash rotates. On top of CSS-prefix matching we also scan the
    Next.js RSC hydration payload (``__next_f`` pushes) for the same price /
    address / photo / coord data, so a hash drift in isolation cannot lose
    the headline fields.
    """
    tree = HTMLParser(html)
    ld = _find_realestate_jsonld(tree)
    nextjs = _parse_zoopla_nextjs_payload(html)

    if ld is None and source_url is None and nextjs is None:
        return None

    url = (
        _coerce_str(ld.get("mainEntityOfPage") if ld else None)
        or source_url
        or (nextjs.get("source_url") if nextjs else None)
    )
    if not url:
        return None
    source_id = _extract_listing_id(url)
    if source_id is None:
        return None

    tx = (
        transaction_type
        if transaction_type != TransactionType.UNKNOWN
        else _transaction_from_url(url)
    )

    title, address_raw = _parse_detail_title_address(tree)
    title = title or _coerce_str(ld.get("name") if ld else None)

    if not address_raw and ld:
        address_raw = _derive_address_from_ld_name(_coerce_str(ld.get("name")))
    if not address_raw and nextjs:
        address_raw = nextjs.get("address")

    if not address_raw:
        return None

    price_raw, price_qualifier_raw = _parse_detail_price(tree)
    amount_pence = _extract_price_pence(price_raw) if price_raw else None
    if amount_pence is None and ld:
        offer = ld.get("offers") or {}
        ld_price = offer.get("price") if isinstance(offer, dict) else None
        if isinstance(ld_price, (int, float)):
            amount_pence = round(float(ld_price) * 100)
    if amount_pence is None and nextjs:
        amount_pence = nextjs.get("amount_pence")
    if not price_raw and nextjs and nextjs.get("price_raw"):
        price_raw = nextjs["price_raw"]

    sale_price, rent_price = _materialize_prices(
        raw=price_raw or "",
        qualifier_raw=price_qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    amenities = _parse_detail_amenities(tree)
    beds = amenities.get("beds")
    baths = amenities.get("baths")
    receptions = amenities.get("receptions")
    sqft = amenities.get("sqft")

    if beds is None and ld:
        beds = _ld_property_value_int(ld, "Bedrooms")
    if baths is None and ld:
        baths = _ld_property_value_int(ld, "Bathrooms")

    description = _parse_detail_description(tree)
    if not description and ld:
        description = _coerce_str(ld.get("description"))

    image_urls = _parse_detail_images(
        tree,
        floorplan_urls=nextjs.get("floorplan_urls") if nextjs else None,
    )
    if not image_urls and ld:
        img = _coerce_str(ld.get("image"))
        if img:
            image_urls = [Image(url=img)]  # type: ignore[arg-type]
    if not image_urls and nextjs and nextjs.get("image_urls"):
        image_urls = [Image(url=u) for u in nextjs["image_urls"]]  # type: ignore[arg-type]

    property_type_raw = _parse_detail_property_type(title or "")
    property_type = _infer_property_type(property_type_raw) if property_type_raw else PropertyType.UNKNOWN

    first_listed = None
    if ld:
        posted = _coerce_str(ld.get("datePosted"))
        if posted:
            first_listed = _parse_iso(posted)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    tenure = _parse_detail_tenure(tree)
    agent = _parse_detail_agent(tree, nextjs=nextjs)
    features = _parse_detail_features(tree)
    if features and rent_price is not None and ListingFeature.AUCTION in features:
        features.remove(ListingFeature.AUCTION)

    coords = extract_uk_coords(html)

    # Detail-page enrichments — all optional, populated when the page exposes them.
    nts_entries = (nextjs or {}).get("nts_entries") or {}
    ad_targeting = (nextjs or {}).get("ad_targeting") or {}

    timeline = _parse_detail_timeline(tree)
    broadband = _nts_broadband(nts_entries)
    mobile_signal_list = _nts_mobile_signal(nts_entries)
    council_tax_band = _nts_council_tax_band(nts_entries)
    epc = _parse_detail_epc(nextjs or {})
    lease = _nts_lease(nts_entries)

    # Upgrade tenure from ntsInfo/adTargeting when DOM detection found nothing.
    if tenure == Tenure.UNKNOWN:
        for candidate in (nts_entries.get("tenure"), ad_targeting.get("tenure")):
            if isinstance(candidate, str):
                tenure = _detect_tenure(candidate.lower())
                if tenure != Tenure.UNKNOWN:
                    break

    material = _build_material_information(
        nts_entries=nts_entries,
        council_tax_band=council_tax_band,
        tenure=tenure,
        lease=lease,
        epc=epc,
        broadband=broadband,
        mobile_signal=mobile_signal_list,
    )

    return Listing(
        source=Source.ZOOPLA,
        source_id=source_id,
        source_url=url,  # type: ignore[arg-type]
        listing_type=ListingType.DETAIL,
        transaction_type=tx,
        sale_price=sale_price,
        rent_price=rent_price,
        property_type=property_type,
        property_type_raw=property_type_raw,
        bedrooms=beds,
        bathrooms=baths,
        reception_rooms=receptions,
        floor_area_sqft=sqft,
        tenure=tenure,
        address=address,
        coords=coords,
        title=title,
        summary=None,
        description=description,
        features=features,
        image_urls=image_urls,
        agent=agent,
        first_listed_at=first_listed,
        lease=lease,
        broadband=broadband,
        mobile_signal=mobile_signal_list,
        epc=epc,
        council_tax_band=council_tax_band,
        timeline=timeline,
        material_information=material,
        raw_site_fields={
            k: v
            for k, v in {
                "price": price_raw,
                "price_qualifier": price_qualifier_raw,
                "property_type_raw": property_type_raw,
            }.items()
            if v
        },
    )


# ── Search card discovery ───────────────────────────────────────────────────


def _find_listing_cards(tree: HTMLParser) -> list[Node]:
    """Locate each listing row.

    Zoopla wraps every search result in ``<div id="listing_{id}" class="Listings_listingRow__...">``
    inside ``[data-testid="regular-listings"]``. We target that ID prefix as the
    primary selector — it's been stable for years. If Zoopla ships a new shape,
    we fall back to any element carrying the card-content testid, then to any
    anchor pointing at a detail URL.
    """
    primary = tree.css('div[id^="listing_"]')
    if primary:
        return primary

    fallback_cards = tree.css('[data-testid="listing-card-content"]')
    if fallback_cards:
        return fallback_cards

    synthetic: list[Node] = []
    seen_hrefs: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href", "") or ""
        if not any(p in href for p in _DETAIL_HREF_PATTERNS):
            continue
        if any(e in href for e in _EXCLUDED_HREF_SUBSTRINGS):
            continue
        canonical = _strip_query(_absolutize(href))
        if canonical in seen_hrefs:
            continue
        seen_hrefs.add(canonical)
        synthetic.append(anchor)
    return synthetic


def _parse_search_card(card: Node, *, hinted_type: TransactionType) -> Listing | None:
    """Turn a single listing-row DOM node into a SEARCH_CARD Listing."""
    url = _find_detail_url(card)
    if url is None:
        return None

    source_id = _extract_source_id_from_row(card) or _extract_listing_id(url)
    if source_id is None:
        return None

    tx = hinted_type if hinted_type != TransactionType.UNKNOWN else _transaction_from_url(url)

    # Card anchor holds most of the content. Fall back to walking up from the
    # row if there's no anchor with the testid (rare — but keeps the parser
    # degrading gracefully).
    content = card.css_first('a[data-testid="listing-card-content"]') or card

    address_raw = _clean_whitespace(_first_text(content, ['address[class*="summary_address"]', "address"]))
    if not address_raw:
        address_raw = _clean_whitespace(_first_text(content, ["h2", "h3"]))
    if not address_raw:
        return None

    price_text = _clean_whitespace(_first_text(content, ['[class*="price_priceText"]', '[class*="PriceText"]']))
    price_qualifier_raw = _clean_whitespace(
        _first_text(content, ['[class*="price_priceTitle"]', '[class*="PriceTitle"]'])
    )

    amenity_list = content.css_first('[class*="amenities_amenityListSlim"]') or content.css_first(
        '[class*="amenities_amenityList"]'
    )
    amenities_raw = _parse_amenity_items(amenity_list)
    beds = amenities_raw.get("beds")
    baths = amenities_raw.get("baths")
    receptions = amenities_raw.get("receptions")
    sqft = amenities_raw.get("sqft")

    summary = _clean_whitespace(_first_text(content, ['p[class*="summary_summary"]']))

    badges_text = _collect_badges(card)
    status_badges_text = _collect_status_badges(card)
    tenure = _detect_tenure(" ".join(badges_text))

    features = _detect_features(
        blob=" ".join(
            filter(
                None,
                [
                    address_raw,
                    price_text,
                    price_qualifier_raw,
                    summary,
                    " ".join(badges_text),
                    " ".join(status_badges_text),
                ],
            )
        ),
        url=url,
    )

    amount_pence = _extract_price_pence(price_text) if price_text else None
    sale_price, rent_price = _materialize_prices(
        raw=price_text or "",
        qualifier_raw=price_qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    property_type_raw = _parse_detail_property_type(summary or "") or _parse_detail_property_type(
        address_raw
    )
    property_type = _infer_property_type(property_type_raw) if property_type_raw else PropertyType.UNKNOWN

    images, image_count = _parse_card_images(card)
    agent = _parse_card_agent(card)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    return Listing(
        source=Source.ZOOPLA,
        source_id=source_id,
        source_url=url,  # type: ignore[arg-type]
        listing_type=ListingType.SEARCH_CARD,
        transaction_type=tx,
        sale_price=sale_price,
        rent_price=rent_price,
        property_type=property_type,
        property_type_raw=property_type_raw,
        bedrooms=beds,
        bathrooms=baths,
        reception_rooms=receptions,
        floor_area_sqft=sqft,
        tenure=tenure,
        address=address,
        title=address_raw,
        summary=summary,
        features=features,
        image_urls=images,
        image_count=image_count,
        agent=agent,
        raw_site_fields=_build_raw_fields(
            price=price_text,
            price_qualifier=price_qualifier_raw,
            beds=amenities_raw.get("beds_raw"),
            baths=amenities_raw.get("baths_raw"),
            receptions=amenities_raw.get("receptions_raw"),
            sqft=amenities_raw.get("sqft_raw"),
            badges=" | ".join(badges_text) if badges_text else None,
            status_badges=" | ".join(status_badges_text) if status_badges_text else None,
            property_type=property_type_raw,
        ),
    )


# ── Field extractors ─────────────────────────────────────────────────────────


def _find_detail_url(card: Node) -> str | None:
    anchor = card.css_first('a[data-testid="listing-card-content"][href]')
    if anchor is not None:
        href = anchor.attributes.get("href", "") or ""
        if href and any(p in href for p in _DETAIL_HREF_PATTERNS):
            return _strip_query(_absolutize(href))

    for anchor in card.css("a[href]"):
        href = anchor.attributes.get("href", "") or ""
        if not any(p in href for p in _DETAIL_HREF_PATTERNS):
            continue
        if any(e in href for e in _EXCLUDED_HREF_SUBSTRINGS):
            continue
        return _strip_query(_absolutize(href))

    if card.tag == "a":
        href = card.attributes.get("href", "") or ""
        if href and any(p in href for p in _DETAIL_HREF_PATTERNS):
            return _strip_query(_absolutize(href))
    return None


def _extract_source_id_from_row(card: Node) -> str | None:
    identifier = card.attributes.get("id", "") or ""
    match = _LISTING_ID_FROM_ATTR_RE.match(identifier)
    return match.group(1) if match else None


def _extract_listing_id(url: str) -> str | None:
    match = _DETAIL_ID_RE.search(url)
    return match.group(1) if match else None


def _parse_amenity_items(amenity_list: Node | None) -> dict[str, int | str | None]:
    """Parse the slim amenity list like '6 beds · 3 baths · 2 receptions · ~1894 sq ft'."""
    out: dict[str, int | str | None] = {}
    if amenity_list is None:
        return out

    items = amenity_list.css('[class*="amenities_amenityItemSlim"]') or amenity_list.css(
        '[class*="amenities_amenity"]'
    )
    for node in items:
        text = _clean_whitespace(node.text(strip=True))
        if not text:
            continue
        lower = text.lower()
        if "bed" in lower and "beds" not in out:
            out["beds"] = _parse_int(text)
            out["beds_raw"] = text
        elif "bath" in lower and "baths" not in out:
            out["baths"] = _parse_int(text)
            out["baths_raw"] = text
        elif ("reception" in lower or "living" in lower) and "receptions" not in out:
            out["receptions"] = _parse_int(text)
            out["receptions_raw"] = text
        elif ("sq ft" in lower or "sq. ft" in lower or "sqft" in lower) and "sqft" not in out:
            out["sqft"] = _parse_int(text)
            out["sqft_raw"] = text
    return out


def _collect_badges(card: Node) -> list[str]:
    badges: list[str] = []
    for wrapper in card.css('[class*="badges_badgesListSlim"] li'):
        text = _clean_whitespace(wrapper.text(strip=True))
        if text:
            badges.append(text)
    return badges


def _collect_status_badges(card: Node) -> list[str]:
    badges: list[str] = []
    for wrapper in card.css('[class*="status_statusListSlim"] li'):
        text = _clean_whitespace(wrapper.text(strip=True))
        if text:
            badges.append(text)
    return badges


def _parse_card_images(card: Node) -> tuple[list[Image], int | None]:
    """Extract image URLs from the gallery + the total image count from pagination badge."""
    images: list[Image] = []
    seen: set[str] = set()

    primary_source = card.css_first(
        'picture source[srcset][type="image/jpeg"]'
    ) or card.css_first("picture source[srcset]")
    if primary_source is not None:
        url = _first_srcset_url(primary_source.attributes.get("srcset"))
        if url and url not in seen:
            seen.add(url)
            images.append(Image(url=url))  # type: ignore[arg-type]

    for img in card.css('img[class*="Listings_additionalImage"]'):
        src = img.attributes.get("src") or ""
        if src and src not in seen and src.startswith("http"):
            seen.add(src)
            images.append(Image(url=src))  # type: ignore[arg-type]

    if not images:
        for img in card.css("picture img, img[src]"):
            src = img.attributes.get("src") or ""
            if src.startswith("http") and src not in seen:
                seen.add(src)
                images.append(Image(url=src))  # type: ignore[arg-type]
                break

    pagination = card.css_first('[data-testid="pagination-count"]')
    image_count: int | None = None
    if pagination is not None:
        match = _IMAGE_COUNT_RE.search(pagination.text(strip=True))
        if match:
            image_count = int(match.group(2))
    if image_count is None:
        gallery_items = card.css('ol[role="list"] > li[data-slide]')
        if gallery_items:
            image_count = len(gallery_items)

    return images, image_count


def _parse_card_agent(card: Node) -> Agent | None:
    logo = card.css_first('img[class*="agent-logo_agentLogoImage"]') or card.css_first(
        'img[alt][src*="static_agent_logo"]'
    )
    if logo is None:
        return None
    alt = logo.attributes.get("alt") or ""
    src = logo.attributes.get("src") or None
    name: str | None
    branch: str | None
    if " - " in alt:
        name, branch = (seg.strip() for seg in alt.split(" - ", 1))
    else:
        name = alt.strip() or None
        branch = None
    return Agent(
        name=name,
        branch=branch,
        logo_url=src if src and src.startswith("http") else None,  # type: ignore[arg-type]
    )


def _first_srcset_url(srcset: str | None) -> str | None:
    if not srcset:
        return None
    candidates = [seg.strip() for seg in srcset.split(",") if seg.strip()]
    if not candidates:
        return None
    url_part = candidates[0].split(" ")[0]
    return _normalize_image_url(url_part)


def _normalize_image_url(url: str) -> str | None:
    if not url:
        return None
    if url.endswith(":p"):
        url = url[:-2]
    return url if url.startswith("http") else None


# ── Detail page helpers ─────────────────────────────────────────────────────


def _find_realestate_jsonld(tree: HTMLParser) -> dict | None:
    for script in tree.css('script[type="application/ld+json"]'):
        text = script.text(strip=False).strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("@type") == "RealEstateListing":
            return obj
        if isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict) and item.get("@type") == "RealEstateListing":
                    return item
    return None


def _parse_detail_title_address(tree: HTMLParser) -> tuple[str | None, str | None]:
    h1 = tree.css_first('h1[class*="page_titleWrapper"]') or tree.css_first("h1")
    if h1 is None:
        return None, None
    address_node = h1.css_first('address[class*="page_address"]') or h1.css_first("address")
    address_raw = _clean_whitespace(address_node.text(strip=True)) if address_node else None

    if address_node is not None:
        address_node.decompose()
    title = _clean_whitespace(h1.text(strip=True)) or None
    return title, address_raw


def _parse_detail_price(tree: HTMLParser) -> tuple[str | None, str | None]:
    wrapper = tree.css_first('[class*="Price_priceWrapper"]')
    price_el = (wrapper.css_first('[class*="Price_price__"]') if wrapper else None) or tree.css_first(
        '[class*="Price_price__"]'
    )
    price_text = _clean_whitespace(price_el.text(strip=True)) if price_el else None

    qualifier_text: str | None = None
    if wrapper is not None:
        preceding = wrapper.parent
        if preceding is not None:
            p = preceding.css_first("p")
            if p is not None:
                qualifier_text = _clean_whitespace(p.text(strip=True))

    return price_text, qualifier_text


def _parse_detail_amenities(tree: HTMLParser) -> dict[str, int | None]:
    """Detail page amenity list is different from search — ul[class^='Amenities_amenitiesList']."""
    out: dict[str, int | None] = {}
    amenity_nodes = tree.css('ul[class*="Amenities_amenitiesList"] [class*="Amenities_amenity__"]')
    for node in amenity_nodes:
        text = _clean_whitespace(node.text(strip=True))
        if not text:
            continue
        lower = text.lower()
        if "bed" in lower and out.get("beds") is None:
            out["beds"] = _parse_int(text)
        elif "bath" in lower and out.get("baths") is None:
            out["baths"] = _parse_int(text)
        elif "reception" in lower and out.get("receptions") is None:
            out["receptions"] = _parse_int(text)
        elif ("sq ft" in lower or "sq. ft" in lower) and out.get("sqft") is None:
            out["sqft"] = _parse_int(text)
    out.setdefault("beds", None)
    out.setdefault("baths", None)
    out.setdefault("receptions", None)
    out.setdefault("sqft", None)
    return out


def _parse_detail_description(tree: HTMLParser) -> str | None:
    node = tree.css_first('[class*="DetailedDescription_detailedDescription"]')
    if node is None:
        return None
    # Strip scripts/styles within — just in case.
    for junk in node.css("script, style"):
        junk.decompose()
    return _clean_whitespace(node.text(separator="\n"))


def _parse_detail_images(
    tree: HTMLParser,
    *,
    floorplan_urls: set[str] | None = None,
) -> list[Image]:
    """Extract gallery + floorplan images as one deduplicated ``list[Image]``.

    Floorplan-tagging rules:
      1. Any URL present in ``floorplan_urls`` (derived from the Next.js
         hydration payload's ``floorPlan`` block) is tagged ``caption="floorplan"``.
      2. Any URL whose shape looks like a floorplan (``is_floorplan_url``) is
         tagged as a fallback.
    """
    fp_set = floorplan_urls or set()
    images: list[Image] = []
    seen: set[str] = set()
    for source in tree.css("picture source[srcset]"):
        url = _first_srcset_url(source.attributes.get("srcset"))
        if not url or url in seen:
            continue
        seen.add(url)
        caption = FLOORPLAN_CAPTION if (url in fp_set or is_floorplan_url(url)) else None
        images.append(Image(url=url, caption=caption))  # type: ignore[arg-type]
        if len(images) >= 30:
            break
    for fp_url in fp_set:
        if fp_url in seen:
            continue
        seen.add(fp_url)
        images.append(Image(url=fp_url, caption=FLOORPLAN_CAPTION))  # type: ignore[arg-type]
    return images


def _parse_detail_property_type(text: str) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    for phrase, _ in _PROPERTY_TYPE_HINTS:
        if phrase in lowered:
            return phrase
    return None


def _parse_detail_tenure(tree: HTMLParser) -> Tenure:
    text = " ".join(
        _clean_whitespace(node.text(strip=True)) or ""
        for node in tree.css('[class*="DetailedDescription"], [class*="KeyInformation"], dt, dd')
    ).lower()
    return _detect_tenure(text)


def _parse_detail_agent(
    tree: HTMLParser, *, nextjs: dict[str, object] | None = None
) -> Agent | None:
    """Extract the agent associated with a detail page.

    Zoopla exposes a ``Contact agent`` sidebar whose React props are embedded
    verbatim in the Next.js RSC stream: ``branchId``, ``name`` (e.g.
    ``'Connells - Cambourne'``), ``number`` (phone), ``url.contact`` (the
    enquiry endpoint) and ``url.listings`` (the branch page). When the RSC
    payload is available we prefer it — it's the only place phone + branch id
    are machine-readable. When it's missing we fall back to the image-alt
    heuristic that already worked on search cards.
    """
    contact = (nextjs or {}).get("contact_agent") if nextjs else None
    logo = tree.css_first('img[class*="BranchSummary"][alt]') or tree.css_first(
        'img[class*="agent-logo"][alt]'
    )

    alt = (logo.attributes.get("alt") if logo is not None else None) or ""
    logo_src = (logo.attributes.get("src") if logo is not None else None) or None

    name: str | None = None
    branch: str | None = None
    phone: str | None = None
    source_id: str | None = None
    group_name: str | None = None
    url: str | None = None

    if isinstance(contact, dict):
        raw_name = _coerce_str(contact.get("name"))
        if raw_name:
            if " - " in raw_name:
                group_seg, branch_seg = (seg.strip() for seg in raw_name.split(" - ", 1))
                group_name = group_seg or None
                branch = branch_seg or None
                name = raw_name
            else:
                name = raw_name
        phone = _coerce_str(contact.get("number")) or None
        branch_id = contact.get("branchId")
        if isinstance(branch_id, (int, str)):
            source_id = str(branch_id)
        url_block = contact.get("url")
        if isinstance(url_block, dict):
            listings_path = _coerce_str(url_block.get("listings"))
            if listings_path and listings_path.startswith("/"):
                url = _ZOOPLA_ORIGIN + listings_path

    # Fall back to adTargeting block for brand / branch id if still missing.
    if nextjs and (source_id is None or group_name is None or name is None):
        ad = (nextjs or {}).get("ad_targeting") or {}
        if name is None:
            name = _coerce_str(ad.get("branchName"))
        if group_name is None:
            group_name = _coerce_str(ad.get("brandName"))
        if source_id is None and isinstance(ad.get("branchId"), (int, str)):
            source_id = str(ad["branchId"])

    # DOM alt-tag fallback for freshly-deployed Zoopla layouts.
    if name is None and alt:
        if " - " in alt:
            alt_name, alt_branch = (seg.strip() for seg in alt.split(" - ", 1))
            name = alt_name or None
            if branch is None:
                branch = alt_branch or None
        else:
            name = alt.strip() or None

    if name is None and logo_src is None:
        return None

    return Agent(
        name=name,
        branch=branch,
        phone=phone,
        url=url if url and url.startswith("http") else None,  # type: ignore[arg-type]
        logo_url=logo_src if logo_src and logo_src.startswith("http") else None,  # type: ignore[arg-type]
        source_id=source_id,
        group_name=group_name,
    )


def _parse_detail_features(tree: HTMLParser) -> list[ListingFeature]:
    blob = " ".join(
        _clean_whitespace(node.text(strip=True)) or ""
        for node in tree.css(
            '[class*="DetailedDescription"], [class*="Features"], [class*="KeyInformation"], li'
        )
    )
    return _detect_features(blob=blob, url=None)


# ── Value detection ─────────────────────────────────────────────────────────


def _transaction_from_url(url: str) -> TransactionType:
    for pattern, tx in _TRANSACTION_FROM_URL.items():
        if pattern in url:
            return tx
    return TransactionType.UNKNOWN


def _materialize_prices(
    *,
    raw: str,
    qualifier_raw: str | None,
    amount_pence: int | None,
    transaction_type: TransactionType,
) -> tuple[Price | None, RentPrice | None]:
    if not raw and amount_pence is None:
        return None, None

    qualifier_source = " ".join(filter(None, [raw, qualifier_raw])).lower()
    qualifier = _detect_qualifier(qualifier_source)

    if transaction_type == TransactionType.RENT or any(k in raw.lower() for k in _RENT_PERIOD_MAP):
        period = _detect_rent_period(raw.lower())
        return None, RentPrice(
            amount_pence=amount_pence,
            qualifier=qualifier,
            raw=raw or (qualifier_raw or ""),
            period=period,
        )

    if transaction_type == TransactionType.SHARED_OWNERSHIP or "shared ownership" in qualifier_source:
        qualifier = PriceQualifier.SHARED_OWNERSHIP_FROM if qualifier == PriceQualifier.UNKNOWN else qualifier

    return (
        Price(amount_pence=amount_pence, qualifier=qualifier, raw=raw or (qualifier_raw or "")),
        None,
    )


def _detect_qualifier(lowered: str) -> PriceQualifier:
    for phrase, qualifier in sorted(_QUALIFIER_MAP.items(), key=lambda kv: -len(kv[0])):
        if phrase in lowered:
            return qualifier
    return PriceQualifier.UNKNOWN


def _detect_rent_period(lowered: str) -> RentPeriod:
    for phrase, period in _RENT_PERIOD_MAP.items():
        if phrase in lowered:
            return period
    return RentPeriod.UNKNOWN


def _detect_tenure(blob_lower: str) -> Tenure:
    lowered = blob_lower.lower()
    for phrase, tenure in sorted(
        _TENURE_TOKEN_MAP.items(), key=lambda kv: -len(kv[0])
    ):
        if phrase in lowered:
            return tenure
    return Tenure.UNKNOWN


def _detect_features(*, blob: str, url: str | None) -> list[ListingFeature]:
    lowered = blob.lower()
    features: list[ListingFeature] = []
    if url and "/new-homes/" in url:
        features.append(ListingFeature.NEW_HOME)
    for token, feature in _FEATURE_TOKEN_MAP.items():
        if token in lowered and feature not in features:
            features.append(feature)
    return list(dict.fromkeys(features))


def _infer_property_type(raw: str) -> PropertyType:
    lowered = raw.lower()
    for phrase, ptype in _PROPERTY_TYPE_HINTS:
        if phrase in lowered:
            return ptype
    return PropertyType.OTHER


def _extract_full_postcode(text: str) -> str | None:
    match = _POSTCODE_RE.search(text.upper())
    if match and match.group(2):
        return f"{match.group(1)} {match.group(2)}"
    return None


def _extract_postcode_outcode(text: str) -> str | None:
    match = _POSTCODE_RE.search(text.upper())
    return match.group(1) if match else None


def _extract_price_pence(raw: str) -> int | None:
    match = _PRICE_AMOUNT_RE.search(raw)
    if not match:
        return None
    cleaned = match.group(1).replace(",", "")
    try:
        amount_pounds = float(cleaned)
    except ValueError:
        return None
    return round(amount_pounds * 100)


def _parse_int(raw: str | None) -> int | None:
    if not raw:
        return None
    cleaned = raw.replace(",", "")
    match = _INT_RE.search(cleaned)
    return int(match.group(1)) if match else None


def _first_text(root: Node, selectors: list[str]) -> str | None:
    for selector in selectors:
        node = root.css_first(selector)
        if node is None:
            continue
        text = node.text(strip=True)
        if text:
            return text
    return None


def _clean_whitespace(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def _absolutize(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return _ZOOPLA_ORIGIN + href
    return href


def _strip_query(url: str) -> str:
    if "?" in url:
        url = url.split("?", 1)[0]
    if "#" in url:
        url = url.split("#", 1)[0]
    if not url.endswith("/"):
        url += "/"
    return url


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s).astimezone() if "+" in s or "Z" in s else datetime.fromisoformat(s)
    except ValueError:
        return None


def _ld_property_value_int(ld: dict, name: str) -> int | None:
    props = ld.get("additionalProperty") or []
    if not isinstance(props, list):
        return None
    for prop in props:
        if not isinstance(prop, dict):
            continue
        if prop.get("name") == name:
            value = prop.get("value")
            if isinstance(value, (int, float)):
                return int(value)
            if isinstance(value, str):
                return _parse_int(value)
    return None


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value).strip() or None


def _derive_address_from_ld_name(name: str | None) -> str | None:
    """JSON-LD ``name`` is '<beds> bed <type> for sale <address>' — split off address."""
    if not name:
        return None
    lowered = name.lower()
    for marker in (" for sale ", " to rent ", " for rent "):
        idx = lowered.find(marker)
        if idx != -1:
            return name[idx + len(marker) :].strip() or None
    return None


def _build_raw_fields(**fields: str | int | None) -> dict[str, str]:
    return {k: str(v) for k, v in fields.items() if v not in (None, "")}


# ── Next.js payload fallback ────────────────────────────────────────────────
#
# Zoopla is a Next.js 13+ app. The server renders each page, then streams an
# RSC payload via repeated ``self.__next_f.push([N, "<chunk>"])`` script
# blocks at the bottom of the document. That payload contains the canonical
# listing data (price, address, photos, floorplans, coords). We use it as a
# belt-and-braces fallback for CSS-based extraction: if a selector drifts
# after a deploy, we can still recover the field from this stream.

_NEXT_F_CHUNK_RE: Final = re.compile(
    r"self\.__next_f\.push\(\[\d+\s*,\s*(\"(?:\\.|[^\"\\])*\")\]\)",
    re.DOTALL,
)
_PRICE_ACTUAL_RE: Final = re.compile(r'"priceActual"\s*:\s*(\d+(?:\.\d+)?)')
_PRICE_QUALIFIER_RE: Final = re.compile(r'"priceQualifier"\s*:\s*"([^"]+)"')
_PRICE_LABEL_RE: Final = re.compile(r'"label"\s*:\s*"(£[^"]+)"')
_DISPLAY_ADDRESS_RE: Final = re.compile(
    r'"displayAddress"\s*:\s*"((?:\\.|[^"\\])+)"'
)
_IMAGE_FILENAME_RE: Final = re.compile(r'"filename"\s*:\s*"([a-f0-9]+\.jpe?g)"')
_FLOORPLAN_BLOCK_RE: Final = re.compile(
    r'"floorPlan"\s*:\s*\[(.*?)\]', re.DOTALL
)
_FLOORPLAN_ORIGINAL_RE: Final = re.compile(r'"original"\s*:\s*"(https?://[^"]+)"')
_SOURCE_URL_RE: Final = re.compile(r'"listingUri"\s*:\s*"([^"]+)"')
_ZOOPLA_CDN_PREFIX: Final = "https://lc.zoocdn.com"


def _parse_zoopla_nextjs_payload(html: str) -> dict[str, object] | None:
    """Best-effort scrape of Zoopla's Next.js RSC stream.

    Returns a dict with optional keys ``price_raw``, ``amount_pence``,
    ``address``, ``image_urls``, ``floorplan_urls``, ``source_url`` when the
    corresponding field is discoverable in the hydration payload. Returns
    ``None`` when no ``__next_f`` chunk is present at all.
    """
    if "__next_f" not in html:
        return None

    chunks: list[str] = []
    for match in _NEXT_F_CHUNK_RE.finditer(html):
        quoted = match.group(1)
        try:
            chunks.append(json.loads(quoted))
        except json.JSONDecodeError:
            continue
    if not chunks:
        return None
    blob = "".join(chunks)

    result: dict[str, object] = {}

    price_match = _PRICE_ACTUAL_RE.search(blob)
    if price_match:
        try:
            amount = float(price_match.group(1))
            result["amount_pence"] = round(amount * 100)
            result["price_raw"] = f"£{int(amount):,}"
        except ValueError:
            pass
    if "price_raw" not in result:
        label_match = _PRICE_LABEL_RE.search(blob)
        if label_match:
            result["price_raw"] = label_match.group(1)
    qualifier_match = _PRICE_QUALIFIER_RE.search(blob)
    if qualifier_match:
        result["price_qualifier"] = qualifier_match.group(1)

    addr_match = _DISPLAY_ADDRESS_RE.search(blob)
    if addr_match:
        try:
            result["address"] = json.loads(f'"{addr_match.group(1)}"')
        except json.JSONDecodeError:
            result["address"] = addr_match.group(1)

    url_match = _SOURCE_URL_RE.search(blob)
    if url_match:
        path = url_match.group(1)
        if path.startswith("/"):
            result["source_url"] = _ZOOPLA_ORIGIN + path

    floorplan_urls: set[str] = set()
    fp_block = _FLOORPLAN_BLOCK_RE.search(blob)
    if fp_block:
        for orig in _FLOORPLAN_ORIGINAL_RE.finditer(fp_block.group(1)):
            floorplan_urls.add(orig.group(1))
    if floorplan_urls:
        result["floorplan_urls"] = floorplan_urls

    image_urls: list[str] = []
    seen_filenames: set[str] = set()
    for m in _IMAGE_FILENAME_RE.finditer(blob):
        filename = m.group(1)
        if filename in seen_filenames:
            continue
        seen_filenames.add(filename)
        image_urls.append(f"{_ZOOPLA_CDN_PREFIX}/{filename}")
        if len(image_urls) >= 30:
            break
    if image_urls:
        result["image_urls"] = image_urls

    nts_entries = _extract_nts_entries(blob)
    if nts_entries:
        result["nts_entries"] = nts_entries

    ad_targeting = _extract_ad_targeting(blob)
    if ad_targeting:
        result["ad_targeting"] = ad_targeting

    contact_agent = _extract_contact_agent(blob)
    if contact_agent:
        result["contact_agent"] = contact_agent

    epc_block = _extract_epc_block(blob)
    if epc_block:
        result["epc_block"] = epc_block

    return result or None


# ── NTS / Material-Info helpers (built on the RSC blob) ─────────────────────
#
# Zoopla inlines its Material Information bundle as a JSON array of
# ``{"title":..., "key":..., "value":...}`` objects. Keys we see in the wild:
# tenure, council_tax_band, broadband, broadband_speed, mobile_coverage,
# parking, restrictions, rights_and_easements, water, heating, electricity,
# sewerage, and (for leasehold) ground_rent, service_charge, lease_length,
# review_date. Values are almost always strings; occasionally numbers.
_NTS_ENTRY_RE: Final = re.compile(
    r'\{"title":"(?P<title>[^"]+)","(?:value"|key":")'
    r'(?:[^"]+",")?'
    r'(?P<prefix>key|value)":"(?P<prefix_value>[^"]*)",'
    r'"(?P<suffix>value|key)":"(?P<suffix_value>[^"]*)"'
)
_NTS_SIMPLE_RE: Final = re.compile(
    r'\{"title":"(?P<title>[^"]+)","value":"(?P<value>[^"]*)","key":"(?P<key>[^"]+)"'
)
_NTS_KEY_FIRST_RE: Final = re.compile(
    r'\{"title":"(?P<title>[^"]+)","key":"(?P<key>[^"]+)","value":"(?P<value>[^"]*)"'
)


def _extract_nts_entries(blob: str) -> dict[str, str]:
    """Pull all ``{"title":..., "key":..., "value":...}`` objects into a flat dict.

    The two orderings (``value``-then-``key`` vs ``key``-then-``value``) both
    occur in the same page — we try each regex and merge. Later entries win
    so tenure in the adTargeting-adjacent bundle overrides an earlier generic
    one. Values are kept as raw strings — callers normalize them.
    """
    entries: dict[str, str] = {}
    for m in _NTS_SIMPLE_RE.finditer(blob):
        key = m.group("key")
        value = m.group("value")
        if key and value and key not in entries:
            entries[key] = value
    for m in _NTS_KEY_FIRST_RE.finditer(blob):
        key = m.group("key")
        value = m.group("value")
        if key and value and key not in entries:
            entries[key] = value
    return entries


_AD_TARGETING_RE: Final = re.compile(
    r'"adTargeting":\{[^{}]*"branchId":(?P<branch_id>\d+)[^{}]*?'
    r'"branchName":"(?P<branch_name>[^"]+)"[^{}]*?'
    r'"brandName":"(?P<brand_name>[^"]+)"[^{}]*?'
    r'"companyId":(?P<company_id>\d+)[^{}]*?'
    r'"groupId":(?P<group_id>\d+)[^{}]*?'
    r'(?:"listingStatus":"(?P<listing_status>[^"]+)")?',
    re.DOTALL,
)


def _extract_ad_targeting(blob: str) -> dict[str, object]:
    """Mine the ``adTargeting`` block for branch metadata.

    Zoopla renders this as analytics scaffolding, but it's the most complete
    single source for branch id + brand + listing status. Falls back silently
    if Zoopla changes the shape.
    """
    m = _AD_TARGETING_RE.search(blob)
    if not m:
        return {}
    out: dict[str, object] = {
        "branchId": int(m.group("branch_id")),
        "branchName": m.group("branch_name"),
        "brandName": m.group("brand_name"),
        "companyId": int(m.group("company_id")),
        "groupId": int(m.group("group_id")),
    }
    status = m.group("listing_status")
    if status:
        out["listingStatus"] = status
    ten_match = re.search(r'"tenure":"([^"]+)"', blob[m.start() : m.end() + 200])
    if ten_match:
        out["tenure"] = ten_match.group(1)
    return out


_CONTACT_AGENT_ANCHOR_RE: Final = re.compile(
    r'"aria-label":"Contact agent"',
    re.DOTALL,
)
_CONTACT_BRANCH_ID_RE: Final = re.compile(r'"branchId":(?P<branch_id>\d+)')
_CONTACT_GROUP_ID_RE: Final = re.compile(r'"groupId":(?P<group_id>\d+)')
_CONTACT_LISTING_ID_RE: Final = re.compile(r'"listingId":"(?P<listing_id>\d+)"')
# The outer branch name + phone only appear in the canonical order
# ``"logo":{"src":"..."},"name":"...","number":"..."`` — nested ``ecommerce``
# objects have their own ``name`` key but never a ``logo``-then-``name`` bridge.
_CONTACT_LOGO_NAME_NUMBER_RE: Final = re.compile(
    r'"logo":\{"src":"(?P<logo>[^"]+)"\},'
    r'"name":"(?P<name>[^"]+)","number":"(?P<phone>[^"]+)"'
)
_CONTACT_URL_BLOCK_RE: Final = re.compile(
    r'"url":\{"contact":"(?P<contact_url>[^"]+)","listings":"(?P<listings_url>[^"]+)"'
)


def _extract_contact_agent(blob: str) -> dict[str, object]:
    """Pull branch id + name + phone + enquiry URL from the 'Contact agent' sidebar.

    The sidebar's React props embed the actionable data we need for
    ``send_inquiry`` later (branch id, enquiry URL path) as well as the
    phone number that Zoopla doesn't expose anywhere in the DOM outside this
    one block. We anchor on ``"aria-label":"Contact agent"`` and then extract
    each field with its own narrow regex — the block contains nested objects
    (``ecommerce``, ``text``) that break single "skip-over-anything" patterns.
    """
    anchor = _CONTACT_AGENT_ANCHOR_RE.search(blob)
    if not anchor:
        return {}
    # The sidebar React tree sits within ~3kB of the anchor — keep a generous
    # window but bounded so we don't bleed into adjacent modules.
    section = blob[anchor.start() : anchor.start() + 3000]

    result: dict[str, object] = {}
    if m := _CONTACT_BRANCH_ID_RE.search(section):
        result["branchId"] = int(m.group("branch_id"))
    if m := _CONTACT_GROUP_ID_RE.search(section):
        result["groupId"] = int(m.group("group_id"))
    if m := _CONTACT_LISTING_ID_RE.search(section):
        result["listingId"] = m.group("listing_id")
    if m := _CONTACT_LOGO_NAME_NUMBER_RE.search(section):
        result["logo"] = {"src": m.group("logo")}
        result["name"] = m.group("name")
        result["number"] = m.group("phone")
    if m := _CONTACT_URL_BLOCK_RE.search(section):
        result["url"] = {
            "contact": m.group("contact_url"),
            "listings": m.group("listings_url"),
        }
    # Anchor match alone isn't useful — require at least one identifying field.
    if "branchId" not in result and "name" not in result:
        return {}
    return result


_EPC_SUMMARY_RE: Final = re.compile(
    r'"title":"EPC Rating","text":"EPC Rating:\s*(?P<band>[A-G])"'
)


def _extract_epc_block(blob: str) -> dict[str, str] | None:
    m = _EPC_SUMMARY_RE.search(blob)
    if not m:
        return None
    return {"current": m.group("band"), "raw": m.group(0)}


# ── Detail-page structured helpers ──────────────────────────────────────────


_TIMELINE_KIND_MAP: Final[dict[str, PropertyTimelineEventKind]] = {
    "listed": PropertyTimelineEventKind.LISTED,
    "relisted": PropertyTimelineEventKind.RELISTED,
    "reduced": PropertyTimelineEventKind.REDUCED,
    "increased": PropertyTimelineEventKind.INCREASED,
    "under offer": PropertyTimelineEventKind.UNDER_OFFER,
    "sold stc": PropertyTimelineEventKind.SOLD_STC,
    "sold (stc)": PropertyTimelineEventKind.SOLD_STC,
    "sold subject to contract": PropertyTimelineEventKind.SOLD_STC,
    "sold": PropertyTimelineEventKind.SOLD,
    "let agreed": PropertyTimelineEventKind.LET_AGREED,
    "withdrawn": PropertyTimelineEventKind.WITHDRAWN,
}

_MONTH_NAMES: Final[dict[str, int]] = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

_MONTH_YEAR_RE: Final = re.compile(
    r"^(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<year>\d{4})$",
    re.IGNORECASE,
)
_DAY_MONTH_YEAR_RE: Final = re.compile(
    r"^(?P<day>\d{1,2})\s+(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+(?P<year>\d{4})$",
    re.IGNORECASE,
)
_CHANGE_PENCE_RE: Final = re.compile(r"£([\d,]+)")
_CHANGE_PCT_RE: Final = re.compile(r"\(([\d.]+)%\)")


def _parse_detail_timeline(tree: HTMLParser) -> list[PropertyTimelineEvent]:
    """Extract the "Property timeline" block from a detail page.

    Zoopla renders the timeline inside ``<section aria-labelledby="timeline">``
    with one ``Timeline_timelineListItem__<hash>`` per event. Each item has:
    a badge (Reduced / Listed / Sold / etc.), a date string ("February 2026"
    or the day-of-month variant for recent events), a price, and optionally
    a ``Timeline_timelineChange__`` node carrying the delta (``£50,000 (7.7%)``
    with a directional arrow icon).
    """
    container = tree.css_first(
        'ul[class*="Timeline_timelineList"]'
    ) or tree.css_first('section[aria-labelledby="timeline"] ul')
    if container is None:
        return []

    events: list[PropertyTimelineEvent] = []
    prev_price_pence: int | None = None
    for li in container.css('li[class*="Timeline_timelineListItem"]'):
        badge_el = li.css_first('[class*="Timeline_timelineBadge"]')
        date_el = li.css_first('[class*="Timeline_timelineDate"]')
        price_el = li.css_first('[class*="Timeline_timelinePrice"]')
        change_el = li.css_first('[class*="Timeline_timelineChange"]')
        if date_el is None:
            continue

        badge_text = _clean_whitespace(badge_el.text(strip=True)) if badge_el else None
        date_text = _clean_whitespace(date_el.text(strip=True)) or ""
        price_text = (
            _clean_whitespace(price_el.text(strip=True)) if price_el else None
        )
        change_text = (
            _clean_whitespace(change_el.text(strip=True)) if change_el else None
        )

        kind = _TIMELINE_KIND_MAP.get(
            (badge_text or "").lower().strip(),
            PropertyTimelineEventKind.UNKNOWN,
        )
        occurred = _parse_timeline_date(date_text)
        price_pence = _extract_price_pence(price_text) if price_text else None

        change_pence: int | None = None
        change_pct: float | None = None
        if change_text:
            if (pm := _CHANGE_PENCE_RE.search(change_text)):
                try:
                    change_pence = int(pm.group(1).replace(",", "")) * 100
                except ValueError:
                    change_pence = None
            if (pctm := _CHANGE_PCT_RE.search(change_text)):
                try:
                    change_pct = float(pctm.group(1))
                except ValueError:
                    change_pct = None
            # Reduced events are directional — negate the sign explicitly.
            if kind == PropertyTimelineEventKind.REDUCED:
                if change_pence is not None and change_pence > 0:
                    change_pence = -change_pence
                if change_pct is not None and change_pct > 0:
                    change_pct = -change_pct
        elif (
            prev_price_pence is not None
            and price_pence is not None
            and kind in {PropertyTimelineEventKind.REDUCED, PropertyTimelineEventKind.INCREASED}
        ):
            change_pence = price_pence - prev_price_pence
            if prev_price_pence:
                change_pct = round(100 * change_pence / prev_price_pence, 2)

        raw_parts = [p for p in (badge_text, date_text, price_text, change_text) if p]
        events.append(
            PropertyTimelineEvent(
                kind=kind,
                occurred_at=occurred,
                occurred_at_text=date_text,
                price_pence=price_pence,
                change_pence=change_pence,
                change_pct=change_pct,
                raw=" | ".join(raw_parts),
            )
        )
        if price_pence is not None:
            prev_price_pence = price_pence

    return events


def _parse_timeline_date(text: str) -> date | None:
    """Parse 'February 2026' or '13 February 2026' into a ``date`` (day 1 for month-only)."""
    text = text.strip()
    if not text:
        return None
    if (m := _DAY_MONTH_YEAR_RE.match(text)):
        day = int(m.group("day"))
        month = _MONTH_NAMES.get(m.group("month").lower())
        year = int(m.group("year"))
        if month:
            try:
                return date(year, month, day)
            except ValueError:
                return None
    if (m := _MONTH_YEAR_RE.match(text)):
        month = _MONTH_NAMES.get(m.group("month").lower())
        year = int(m.group("year"))
        if month:
            try:
                return date(year, month, 1)
            except ValueError:
                return None
    return None


# ── NTS-derived normalization helpers ───────────────────────────────────────


def _nts_council_tax_band(entries: dict[str, str]) -> str | None:
    raw = entries.get("council_tax_band")
    if not raw:
        return None
    stripped = raw.strip().upper()
    if len(stripped) == 1 and "A" <= stripped <= "I":
        return stripped
    match = re.search(r"\b([A-I])\b", stripped)
    return match.group(1) if match else None


def _nts_broadband(entries: dict[str, str]) -> BroadbandSpeed | None:
    tech_raw = entries.get("broadband")
    speed_raw = entries.get("broadband_speed")
    raw_bits = [v for v in (tech_raw, speed_raw) if v]
    if not raw_bits:
        return None

    tech = tech_raw.strip() if tech_raw else None
    tech_upper = (tech or "").upper()
    technology = tech if tech and tech.lower() != "ask agent" else None

    mbps: int | None = None
    if speed_raw:
        match = re.search(r"(\d{1,5})\s*Mbps", speed_raw, re.IGNORECASE)
        if match:
            try:
                mbps = int(match.group(1))
            except ValueError:
                mbps = None

    tier = BroadbandTier.UNKNOWN
    if mbps is not None:
        if mbps >= 1000:
            tier = BroadbandTier.GIGABIT
        elif mbps >= 300:
            tier = BroadbandTier.ULTRAFAST
        elif mbps >= 30:
            tier = BroadbandTier.SUPERFAST
        else:
            tier = BroadbandTier.BASIC
    elif "FTTP" in tech_upper or "GIGABIT" in tech_upper:
        tier = BroadbandTier.ULTRAFAST
    elif "FTTC" in tech_upper:
        tier = BroadbandTier.SUPERFAST
    elif "ADSL" in tech_upper or "DSL" in tech_upper:
        tier = BroadbandTier.BASIC

    return BroadbandSpeed(
        tier=tier,
        max_download_mbps=mbps,
        technology=technology,
        raw=" | ".join(raw_bits),
    )


def _nts_mobile_signal(entries: dict[str, str]) -> list[MobileSignal]:
    """Zoopla only surfaces a single 'Mobile coverage' text value (not per-carrier).

    We emit a single ``MobileSignal`` entry with carrier ``'all'`` when the
    disclosure is anything other than ``Ask agent``; OTM is the only portal
    with per-carrier data (handled in its own parser).
    """
    raw = entries.get("mobile_coverage")
    if not raw or raw.strip().lower() in {"", "ask agent"}:
        return []
    level = MobileCoverageLevel.UNKNOWN
    lowered = raw.lower()
    if any(tok in lowered for tok in ("strong", "good", "enhanced", "excellent")):
        level = MobileCoverageLevel.ENHANCED
    elif any(tok in lowered for tok in ("likely", "probable")):
        level = MobileCoverageLevel.LIKELY
    elif any(tok in lowered for tok in ("limited", "patchy", "variable")):
        level = MobileCoverageLevel.LIMITED
    elif any(tok in lowered for tok in ("none", "no signal", "no coverage")):
        level = MobileCoverageLevel.NONE
    return [MobileSignal(carrier="all", voice=level, data=level)]


_MONEY_PER_ANNUM_RE: Final = re.compile(r"£\s*([\d,]+(?:\.\d+)?)\s*(?:per\s+annum|p\.?a\.?|per\s+year|/year|year|annual)", re.IGNORECASE)
_MONEY_ANY_RE: Final = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
_YEARS_RE: Final = re.compile(r"(\d{1,4})\s*(?:years?|yrs?)", re.IGNORECASE)
_PCT_RE: Final = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def _nts_lease(entries: dict[str, str]) -> LeaseTerms | None:
    """Assemble leasehold economics from whatever keys Zoopla exposed.

    Keys seen: ``lease_length`` (e.g. "115 years remaining"), ``ground_rent``
    ("£450 per annum"), ``service_charge`` ("£3,039 per annum"),
    ``review_date`` / ``ground_rent_review`` ("Every 10 years"), and
    occasionally a free-form ``lease_end`` or ``review_percentage``.
    Everything stays optional so freehold properties produce ``None``.
    """
    keys = (
        "lease_length",
        "ground_rent",
        "service_charge",
        "review_date",
        "ground_rent_review",
        "review_period",
        "ground_rent_review_percentage",
        "lease_review_percentage",
    )
    present = {k: entries[k] for k in keys if k in entries and entries[k]}
    if not present:
        return None

    years_remaining: int | None = None
    length_years: int | None = None
    if (ll := present.get("lease_length")):
        years_match = _YEARS_RE.search(ll)
        if years_match:
            n = int(years_match.group(1))
            # Heuristic: <= 999 is "years remaining" in Zoopla's NTS copy;
            # anything higher is treated as total lease length (e.g. 125 / 999).
            if "remaining" in ll.lower() or "left" in ll.lower():
                years_remaining = n
            else:
                length_years = n

    ground_rent_pence: int | None = None
    if (gr := present.get("ground_rent")):
        money = _MONEY_PER_ANNUM_RE.search(gr) or _MONEY_ANY_RE.search(gr)
        if money:
            try:
                ground_rent_pence = round(float(money.group(1).replace(",", "")) * 100)
            except ValueError:
                pass

    service_charge_pence: int | None = None
    if (sc := present.get("service_charge")):
        money = _MONEY_PER_ANNUM_RE.search(sc) or _MONEY_ANY_RE.search(sc)
        if money:
            try:
                service_charge_pence = round(float(money.group(1).replace(",", "")) * 100)
            except ValueError:
                pass

    review_years: int | None = None
    if (rv := present.get("review_date") or present.get("ground_rent_review") or present.get("review_period")):
        years_match = _YEARS_RE.search(rv)
        if years_match:
            review_years = int(years_match.group(1))

    review_pct: float | None = None
    for k in ("ground_rent_review_percentage", "lease_review_percentage"):
        if k in present:
            pct_match = _PCT_RE.search(present[k])
            if pct_match:
                try:
                    review_pct = float(pct_match.group(1))
                except ValueError:
                    pass
            break

    return LeaseTerms(
        years_remaining=years_remaining,
        length_years=length_years,
        ground_rent_pence_per_year=ground_rent_pence,
        ground_rent_review_period_years=review_years,
        ground_rent_review_pct=review_pct,
        service_charge_pence_per_year=service_charge_pence,
        raw={k: v for k, v in present.items()},
    )


def _parse_detail_epc(nextjs: dict[str, object]) -> EnergyRating | None:
    block = nextjs.get("epc_block")
    if not isinstance(block, dict):
        return None
    band = block.get("current")
    if not isinstance(band, str) or len(band) != 1 or not ("A" <= band <= "G"):
        return None
    return EnergyRating(current=band, raw=str(block.get("raw") or f"EPC Rating: {band}"))


def _build_material_information(
    *,
    nts_entries: dict[str, str],
    council_tax_band: str | None,
    tenure: Tenure,
    lease: LeaseTerms | None,
    epc: EnergyRating | None,
    broadband: BroadbandSpeed | None,
    mobile_signal: list[MobileSignal],
) -> MaterialInformation | None:
    """Roll the individual NTS helpers back up into a single structured bundle.

    Returns ``None`` when nothing useful was exposed; otherwise every captured
    field is surfaced so a consumer can render a "Material Information" card
    without re-reading the listing description.
    """
    has_any = any(
        (
            nts_entries,
            council_tax_band,
            tenure != Tenure.UNKNOWN,
            lease is not None,
            epc is not None,
            broadband is not None,
            mobile_signal,
        )
    )
    if not has_any:
        return None

    known_keys = {
        "tenure",
        "council_tax_band",
        "broadband",
        "broadband_speed",
        "mobile_coverage",
        "ground_rent",
        "service_charge",
        "lease_length",
        "review_date",
        "ground_rent_review",
        "review_period",
        "ground_rent_review_percentage",
        "lease_review_percentage",
        "parking",
        "heating",
        "electricity",
        "water",
        "sewerage",
        "restrictions",
        "rights_and_easements",
        "flood_risk",
    }

    return MaterialInformation(
        council_tax_band=council_tax_band,
        tenure=tenure,
        lease=lease,
        epc=epc,
        broadband=broadband,
        mobile_signal=mobile_signal,
        parking_raw=nts_entries.get("parking"),
        heating_raw=nts_entries.get("heating"),
        electricity_raw=nts_entries.get("electricity"),
        water_raw=nts_entries.get("water"),
        sewerage_raw=nts_entries.get("sewerage"),
        restrictions_raw=nts_entries.get("restrictions"),
        rights_and_easements_raw=nts_entries.get("rights_and_easements"),
        flood_risk_raw=nts_entries.get("flood_risk"),
        extra={
            k: v
            for k, v in nts_entries.items()
            if k not in known_keys and v
        },
    )
