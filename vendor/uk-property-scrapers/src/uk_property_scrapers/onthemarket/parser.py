"""Parser for OnTheMarket search-results and detail HTML.

Selectors were verified against live captures (April 2026 fixtures under
``tests/fixtures/onthemarket/``). OnTheMarket is a Next.js site with Tailwind
utility classes; stable hooks are ``data-component`` attributes, microdata
``itemprop`` values, and the ``result-{id}`` / ``result-{id}-spotlight`` list
item ids on search pages.

Detail pages additionally ship a ``__NEXT_DATA__`` JSON blob containing
``initialReduxState.property`` — the authoritative source for enrichment
fields (numeric broadband Mbps, per-carrier mobile signal, EPC rating,
``keyInfo`` block, agent branch + phone). We extract it once and hand
sub-slices to field-specific builders.

All functions are pure: they accept an HTML ``str`` and return Pydantic models.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Final

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
    LatLng,
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

_OTM_ORIGIN: Final = "https://www.onthemarket.com"

_DETAIL_PATH_RE: Final = re.compile(r"/details/(\d+)(?:/|[?#]|$)")

_TRANSACTION_FROM_URL: Final[dict[str, TransactionType]] = {
    "/for-sale/": TransactionType.SALE,
    "/to-rent/": TransactionType.RENT,
    "/new-homes/": TransactionType.SALE,
}

# ── Text-level patterns (aligned with Zoopla parser helpers) ────────────────

_PRICE_AMOUNT_RE: Final = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
_INT_RE: Final = re.compile(r"(\d+)")

_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)(?:\s+([0-9][A-Z]{2}))?\b"
)

_ARTICLE_TITLE_RE: Final = re.compile(
    r"^View the details for\s+(.+?)\s+-\s+(\d+)\s+bedroom\s+(.+?)\s+for\s+(sale|rent|let)\s*$",
    re.IGNORECASE | re.DOTALL,
)

_H1_SUMMARY_RE: Final = re.compile(
    r"^(\d+)\s+bedroom\s+(.+?)\s+for\s+(sale|rent|let)\s*$",
    re.IGNORECASE,
)

_TITLE_TAG_ADDRESS_RE: Final = re.compile(
    r"\s+in\s+(.+?)\s*\|\s*OnTheMarket\s*$",
    re.IGNORECASE,
)

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
    "shared ownership": PriceQualifier.SHARED_OWNERSHIP_FROM,
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

_TENURE_TOKEN_MAP: Final[dict[str, Tenure]] = {
    "freehold": Tenure.FREEHOLD,
    "leasehold": Tenure.LEASEHOLD,
    "share of freehold": Tenure.SHARE_OF_FREEHOLD,
    "commonhold": Tenure.COMMONHOLD,
    "feuhold": Tenure.FEUHOLD,
}

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
    "new today": ListingFeature.NEW_LISTING,
    "just added": ListingFeature.NEW_LISTING,
    "featured": ListingFeature.FEATURED,
    "spotlight property": ListingFeature.PREMIUM,
    "spotlight": ListingFeature.PREMIUM,
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


# ── Public API ───────────────────────────────────────────────────────────────


def extract_listing_urls(html: str) -> list[str]:
    """Return de-duplicated OnTheMarket listing detail URLs found in the HTML."""
    tree = HTMLParser(html)
    seen: set[str] = set()
    urls: list[str] = []

    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        canonical = _canonical_detail_url_from_href(href)
        if canonical is None:
            continue
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
    """Parse an OnTheMarket search-results page into SEARCH_CARD listings.

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
    """Parse an OnTheMarket property detail page into a single DETAIL Listing.

    Hardening against hydrate-variant drift: OnTheMarket ships three subtly
    different detail-page layouts (standard resale, new-homes spotlight,
    no-agent-photo variant). Rather than chase each layout with a separate
    CSS path, we also parse the ``__NEXT_DATA__`` JSON blob that ships with
    every variant and use it as a fallback for price + address when the
    primary selectors fail.
    """
    tree = HTMLParser(html)
    next_data = _parse_otm_next_data(html)
    redux_property = _extract_otm_redux_property(html)
    url = source_url or _extract_canonical_detail_url(tree)
    if not url and next_data:
        url = next_data.get("source_url")
    if not url:
        return None

    source_id = _extract_listing_id(url)
    if source_id is None:
        return None

    tx = (
        transaction_type
        if transaction_type != TransactionType.UNKNOWN
        else _transaction_from_detail_page(tree, url)
    )

    address_raw = _parse_detail_address(tree)
    if not address_raw:
        address_raw = _parse_detail_address_from_document_title(tree)
    if not address_raw and next_data:
        address_raw = next_data.get("address")
    if not address_raw:
        return None

    h1 = tree.css_first("h1")
    h1_text = _clean_whitespace(h1.text(strip=True)) if h1 else None

    beds, baths, property_type_raw_from_h1, tx_from_h1 = _parse_h1_summary(h1_text)
    if tx == TransactionType.UNKNOWN and tx_from_h1 != TransactionType.UNKNOWN:
        tx = tx_from_h1

    price_raw, qualifier_raw = _parse_detail_price(tree)
    if not price_raw and next_data:
        price_raw = next_data.get("price_raw")
        qualifier_raw = qualifier_raw or next_data.get("price_qualifier")
    amount_pence = _extract_price_pence(price_raw) if price_raw else None
    if amount_pence is None and next_data:
        amount_pence = next_data.get("amount_pence")
    sale_price, rent_price = _materialize_prices(
        raw=price_raw or "",
        qualifier_raw=qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    beds_micro = _parse_int(_first_text(tree, ['[itemprop="numberOfBedrooms"]']))
    if beds_micro is not None:
        beds = beds_micro
    if baths is None:
        baths = _parse_detail_bathrooms(tree)

    if not property_type_raw_from_h1 and h1_text:
        property_type_raw_from_h1 = _parse_property_type_phrase_from_h1(h1_text)

    property_type = (
        _infer_property_type(property_type_raw_from_h1)
        if property_type_raw_from_h1
        else PropertyType.UNKNOWN
    )

    description = _parse_detail_description(tree)
    summary = description[:280] + "…" if description and len(description) > 280 else description

    image_urls = _parse_detail_images(tree, html)
    tenure = _parse_detail_tenure(tree)
    agent = _parse_detail_agent(tree)
    features = _parse_detail_features(tree)
    if features and rent_price is not None and ListingFeature.AUCTION in features:
        features.remove(ListingFeature.AUCTION)

    coords = extract_uk_coords(html)

    # ── Redux-sourced enrichment (April 2026 ``keyInfo``/``broadband``/…) ──
    #
    # The DOM path above still populates a lean Listing; the Redux blob
    # provides canonical values for fields the DOM either hides behind
    # tooltips or doesn't render at all (numeric broadband Mbps, per-carrier
    # mobile signal, council tax band, agent group/branch IDs, and the
    # relative "Reduced yesterday" ribbon used to drive the timeline).
    years_remaining: int | None = None
    lease_terms: LeaseTerms | None = None
    broadband: BroadbandSpeed | None = None
    mobile_signal: list[MobileSignal] = []
    epc_rating: EnergyRating | None = None
    council_tax_band: str | None = None
    timeline: list[PropertyTimelineEvent] = []
    material_info: MaterialInformation | None = None
    raw_site_fields: dict[str, str] = {}

    if redux_property is not None:
        keyinfo = redux_property.get("keyInfo")
        if isinstance(keyinfo, list):
            redux_tenure, years_remaining = _otm_tenure_from_keyinfo(keyinfo)
            if redux_tenure != Tenure.UNKNOWN:
                tenure = redux_tenure
            council_tax_band = _otm_council_tax_band_from_keyinfo(keyinfo)
            lease_terms = _otm_lease_from_keyinfo(
                keyinfo, years_remaining=years_remaining
            )

        broadband = _otm_broadband(redux_property)
        mobile_signal = _otm_mobile_signal(redux_property)
        epc_rating = _otm_epc(redux_property)

        redux_agent = _otm_agent(redux_property)
        if redux_agent is not None:
            # The Redux copy is richer (branch id, group name, full postal
            # address); keep any extra fields the DOM-scraped agent had.
            if agent is not None:
                redux_agent = redux_agent.model_copy(
                    update={
                        "email": redux_agent.email or agent.email,
                        "logo_url": redux_agent.logo_url or agent.logo_url,
                    }
                )
            agent = redux_agent

        redux_coords = _otm_coords(redux_property)
        if redux_coords is not None:
            coords = redux_coords

        # Current amount in pence for a single "reduced" timeline datapoint.
        price_pence_for_timeline = (
            sale_price.amount_pence if sale_price is not None else None
        )
        timeline = _otm_timeline(
            redux_property, current_price_pence=price_pence_for_timeline
        )

        material_info = _otm_material_information(
            redux_property,
            tenure=tenure,
            lease=lease_terms,
            epc=epc_rating,
            broadband=broadband,
            mobile_signal=mobile_signal,
            council_tax_band=council_tax_band,
        )

        # Surface the label ribbon (e.g. "Reduced yesterday", "Featured")
        # verbatim so downstream clients can render the same badge.
        for raw_key in ("daysSinceAddedReduced", "dataLabelId", "premiumText"):
            value = redux_property.get(raw_key)
            if isinstance(value, str) and value.strip():
                raw_site_fields[raw_key] = value.strip()

        derived = _otm_key_features_as_listing_features(redux_property)
        if derived:
            existing = {f for f in features}
            for feature in derived:
                if feature not in existing:
                    features.append(feature)

    raw_site_fields.update(
        {
            k: v
            for k, v in {
                "price": price_raw,
                "price_qualifier": qualifier_raw,
                "property_type_raw": property_type_raw_from_h1,
            }.items()
            if v
        }
    )

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    return Listing(
        source=Source.ONTHEMARKET,
        source_id=source_id,
        source_url=url,  # type: ignore[arg-type]
        listing_type=ListingType.DETAIL,
        transaction_type=tx,
        sale_price=sale_price,
        rent_price=rent_price,
        property_type=property_type,
        property_type_raw=property_type_raw_from_h1,
        bedrooms=beds,
        bathrooms=baths,
        tenure=tenure,
        address=address,
        coords=coords,
        title=h1_text,
        summary=summary,
        description=description,
        features=features,
        image_urls=image_urls,
        agent=agent,
        lease=lease_terms,
        broadband=broadband,
        mobile_signal=mobile_signal,
        epc=epc_rating,
        council_tax_band=council_tax_band,
        timeline=timeline,
        material_information=material_info,
        raw_site_fields=raw_site_fields,
    )


# ── Search card discovery ───────────────────────────────────────────────────


def _find_listing_cards(tree: HTMLParser) -> list[Node]:
    primary = tree.css('ul#maincontent > li[id^="result-"]')
    if primary:
        return [node for node in primary if node.css_first('a[href^="/details/"]')]

    fallback: list[Node] = []
    for node in tree.css('li[id^="result-"]'):
        if node.css_first('article[data-component="search-result-property-card"]'):
            fallback.append(node)
    return fallback


def _parse_search_card(card: Node, *, hinted_type: TransactionType) -> Listing | None:
    url = _find_detail_url(card)
    if url is None:
        return None

    source_id = _source_id_from_li(card) or _extract_listing_id(url)
    if source_id is None:
        return None

    article = card.css_first('article[data-component="search-result-property-card"]') or card
    title_attr = _clean_whitespace(article.attributes.get("title"))
    parsed_title = _parse_article_title_attr(title_attr)

    address_raw = None
    beds: int | None = None
    baths: int | None = None
    property_type_raw: str | None = None
    tx_from_title = TransactionType.UNKNOWN

    if parsed_title:
        address_raw = parsed_title["address"]
        beds = parsed_title["bedrooms"]
        property_type_raw = parsed_title["property_type_raw"]
        tx_from_title = parsed_title["transaction_type"]

    if not address_raw:
        addr_node = card.css_first('address[itemprop="address"] span') or card.css_first(
            'address[itemprop="address"]'
        )
        address_raw = _clean_whitespace(addr_node.text(strip=True)) if addr_node else None

    if not address_raw:
        return None

    tx = hinted_type if hinted_type != TransactionType.UNKNOWN else tx_from_title
    if tx == TransactionType.UNKNOWN:
        tx = _transaction_from_url(url)

    price_el = card.css_first('[data-component="price-title"]')
    price_text = _clean_whitespace(price_el.text(strip=True)) if price_el else None
    price_qualifier_raw: str | None = None
    if price_el is not None:
        inner_div = price_el.css_first("div")
        if inner_div is not None:
            price_qualifier_raw = _clean_whitespace(inner_div.text(strip=True))

    if beds is None or baths is None:
        bb = card.css_first('[data-component="BedBathCounts"]')
        if bb is not None:
            spans = bb.css("span")
            if beds is None and spans:
                beds = _parse_int(spans[0].text(strip=True))
            if baths is None and len(spans) > 1:
                baths = _parse_int(spans[1].text(strip=True))

    if not property_type_raw:
        summary_line = card.css_first("div.text-sm.text-denim")
        raw_line = _clean_whitespace(summary_line.text(strip=True)) if summary_line else None
        if raw_line:
            property_type_raw = _parse_property_type_from_summary_line(raw_line)

    if not property_type_raw:
        property_type_raw = _parse_property_type_phrase_from_h1(address_raw)

    property_type = (
        _infer_property_type(property_type_raw) if property_type_raw else PropertyType.UNKNOWN
    )

    summary_line = card.css_first("div.text-sm.text-denim")
    summary = _clean_whitespace(summary_line.text(strip=True)) if summary_line else None

    tenure = _parse_card_tenure(card)
    pills_text = [p.text(strip=True) for p in card.css('[data-component="pill"]')]
    extra_spans = " ".join(
        _clean_whitespace(s.text(strip=True)) or ""
        for s in card.css(".text-sm.leading-relaxed.text-slate span")
    )
    tenure = tenure if tenure != Tenure.UNKNOWN else _detect_tenure(extra_spans)

    features = _detect_features(
        blob=" ".join(
            filter(
                None,
                [
                    address_raw,
                    price_text or "",
                    summary or "",
                    " ".join(pills_text),
                    extra_spans,
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

    images: list[Image] = []
    img = card.css_first('img[src^="https://media.onthemarket.com/properties/"]')
    if img is not None:
        src = img.attributes.get("src") or ""
        if src.startswith("http"):
            images.append(Image(url=src))  # type: ignore[arg-type]

    agent = _parse_card_agent(card)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    return Listing(
        source=Source.ONTHEMARKET,
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
        tenure=tenure,
        address=address,
        title=address_raw,
        summary=summary,
        features=features,
        image_urls=images,
        agent=agent,
        raw_site_fields=_build_raw_fields(
            price=price_text,
            price_qualifier=price_qualifier_raw,
            property_type=property_type_raw,
            pills=" | ".join(pills_text) if pills_text else None,
        ),
    )


# ── Field extractors ─────────────────────────────────────────────────────────


def _find_detail_url(card: Node) -> str | None:
    article = card.css_first('article[data-component="search-result-property-card"]') or card
    for anchor in article.css('a[href^="/details/"]'):
        href = anchor.attributes.get("href", "") or ""
        canonical = _canonical_detail_url_from_href(href)
        if canonical:
            return canonical
    return None


def _source_id_from_li(card: Node) -> str | None:
    lid = card.attributes.get("id", "") or ""
    if not lid.startswith("result-"):
        return None
    rest = lid[len("result-") :]
    if rest.endswith("-spotlight"):
        rest = rest[: -len("-spotlight")]
    match = _INT_RE.match(rest)
    return match.group(1) if match else None


def _extract_listing_id(url: str) -> str | None:
    match = _DETAIL_PATH_RE.search(url)
    return match.group(1) if match else None


def _extract_canonical_detail_url(tree: HTMLParser) -> str | None:
    for anchor in tree.css('a[href^="/details/"]'):
        href = anchor.attributes.get("href") or ""
        canonical = _canonical_detail_url_from_href(href)
        if canonical and "/photos/" not in canonical:
            return canonical
    return None


def _canonical_detail_url_from_href(href: str) -> str | None:
    if "/details/" not in href:
        return None
    if href.startswith("http") and "onthemarket.com" not in href:
        return None
    match = _DETAIL_PATH_RE.search(href)
    if not match:
        return None
    listing_id = match.group(1)
    return _strip_query(f"{_OTM_ORIGIN}/details/{listing_id}/")


def _parse_article_title_attr(title: str | None) -> dict[str, object] | None:
    if not title:
        return None
    m = _ARTICLE_TITLE_RE.match(title.strip())
    if not m:
        return None
    addr, beds_s, ptype, tx_word = m.group(1), m.group(2), m.group(3), m.group(4).lower()
    tx = TransactionType.RENT if tx_word in {"rent", "let"} else TransactionType.SALE
    return {
        "address": _clean_whitespace(addr),
        "bedrooms": int(beds_s),
        "property_type_raw": ptype.strip().lower(),
        "transaction_type": tx,
    }


def _parse_card_tenure(card: Node) -> Tenure:
    blob = " ".join(
        _clean_whitespace(s.text(strip=True)) or ""
        for s in card.css(".text-sm.leading-relaxed.text-slate span")
    )
    return _detect_tenure(blob)


def _parse_card_agent(card: Node | None) -> Agent | None:
    if card is None:
        return None
    if card.attributes.get("data-component") == "agent-panel":
        panel = card
    else:
        panel = card.css_first('[data-component="agent-panel"]')
    if panel is None:
        return None
    logo = panel.css_first("img[alt]")
    alt = logo.attributes.get("alt") if logo else ""
    alt = alt.strip() if alt else ""
    if not alt:
        return None
    src = logo.attributes.get("src") if logo else None
    name: str | None
    branch: str | None
    if " - " in alt:
        name, branch = (seg.strip() for seg in alt.split(" - ", 1))
    else:
        name, branch = alt or None, None
    phone_el = panel.css_first('a[itemprop="telephone"][href^="tel:"]')
    phone = None
    if phone_el is not None:
        href = phone_el.attributes.get("href") or ""
        if href.lower().startswith("tel:"):
            phone = href[4:].strip() or None
    return Agent(
        name=name,
        branch=branch,
        phone=phone,
        logo_url=src if src and src.startswith("http") else None,  # type: ignore[arg-type]
    )


def _parse_detail_address(tree: HTMLParser) -> str | None:
    prop = tree.css_first('[data-component="property"]')
    if prop is None:
        return None
    for div in prop.css("div.text-slate.text-body2"):
        text = _clean_whitespace(div.text(strip=True))
        if text and _POSTCODE_RE.search(text.upper()):
            return text
    return None


def _parse_detail_address_from_document_title(tree: HTMLParser) -> str | None:
    tnode = tree.css_first("title")
    raw = _clean_whitespace(tnode.text(strip=True)) if tnode else None
    if not raw:
        return None
    m = _TITLE_TAG_ADDRESS_RE.search(raw)
    if m:
        return _clean_whitespace(m.group(1))
    # e.g. "Pepys Court, Cambridge... 2 bed flat for sale - £425,000"
    if "..." in raw:
        return _clean_whitespace(raw.split("...", 1)[0])
    return None


def _parse_h1_summary(h1: str | None) -> tuple[int | None, int | None, str | None, TransactionType]:
    if not h1:
        return None, None, None, TransactionType.UNKNOWN
    m = _H1_SUMMARY_RE.match(h1.strip())
    if not m:
        return None, None, None, TransactionType.UNKNOWN
    beds = int(m.group(1))
    ptype = m.group(2).strip().lower()
    tx_word = m.group(3).lower()
    tx = TransactionType.RENT if tx_word in {"rent", "let"} else TransactionType.SALE
    return beds, None, ptype, tx


def _parse_property_type_phrase_from_h1(text: str) -> str | None:
    m = _H1_SUMMARY_RE.match(text.strip())
    if m:
        return m.group(2).strip().lower()
    return None


def _parse_property_type_from_summary_line(line: str) -> str | None:
    lowered = line.lower()
    for marker in (" for sale", " to rent", " for rent"):
        if marker in lowered:
            before = lowered.split(marker, 1)[0].strip()
            parts = before.split()
            if len(parts) >= 2 and parts[0].isdigit():
                return " ".join(parts[1:])
            return before
    return None


def _parse_detail_price(tree: HTMLParser) -> tuple[str | None, str | None]:
    prop = tree.css_first('[data-component="property"]')
    if prop is not None:
        raw_html = prop.html
        cut = raw_html.find("Key information")
        snippet_html = raw_html if cut == -1 else raw_html[:cut]
        snippet_tree = HTMLParser(snippet_html)
        head = snippet_tree.text(separator=" ", strip=True)
        qual, amt = _split_price_qualifier_amount(head)
        if amt:
            return amt, qual

    title_node = tree.css_first("title")
    title_text = title_node.text(strip=True) if title_node else ""
    if "£" in title_text:
        m = _PRICE_AMOUNT_RE.search(title_text)
        if m:
            raw = _clean_whitespace(m.group(0))
            return raw, None

    blob = " ".join(
        _clean_whitespace(n.text(strip=True)) or ""
        for n in tree.css('h1, h2, meta[name="description"]')
    )
    qual, amt = _split_price_qualifier_amount(blob)
    if amt:
        return amt, qual

    lowered = tree.html.lower()
    for phrase in ("asking price", "guide price", "offers in excess of", "fixed price"):
        idx = lowered.find(phrase)
        if idx == -1:
            continue
        window = tree.html[idx : idx + 120]
        m = _PRICE_AMOUNT_RE.search(window)
        if m:
            return m.group(0).replace(" ", ""), phrase.title()

    return None, None


def _split_price_qualifier_amount(blob: str) -> tuple[str | None, str | None]:
    if not blob:
        return None, None
    lowered = blob.lower()
    qualifier: str | None = None
    for phrase in sorted(_QUALIFIER_MAP.keys(), key=len, reverse=True):
        if phrase in lowered:
            qualifier = phrase.title() if phrase != phrase.lower() else phrase
            break
    m = _PRICE_AMOUNT_RE.search(blob)
    if not m:
        return qualifier, None
    raw = _clean_whitespace(m.group(0))
    return qualifier, raw


def _parse_detail_bathrooms(tree: HTMLParser) -> int | None:
    prop = tree.css_first('[data-component="property"]')
    if prop is None:
        return None
    text = prop.text(separator=" ", strip=True)
    m = re.search(r"(\d+)\s*beds?\s*(\d+)\s*baths?", text, re.IGNORECASE)
    if m:
        return int(m.group(2))
    return None


def _parse_detail_description(tree: HTMLParser) -> str | None:
    node = tree.css_first('[itemprop="description"]')
    if node is None:
        return None
    for junk in node.css("script, style"):
        junk.decompose()
    return _clean_whitespace(node.text(separator="\n"))


_OTM_IMAGE_URL_RE: Final = re.compile(
    r'https://media\.onthemarket\.com/properties/[^"\']+?\.(?:jpg|jpeg|png|webp)'
)


_OTM_SIZE_SUFFIX_RE: Final = re.compile(
    r"-(?:\d+x\d+|original)\.(?:jpg|jpeg|png|webp)$", re.IGNORECASE
)
_OTM_SIZE_PART_RE: Final = re.compile(r"-(\d+)x(\d+)\.", re.IGNORECASE)


def _otm_variant_score(url: str) -> tuple[int, int]:
    """Rank two size variants of the same OTM image.

    Prefers ``-original.*`` (highest fidelity) over any sized variant, then
    largest width*height, then ``.jpg`` over ``.webp`` so downstream code
    paths that don't speak webp still see a working image.
    """
    lowered = url.lower()
    if "-original." in lowered:
        size = 10**9
    else:
        match = _OTM_SIZE_PART_RE.search(url)
        size = int(match.group(1)) * int(match.group(2)) if match else 0
    is_jpg = 1 if lowered.endswith((".jpg", ".jpeg")) else 0
    return (size, is_jpg)


def _parse_detail_images(tree: HTMLParser, html: str) -> list[Image]:
    """Collect every OnTheMarket media URL, tagging floorplan variants.

    OnTheMarket's image grid is carousel-hydrated: gallery URLs live in the
    raw HTML as inline JSON payloads (``"mediumUrl":"…/image-N-1024x1024.jpg"``
    / ``"originalUrl":"…/floor-plan-N-original.jpg"``) rather than rendered
    ``<img>`` tags. We scan every ``media.onthemarket.com/properties/…`` URL
    in the raw HTML, then collapse size variants of the same asset (the CDN
    ships ``-1024x1024``, ``-218x145``, ``-81x55``, ``-original`` + a webp
    mirror of each) to a single canonical URL per asset, picking the highest
    fidelity. Floorplans are tagged via :data:`FLOORPLAN_CAPTION`.
    """
    # Collect every candidate URL, preserving first-seen insertion order so
    # floorplans and late-gallery photos both survive dedup rather than
    # being truncated by an arbitrary per-match cap.
    candidates: list[str] = []
    for img in tree.css('img[src^="https://media.onthemarket.com/properties/"]'):
        src = img.attributes.get("src") or ""
        if src.startswith("http"):
            candidates.append(src)
    for match in _OTM_IMAGE_URL_RE.finditer(html):
        candidates.append(match.group(0))

    # Collapse size variants. Stem = URL without ``-WIDTHxHEIGHT.ext`` suffix,
    # so ``/image-3-1024x1024.jpg``, ``/image-3-218x145.jpg``, and
    # ``/image-3-1024x1024.webp`` all collapse to the same stem.
    best_per_stem: dict[str, str] = {}
    order: list[str] = []
    for url in candidates:
        stem = _OTM_SIZE_SUFFIX_RE.sub("", url)
        current = best_per_stem.get(stem)
        if current is None:
            best_per_stem[stem] = url
            order.append(stem)
        elif _otm_variant_score(url) > _otm_variant_score(current):
            best_per_stem[stem] = url

    images: list[Image] = []
    for stem in order:
        url = best_per_stem[stem]
        caption = FLOORPLAN_CAPTION if is_floorplan_url(url) else None
        images.append(Image(url=url, caption=caption))  # type: ignore[arg-type]
        if len(images) >= 60:
            break

    return images


def _parse_detail_tenure(tree: HTMLParser) -> Tenure:
    prop = tree.css_first('[data-component="property"]')
    chunks: list[str] = []
    if prop is not None:
        chunks.append(prop.text(separator=" ", strip=True).lower())
    chunks.append(tree.html.lower())
    return _detect_tenure(" ".join(chunks))


def _parse_detail_agent(tree: HTMLParser) -> Agent | None:
    panel = tree.css_first('[data-component="agent-panel"]')
    if panel is None:
        return None
    return _parse_card_agent(panel)


def _parse_detail_features(tree: HTMLParser) -> list[ListingFeature]:
    prop = tree.css_first('[data-component="property"]')
    blob = prop.text(separator=" ", strip=True) if prop else ""
    return _detect_features(blob=blob, url=None)


def _transaction_from_url(url: str) -> TransactionType:
    lowered = url.lower()
    for pattern, tx in _TRANSACTION_FROM_URL.items():
        if pattern in lowered:
            return tx
    return TransactionType.UNKNOWN


def _transaction_from_detail_page(tree: HTMLParser, url: str) -> TransactionType:
    tx = _transaction_from_url(url)
    if tx != TransactionType.UNKNOWN:
        return tx
    h1 = tree.css_first("h1")
    h1_text = _clean_whitespace(h1.text(strip=True)) if h1 else None
    _, _, _, tx_h1 = _parse_h1_summary(h1_text)
    if tx_h1 != TransactionType.UNKNOWN:
        return tx_h1
    title_node = tree.css_first("title")
    title_text = (title_node.text(strip=True) if title_node else "").lower()
    if "to rent" in title_text or " to let" in title_text:
        return TransactionType.RENT
    if "for sale" in title_text:
        return TransactionType.SALE
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

    if transaction_type == TransactionType.RENT or any(
        k in raw.lower() for k in _RENT_PERIOD_MAP
    ):
        period = _detect_rent_period(raw.lower())
        return None, RentPrice(
            amount_pence=amount_pence,
            qualifier=qualifier,
            raw=raw or (qualifier_raw or ""),
            period=period,
        )

    if transaction_type == TransactionType.SHARED_OWNERSHIP or "shared ownership" in qualifier_source:
        qualifier = (
            PriceQualifier.SHARED_OWNERSHIP_FROM
            if qualifier == PriceQualifier.UNKNOWN
            else qualifier
        )

    return (
        Price(
            amount_pence=amount_pence,
            qualifier=qualifier,
            raw=raw or (qualifier_raw or ""),
        ),
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
    for phrase, tenure in sorted(_TENURE_TOKEN_MAP.items(), key=lambda kv: -len(kv[0])):
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


def _first_text(root: Node | None, selectors: list[str]) -> str | None:
    if root is None:
        return None
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


def _strip_query(url: str) -> str:
    if "?" in url:
        url = url.split("?", 1)[0]
    if "#" in url:
        url = url.split("#", 1)[0]
    if not url.endswith("/"):
        url += "/"
    return url


def _build_raw_fields(**fields: str | int | None) -> dict[str, str]:
    return {k: str(v) for k, v in fields.items() if v not in (None, "")}


# ── __NEXT_DATA__ hydrate fallback ──────────────────────────────────────────
#
# OnTheMarket is built on Next.js 13 and ships the full server state inside
# a ``<script id="__NEXT_DATA__" type="application/json">`` tag on every
# detail page. We use this as a belt-and-braces fallback for price + address
# + URL when the primary CSS selectors fail (new-homes spotlight variant,
# no-agent-photo variant, or generic selector drift after a deploy).

_NEXT_DATA_RE: Final = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>',
    re.DOTALL,
)
_PRICE_STRING_RE: Final = re.compile(r'"price"\s*:\s*"([\d,]+)"')
_DISPLAY_ADDRESS_RE: Final = re.compile(
    r'"displayAddress"\s*:\s*"([^"]+)"'
)


def _parse_otm_next_data(html: str) -> dict[str, object] | None:
    """Extract headline fields from OTM's ``__NEXT_DATA__`` blob.

    Returns a dict with optional ``address``, ``price_raw``, ``amount_pence``,
    ``source_url``. Returns ``None`` when the blob is absent or unparseable.
    """
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None

    blob = match.group(1)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None

    result: dict[str, object] = {}

    addr_match = _DISPLAY_ADDRESS_RE.search(blob)
    if addr_match:
        try:
            result["address"] = json.loads(f'"{addr_match.group(1)}"')
        except json.JSONDecodeError:
            result["address"] = addr_match.group(1)

    price_match = _PRICE_STRING_RE.search(blob)
    if price_match:
        raw = price_match.group(1)
        try:
            amount = int(raw.replace(",", ""))
            result["amount_pence"] = amount * 100
            result["price_raw"] = f"£{raw}"
        except ValueError:
            pass

    seo = _walk_for_key(data, "seoLinks")
    if isinstance(seo, list):
        for link in seo:
            if isinstance(link, dict) and link.get("rel") == "canonical":
                canonical = link.get("url")
                if isinstance(canonical, str) and "onthemarket.com" in canonical:
                    result["source_url"] = canonical
                    break

    return result or None


def _walk_for_key(data: object, target: str) -> object | None:
    """Depth-first walk of ``data`` returning the first value stored under ``target``.

    OnTheMarket's Next.js payload nests ``seoLinks`` several layers deep
    inside ``props.pageProps.propertyDetails.*``; we avoid hard-coding the
    path so minor layout reshuffles don't kill the fallback.
    """
    if isinstance(data, dict):
        if target in data:
            return data[target]
        for value in data.values():
            found = _walk_for_key(value, target)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _walk_for_key(item, target)
            if found is not None:
                return found
    return None


# ── OTM Redux state extraction ──────────────────────────────────────────────
#
# OnTheMarket detail pages embed the entire React/Redux hydration payload in a
# ``<script id="__NEXT_DATA__">`` tag. ``props.initialReduxState.property``
# is the authoritative source for:
#
#   • ``keyInfo``         – labelled "more information" rows (Tenure, Ground
#                          rent, Service charge, Council tax, Broadband,
#                          Mobile signal)
#   • ``broadband``       – structured {maxDownloadMbps, broadbandType}
#   • ``mobileReception`` – per-carrier signal (ee/o2/three/vodafone)
#   • ``epc``             – {rating, date}
#   • ``agent``           – branch contact + group name + phone + logo
#   • ``location``        – {lat, lon}
#   • ``daysSinceAdded*`` – relative added/reduced label
#   • ``dataLabelId``     – canonical status tag (``reduced``, ``new``, …)


def _extract_otm_redux_property(html: str) -> dict[str, Any] | None:
    """Return the ``initialReduxState.property`` dict, or ``None`` on failure."""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    prop = (
        data.get("props", {})
        .get("initialReduxState", {})
        .get("property")
    )
    return prop if isinstance(prop, dict) else None


# ── OTM enrichment helpers ─────────────────────────────────────────────────


_OTM_KEYINFO_LEASE_YEARS_RE: Final = re.compile(
    r"(\d+)\s*(?:yrs?|years?)\s*left", re.IGNORECASE
)
_OTM_KEYINFO_GROUND_RENT_RE: Final = re.compile(
    r"£\s*([\d,]+)\s*per\s*annum", re.IGNORECASE
)
_OTM_KEYINFO_REVIEW_PERIOD_RE: Final = re.compile(
    r"review\s*period:\s*(?:every\s*)?(\d+)\s*(?:yrs?|years?)", re.IGNORECASE
)
_OTM_KEYINFO_BROADBAND_SPEED_RE: Final = re.compile(
    r"(\d+)\s*mbps", re.IGNORECASE
)

_OTM_TENURE_MAP: Final[dict[str, Tenure]] = {
    "freehold": Tenure.FREEHOLD,
    "leasehold": Tenure.LEASEHOLD,
    "share of freehold": Tenure.SHARE_OF_FREEHOLD,
    "shared freehold": Tenure.SHARE_OF_FREEHOLD,
    "commonhold": Tenure.COMMONHOLD,
    "feuhold": Tenure.FEUHOLD,
}

_OTM_BROADBAND_TYPE_MAP: Final[dict[str, BroadbandTier]] = {
    "basic": BroadbandTier.BASIC,
    "super-fast": BroadbandTier.SUPERFAST,
    "superfast": BroadbandTier.SUPERFAST,
    "ultra-fast": BroadbandTier.ULTRAFAST,
    "ultrafast": BroadbandTier.ULTRAFAST,
    "gigabit": BroadbandTier.GIGABIT,
}

# OTM's signal-level labels are a mixture of traffic-light colours and text.
# We map both to our canonical :class:`MobileCoverageLevel` enum so downstream
# callers can compare across portals without knowing which vocabulary OTM
# happens to use on any given page.
_OTM_SIGNAL_MAP: Final[dict[str, MobileCoverageLevel]] = {
    "green": MobileCoverageLevel.LIKELY,
    "enhanced": MobileCoverageLevel.ENHANCED,
    "likely": MobileCoverageLevel.LIKELY,
    "amber": MobileCoverageLevel.LIMITED,
    "limited": MobileCoverageLevel.LIMITED,
    "red": MobileCoverageLevel.NONE,
    "none": MobileCoverageLevel.NONE,
    "no": MobileCoverageLevel.NONE,
}

_OTM_CARRIERS: Final = ("ee", "o2", "three", "vodafone")


def _otm_keyinfo_lookup(keyinfo: list[Any], title: str) -> dict[str, Any] | None:
    """Find a :class:`keyInfo` entry by its ``title`` (case-insensitive)."""
    if not isinstance(keyinfo, list):
        return None
    for entry in keyinfo:
        if not isinstance(entry, dict):
            continue
        entry_title = entry.get("title")
        if isinstance(entry_title, str) and entry_title.strip().lower() == title.lower():
            return entry
    return None


def _otm_tenure_from_keyinfo(keyinfo: list[Any]) -> tuple[Tenure, int | None]:
    """Read ``Tenure`` out of ``keyInfo`` and return ``(tenure, years_remaining)``.

    OTM formats the value like ``"Leasehold  |  115 yrs left"``. If no
    tenure block is present we return ``(Tenure.UNKNOWN, None)``.
    """
    entry = _otm_keyinfo_lookup(keyinfo, "Tenure")
    if not entry:
        return Tenure.UNKNOWN, None
    value = entry.get("value") or ""
    if not isinstance(value, str):
        return Tenure.UNKNOWN, None
    lowered = value.lower()
    tenure = Tenure.UNKNOWN
    for phrase, resolved in sorted(
        _OTM_TENURE_MAP.items(), key=lambda kv: -len(kv[0])
    ):
        if phrase in lowered:
            tenure = resolved
            break
    years: int | None = None
    if m := _OTM_KEYINFO_LEASE_YEARS_RE.search(value):
        try:
            years = int(m.group(1))
        except ValueError:
            years = None
    return tenure, years


def _otm_lease_from_keyinfo(
    keyinfo: list[Any], *, years_remaining: int | None
) -> LeaseTerms | None:
    """Assemble :class:`LeaseTerms` from the relevant ``keyInfo`` rows."""
    ground_rent_entry = _otm_keyinfo_lookup(keyinfo, "Ground rent")
    service_charge_entry = _otm_keyinfo_lookup(keyinfo, "Service charge")

    ground_rent_pence: int | None = None
    ground_rent_raw: str | None = None
    ground_rent_review_period: int | None = None
    if ground_rent_entry and isinstance(ground_rent_entry.get("value"), str):
        ground_rent_raw = ground_rent_entry["value"]
        if m := _OTM_KEYINFO_GROUND_RENT_RE.search(ground_rent_raw):
            try:
                ground_rent_pence = int(m.group(1).replace(",", "")) * 100
            except ValueError:
                ground_rent_pence = None
        if m := _OTM_KEYINFO_REVIEW_PERIOD_RE.search(ground_rent_raw):
            try:
                ground_rent_review_period = int(m.group(1))
            except ValueError:
                ground_rent_review_period = None

    service_charge_pence: int | None = None
    service_charge_raw: str | None = None
    if service_charge_entry and isinstance(service_charge_entry.get("value"), str):
        service_charge_raw = service_charge_entry["value"]
        if m := _OTM_KEYINFO_GROUND_RENT_RE.search(service_charge_raw):
            try:
                service_charge_pence = int(m.group(1).replace(",", "")) * 100
            except ValueError:
                service_charge_pence = None

    if not any(
        (
            years_remaining,
            ground_rent_pence,
            service_charge_pence,
            ground_rent_review_period,
        )
    ):
        return None

    raw: dict[str, str] = {}
    if ground_rent_raw:
        raw["ground_rent"] = ground_rent_raw
    if service_charge_raw:
        raw["service_charge"] = service_charge_raw

    return LeaseTerms(
        years_remaining=years_remaining,
        ground_rent_pence_per_year=ground_rent_pence,
        service_charge_pence_per_year=service_charge_pence,
        ground_rent_review_period_years=ground_rent_review_period,
        raw=raw,
    )


def _otm_council_tax_band_from_keyinfo(keyinfo: list[Any]) -> str | None:
    """Extract ``Band C`` → ``"C"`` from the ``Council tax`` row."""
    entry = _otm_keyinfo_lookup(keyinfo, "Council tax")
    if not entry or not isinstance(entry.get("value"), str):
        return None
    value = entry["value"]
    m = re.search(r"band\s*([A-Ha-h])", value, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def _otm_broadband(
    property_state: dict[str, Any],
) -> BroadbandSpeed | None:
    """Build :class:`BroadbandSpeed` from the structured ``broadband`` dict + keyInfo."""
    bb = property_state.get("broadband")
    if not isinstance(bb, dict):
        return None

    speed = bb.get("maxDownloadMbps")
    broadband_type = bb.get("broadbandType")
    if not isinstance(speed, (int, float)) and not isinstance(broadband_type, str):
        return None

    tier = BroadbandTier.UNKNOWN
    if isinstance(broadband_type, str):
        tier = _OTM_BROADBAND_TYPE_MAP.get(broadband_type.lower(), BroadbandTier.UNKNOWN)
    if tier == BroadbandTier.UNKNOWN and isinstance(speed, (int, float)):
        if speed >= 1000:
            tier = BroadbandTier.GIGABIT
        elif speed >= 300:
            tier = BroadbandTier.ULTRAFAST
        elif speed >= 30:
            tier = BroadbandTier.SUPERFAST
        else:
            tier = BroadbandTier.BASIC

    # Normalize broadbandType as a "technology" hint when we recognize it.
    technology: str | None = None
    if isinstance(broadband_type, str):
        technology = broadband_type.replace("-", " ").title()

    raw_parts: list[str] = []
    if isinstance(broadband_type, str):
        raw_parts.append(broadband_type)
    if isinstance(speed, (int, float)):
        raw_parts.append(f"{int(speed)}Mbps")

    return BroadbandSpeed(
        tier=tier,
        technology=technology,
        max_download_mbps=int(speed) if isinstance(speed, (int, float)) else None,
        raw=" ".join(raw_parts) or None,
    )


def _otm_mobile_signal(
    property_state: dict[str, Any],
) -> list[MobileSignal]:
    """Build one :class:`MobileSignal` per carrier from ``mobileReception`` dict.

    OnTheMarket is the only portal that exposes per-carrier signal — its
    ``keyInfo`` "Mobile signal" row carries the same payload. We prefer
    the top-level ``mobileReception`` because it's typed consistently.
    """
    reception = property_state.get("mobileReception")
    if not isinstance(reception, dict):
        return []
    signals: list[MobileSignal] = []
    for carrier in _OTM_CARRIERS:
        raw_value = reception.get(carrier)
        if not isinstance(raw_value, str):
            continue
        level = _OTM_SIGNAL_MAP.get(raw_value.strip().lower())
        if level is None:
            continue
        signals.append(
            MobileSignal(
                carrier=carrier,
                voice=level,
                data=level,
            )
        )
    return signals


def _otm_epc(property_state: dict[str, Any]) -> EnergyRating | None:
    """Build :class:`EnergyRating` from ``propertyState.epc.rating``."""
    epc = property_state.get("epc")
    if not isinstance(epc, dict):
        return None
    rating = epc.get("rating")
    if not isinstance(rating, str) or not rating.strip():
        return None
    date_str = epc.get("date") if isinstance(epc.get("date"), str) else None
    raw = f"EPC {rating.upper()}"
    if date_str:
        raw = f"{raw} ({date_str})"
    return EnergyRating(current=rating.strip().upper(), raw=raw)


def _otm_agent(property_state: dict[str, Any]) -> Agent | None:
    """Promote the Redux ``agent`` block into a canonical :class:`Agent`.

    Unlike Rightmove and Zoopla, OnTheMarket exposes the estate agent's
    group / franchise parent under ``groupName`` (e.g. "Connells Group"
    for Abbotts-Cambridge). We preserve it so downstream callers can do
    franchise-level aggregation.
    """
    agent = property_state.get("agent")
    if not isinstance(agent, dict):
        return None

    name = agent.get("name") or agent.get("companyName")
    branch_id = agent.get("branchId")
    source_id = str(branch_id) if isinstance(branch_id, (int, str)) else None
    phone = (
        agent.get("telephoneEnquiries")
        or agent.get("telephone")
        or agent.get("telephoneAppraisals")
    )
    address = agent.get("address")
    if isinstance(address, str):
        address = _clean_whitespace(
            address.replace("\r", " ").replace("\n", " ")
        )

    branch_url = agent.get("detailsUrl")
    if isinstance(branch_url, str) and branch_url.startswith("/"):
        url = _OTM_ORIGIN + branch_url
    elif isinstance(branch_url, str) and branch_url.startswith("http"):
        url = branch_url
    else:
        url = None

    logo = agent.get("logoUrl")
    if not isinstance(logo, str) or not logo.startswith("http"):
        logo_path = agent.get("logoPath")
        if isinstance(logo_path, str) and logo_path:
            # OTM stores logos on ``media.onthemarket.com`` — prepend the CDN
            # origin so callers get a usable URL.
            logo = f"https://media.onthemarket.com/{logo_path.lstrip('/')}"
        else:
            logo = None

    branch_name = None
    addr_line2 = agent.get("addressline2")
    if isinstance(addr_line2, str) and addr_line2.strip():
        branch_name = addr_line2.strip()
    elif isinstance(name, str) and " - " in name:
        branch_name = name.split(" - ", 1)[1].strip() or None

    group_name = agent.get("groupName")
    if not isinstance(group_name, str) or not group_name.strip():
        company = agent.get("companyName")
        group_name = company if isinstance(company, str) and company.strip() else None

    if not any((name, phone, source_id, url)):
        return None
    return Agent(
        name=name if isinstance(name, str) else None,
        phone=phone if isinstance(phone, str) else None,
        email=None,
        branch=branch_name,
        address=address,
        url=url if isinstance(url, str) else None,  # type: ignore[arg-type]
        logo_url=logo,  # type: ignore[arg-type]
        source_id=source_id,
        group_name=group_name,
    )


def _otm_coords(property_state: dict[str, Any]) -> LatLng | None:
    """Read ``propertyState.location`` → :class:`LatLng`."""
    loc = property_state.get("location")
    if not isinstance(loc, dict):
        return None
    lat = loc.get("lat")
    lng = loc.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        try:
            return LatLng(lat=float(lat), lng=float(lng))
        except ValidationError:
            return None
    return None


def _otm_timeline(
    property_state: dict[str, Any], *, current_price_pence: int | None
) -> list[PropertyTimelineEvent]:
    """Distill OTM's ``daysSinceAddedReduced`` label into a timeline event.

    OnTheMarket only surfaces a relative tag ("Reduced yesterday",
    "Added 3 days ago"). Without an absolute date we emit the event with
    ``occurred_at=None`` and stash the label in ``occurred_at_text`` for
    downstream clients that want to display the original copy.
    """
    label = property_state.get("daysSinceAddedReduced")
    if not isinstance(label, str) or not label.strip():
        return []

    lowered = label.lower()
    if "reduced" in lowered:
        kind = PropertyTimelineEventKind.REDUCED
    elif "added" in lowered or "new" in lowered:
        kind = PropertyTimelineEventKind.LISTED
    elif "sold" in lowered and "stc" in lowered:
        kind = PropertyTimelineEventKind.SOLD_STC
    elif "under offer" in lowered:
        kind = PropertyTimelineEventKind.UNDER_OFFER
    else:
        kind = PropertyTimelineEventKind.UNKNOWN

    return [
        PropertyTimelineEvent(
            kind=kind,
            occurred_at=None,
            occurred_at_text=label.strip(),
            price_pence=current_price_pence
            if kind == PropertyTimelineEventKind.REDUCED
            else None,
            raw=label.strip(),
        )
    ]


def _otm_material_information(
    property_state: dict[str, Any],
    *,
    tenure: Tenure,
    lease: LeaseTerms | None,
    epc: EnergyRating | None,
    broadband: BroadbandSpeed | None,
    mobile_signal: list[MobileSignal],
    council_tax_band: str | None,
) -> MaterialInformation | None:
    """Aggregate OTM-specific fields into the cross-portal :class:`MaterialInformation`."""
    payload: dict[str, Any] = {}
    if council_tax_band:
        payload["council_tax_band"] = council_tax_band
    if tenure != Tenure.UNKNOWN:
        payload["tenure"] = tenure
    if lease is not None:
        payload["lease"] = lease
    if epc is not None:
        payload["epc"] = epc
    if broadband is not None:
        payload["broadband"] = broadband
    if mobile_signal:
        payload["mobile_signal"] = mobile_signal
    extra: dict[str, str] = {}
    area_stats = property_state.get("areaStats")
    if isinstance(area_stats, dict):
        avg = area_stats.get("avgHomePrices")
        if isinstance(avg, dict) and isinstance(avg.get("price"), str):
            extra["avg_home_prices"] = avg["price"]
        crimes = area_stats.get("crimes")
        if isinstance(crimes, dict) and isinstance(crimes.get("label"), str):
            extra["crime_rating"] = crimes["label"]
    if extra:
        payload["extra"] = extra

    if not payload:
        return None
    return MaterialInformation(**payload)


def _otm_key_features_as_listing_features(
    property_state: dict[str, Any],
) -> list[ListingFeature]:
    """Distil ``premiumText``/``propertySticker`` / ``dataLabelId`` into features."""
    blob_parts: list[str] = []
    for key in ("premiumText", "propertySticker", "dataLabelId", "labelText"):
        value = property_state.get(key)
        if isinstance(value, str) and value.strip():
            blob_parts.append(value)
    if not blob_parts:
        return []
    return _detect_features(blob=" ".join(blob_parts), url=None)


def _otm_first_listed(property_state: dict[str, Any]) -> date | None:
    """OTM only exposes relative "Reduced yesterday" strings for this event.

    We keep the hook so downstream callers see ``None`` rather than missing
    attribute — makes it easier to wire future parsers that capture the
    absolute date from the newer ``firstAdvertisedAt`` field.
    """
    return None
