"""Parser for Rightmove search and property-detail HTML.

Rightmove's React markup relies heavily on ``data-testid`` attributes, which are
stable across releases. Human-readable class names are often CSS-module hashes;
use ``[class*="PropertyInformation_propertyType"]``-style substring selectors
where class-based targeting is unavoidable.

Detail pages ship the entire React props tree as a single ``window.PAGE_MODEL``
JS object containing ``propertyData`` + ``analyticsInfo`` + ``metadata``. It's
the authoritative source for the enrichment fields (timeline, tenure /
leasehold economics, council tax, broadband, EPC, agent branch + phone). We
extract it with a balanced-brace scan rather than regex because the blob is
large (~100 kB) and contains nested objects a regex can't follow.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
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

_RIGHTMOVE_ORIGIN: Final = "https://www.rightmove.co.uk"

_PROPERTIES_PATH_RE: Final = re.compile(r"/properties/(\d+)")
_AGENT_PATH_RE: Final = re.compile(
    r"/estate-agents/agent/([^/]+)/(.+)-(\d+)\.html", re.IGNORECASE
)

_PROPERTY_CARD_TESTID_RE: Final = re.compile(r"^propertyCard-(\d+)$")

# ── Text-level patterns (aligned with Zoopla parser) ─────────────────────────

_PRICE_AMOUNT_RE: Final = re.compile(r"£\s*([\d,]+(?:\.\d+)?)")
_INT_RE: Final = re.compile(r"(\d+)")

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
    # Note: avoid a bare ``" pa"`` token — it false-matches the word ``page`` on
    # Rightmove detail pages where tooltip copy sits inside the price wrapper.
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
    "featured property": ListingFeature.FEATURED,
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
    """Return de-duplicated canonical Rightmove ``/properties/{id}`` URLs."""
    tree = HTMLParser(html)
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if "/properties/" not in href:
            continue
        if _PROPERTIES_PATH_RE.search(href) is None:
            continue
        canonical = _canonical_property_url(href)
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
    """Parse a Rightmove search-results page into ``SEARCH_CARD`` listings.

    Individual cards that fail :class:`Listing` validation (eg. a future
    schema-drift quirk) are silently skipped so one bad card doesn't nuke
    the whole page — the counter-pressure on silent drop is the
    ``listings live`` / actor-level smoke, which will notice when a run
    consistently yields zero cards.
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


def _detail_headline_price_text(price_el: Node | None) -> str | None:
    """First ``span`` carrying ``£`` — avoids tooltip / glossary copy in the wrapper."""
    if price_el is None:
        return None
    for span in price_el.css("span"):
        t = _clean_whitespace(span.text(strip=True))
        if t and "£" in t:
            return t
    return _clean_whitespace(price_el.text(strip=True))


def parse_detail_page(
    html: str,
    *,
    source_url: str | None = None,
    transaction_type: TransactionType = TransactionType.UNKNOWN,
) -> Listing | None:
    """Parse a Rightmove property detail page into a single ``DETAIL`` listing."""
    tree = HTMLParser(html)
    page_model = _extract_page_model(html) or {}
    property_data = page_model.get("propertyData") if page_model else {}

    url = _detail_canonical_url(tree, source_url)
    if not url:
        return None
    match = _PROPERTIES_PATH_RE.search(url)
    if not match:
        return None
    source_id = match.group(1)

    tx = (
        transaction_type
        if transaction_type != TransactionType.UNKNOWN
        else _transaction_from_url(url, None)
    )

    h1 = tree.css_first("h1")
    address_raw = _clean_whitespace(h1.text(strip=True)) if h1 else None
    if not address_raw:
        return None

    price_el = tree.css_first('[data-testid="primaryPrice"]')
    price_raw = _detail_headline_price_text(price_el)
    qual_el = tree.css_first('[data-testid="priceQualifier"]')
    qualifier_raw = _clean_whitespace(qual_el.text(strip=True)) if qual_el else None

    amount_pence = _extract_price_pence(price_raw) if price_raw else None
    sale_price, rent_price = _materialize_prices(
        raw=price_raw or "",
        qualifier_raw=qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    type_el = tree.css_first('[data-testid="info-reel-PROPERTY_TYPE-text"]')
    property_type_raw = _clean_whitespace(type_el.text(strip=True)) if type_el else None
    property_type = (
        _infer_property_type(property_type_raw.lower())
        if property_type_raw
        else PropertyType.UNKNOWN
    )

    beds_el = tree.css_first('[data-testid="info-reel-BEDROOMS-text"]')
    baths_el = tree.css_first('[data-testid="info-reel-BATHROOMS-text"]')
    beds = _parse_int(_clean_whitespace(beds_el.text(strip=True)) if beds_el else None)
    baths = _parse_int(_clean_whitespace(baths_el.text(strip=True)) if baths_el else None)

    size_el = tree.css_first('[data-testid="info-reel-SIZE-text"]')
    size_raw = _clean_whitespace(size_el.text(strip=True)) if size_el else None
    sqft: int | None = None
    if size_raw and "ask agent" not in size_raw.lower():
        sqft = _parse_int(size_raw)

    tenure_el = tree.css_first('[data-testid="info-reel-tenure-button"]')
    tenure_text = _clean_whitespace(tenure_el.text(strip=True)) if tenure_el else ""
    tenure = _detect_tenure((tenure_text or "").lower())
    if tenure == Tenure.UNKNOWN and page_model:
        tenure = _rm_tenure_from_page_model(page_model)

    title = address_raw
    description = _parse_detail_description(tree)
    image_urls = _parse_detail_property_images(tree, html)
    agent = (
        _rm_agent_from_page_model(page_model) if page_model else None
    ) or _parse_detail_agent(tree)
    coords = extract_uk_coords(html) or (
        _rm_coords_from_page_model(page_model) if page_model else None
    )

    # Prefer PAGE_MODEL for beds/baths when DOM extraction missed — the
    # info-reel markup sometimes hides one of them behind client-side tabs.
    if beds is None and isinstance(property_data, dict):
        if (bd := property_data.get("bedrooms")) is not None:
            with _swallow_type_error():
                beds = int(bd)
    if baths is None and isinstance(property_data, dict):
        if (bt := property_data.get("bathrooms")) is not None:
            with _swallow_type_error():
                baths = int(bt)

    # Enrichment fields — all optional.
    timeline: list[PropertyTimelineEvent] = []
    first_listed_at: date | None = None
    lease: LeaseTerms | None = None
    broadband: BroadbandSpeed | None = None
    epc: EnergyRating | None = None
    council_tax_band: str | None = None
    material: MaterialInformation | None = None
    key_feature_tokens: list[ListingFeature] = []

    if page_model:
        timeline = _rm_timeline_from_page_model(
            page_model,
            current_price_pence=amount_pence,
        )
        # First-listed timestamp is either the LISTED event's date or the
        # ``analyticsProperty.added`` field we already used above.
        listed_events = [
            e for e in timeline if e.kind == PropertyTimelineEventKind.LISTED
        ]
        if listed_events and listed_events[0].occurred_at is not None:
            first_listed_at = listed_events[0].occurred_at
        else:
            analytics = (
                page_model.get("analyticsInfo") or {}
            ).get("analyticsProperty") or {}
            first_listed_at = _parse_rm_added_yyyymmdd(analytics.get("added"))

        lease = _rm_lease_from_page_model(page_model)
        broadband = _rm_broadband_from_page_model(page_model)
        epc = _rm_epc_from_page_model(page_model)
        council_tax_band = (
            (property_data or {}).get("livingCosts") or {}
        ).get("councilTaxBand")
        if council_tax_band and not isinstance(council_tax_band, str):
            council_tax_band = None
        material = _rm_material_information(
            page_model,
            tenure=tenure,
            lease=lease,
            epc=epc,
            broadband=broadband,
        )
        key_feature_tokens = _rm_key_features_as_listing_features(page_model)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    features = _detect_features(
        blob=" ".join(
            filter(
                None,
                [
                    address_raw,
                    price_raw,
                    qualifier_raw,
                    property_type_raw,
                    tenure_text,
                    description[:500] if description else None,
                ],
            )
        ),
        url=url,
    )
    for token in key_feature_tokens:
        if token not in features:
            features.append(token)

    return Listing(
        source=Source.RIGHTMOVE,
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
        first_listed_at=first_listed_at,
        lease=lease,
        broadband=broadband,
        epc=epc,
        council_tax_band=council_tax_band,
        timeline=timeline,
        material_information=material,
        raw_site_fields={
            k: v
            for k, v in {
                "price": price_raw,
                "price_qualifier": qualifier_raw,
                "property_type_raw": property_type_raw,
                "size": size_raw,
            }.items()
            if v
        },
    )


class _swallow_type_error:
    """Context manager that silently swallows ``TypeError`` / ``ValueError``.

    Used when promoting PAGE_MODEL values that are typed as ``int`` but
    occasionally arrive as stringified numbers or ``None`` — we'd rather
    skip the coercion than crash the whole parse.
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        return exc_type in (TypeError, ValueError)


# ── Search card discovery ───────────────────────────────────────────────────


def _find_listing_cards(tree: HTMLParser) -> list[Node]:
    """Prefer ``data-testid="propertyCard-N"`` rows (excludes ``propertyCard-vrt-N``)."""
    cards: list[Node] = []
    for node in tree.css('[data-testid^="propertyCard-"]'):
        tid = node.attributes.get("data-testid") or ""
        if _PROPERTY_CARD_TESTID_RE.match(tid):
            cards.append(node)
    if cards:
        return cards

    synthetic: list[Node] = []
    seen: set[str] = set()
    for anchor in tree.css('a[href*="/properties/"]'):
        href = anchor.attributes.get("href") or ""
        if _PROPERTIES_PATH_RE.search(href) is None:
            continue
        canonical = _canonical_property_url(href)
        if canonical in seen:
            continue
        seen.add(canonical)
        synthetic.append(anchor)
    return synthetic


def _parse_search_card(card: Node, *, hinted_type: TransactionType) -> Listing | None:
    url, raw_href = _find_detail_url_and_raw(card)
    if url is None:
        return None

    source_id_match = _PROPERTIES_PATH_RE.search(url)
    if not source_id_match:
        return None
    source_id = source_id_match.group(1)

    tx = (
        hinted_type
        if hinted_type != TransactionType.UNKNOWN
        else _transaction_from_url(url, raw_href)
    )

    price_node = card.css_first('[data-testid="property-price"]')
    price_blob = _clean_whitespace(price_node.text(strip=True)) if price_node else None
    amount_pence, qualifier_tail = _primary_price_and_qualifier_tail(price_blob)

    qualifier_raw = qualifier_tail
    sale_price, rent_price = _materialize_prices(
        raw=price_blob or "",
        qualifier_raw=qualifier_raw,
        amount_pence=amount_pence,
        transaction_type=tx,
    )

    addr_node = card.css_first('[data-testid="property-address"]')
    address_raw = _clean_whitespace(addr_node.text(strip=True)) if addr_node else None
    if not address_raw:
        return None

    info = card.css_first('[data-testid="property-information"]')
    property_type_raw: str | None = None
    beds: int | None = None
    baths: int | None = None
    if info is not None:
        pt = info.css_first('[class*="PropertyInformation_propertyType"]')
        property_type_raw = _clean_whitespace(pt.text(strip=True)) if pt else None
        bed_el = info.css_first('[class*="PropertyInformation_bedroomsCount"]')
        beds = _parse_int(_clean_whitespace(bed_el.text(strip=True)) if bed_el else None)
        bath_wrap = info.css_first('[class*="PropertyInformation_bathContainer"]')
        if bath_wrap is not None:
            bath_span = bath_wrap.css_first("span")
            baths = _parse_int(
                _clean_whitespace(bath_span.text(strip=True)) if bath_span else None
            )
    # Large counts (>100) are development-block cards ("197 studios available")
    # rather than single properties; keep them as null so the card still emits
    # without tripping the per-listing schema bounds.
    if beds is not None and beds > 100:
        beds = None
    if baths is not None and baths > 100:
        baths = None

    property_type = (
        _infer_property_type(property_type_raw.lower())
        if property_type_raw
        else PropertyType.UNKNOWN
    )

    desc_node = card.css_first('[data-testid="property-description"]')
    summary = _clean_whitespace(desc_node.text(strip=True)) if desc_node else None

    marketed_raw = _clean_whitespace(
        marketed.text(strip=True) if (marketed := card.css_first('[data-testid="marketed-by-text"]')) else None
    )
    status_clean, agent_from_line = _parse_marketed_by_line(marketed_raw)
    link_agent = _parse_agent_from_card(card)
    if agent_from_line and agent_from_line.name:
        if link_agent is not None and link_agent.url is not None:
            agent = Agent(
                name=agent_from_line.name,
                branch=agent_from_line.branch or link_agent.branch,
                url=link_agent.url,
            )
        else:
            agent = agent_from_line
    else:
        agent = link_agent

    features = _detect_features(
        blob=" ".join(
            filter(
                None,
                [
                    address_raw,
                    price_blob,
                    summary,
                    marketed_raw,
                    status_clean,
                ],
            )
        ),
        url=url,
    )

    images, image_count = _parse_card_images(card)

    address = Address(
        raw=address_raw,
        postcode=_extract_full_postcode(address_raw),
        postcode_outcode=_extract_postcode_outcode(address_raw),
    )

    tenure_blob = " ".join(filter(None, [price_blob, summary, marketed_raw]))
    tenure = _detect_tenure(tenure_blob.lower())

    raw_fields = _build_raw_fields(
        price=price_blob,
        price_qualifier=qualifier_raw,
        marketed_by=marketed_raw,
        property_type=property_type_raw,
    )

    return Listing(
        source=Source.RIGHTMOVE,
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
        image_count=image_count,
        agent=agent,
        raw_site_fields=raw_fields,
    )


# ── Field extractors ─────────────────────────────────────────────────────────


def _find_detail_url_and_raw(card: Node) -> tuple[str | None, str | None]:
    for anchor in card.css('a[href*="/properties/"]'):
        href = anchor.attributes.get("href") or ""
        if _PROPERTIES_PATH_RE.search(href):
            return _canonical_property_url(href), href
    if card.tag == "a":
        href = card.attributes.get("href") or ""
        if _PROPERTIES_PATH_RE.search(href):
            return _canonical_property_url(href), href
    return None, None


def _parse_card_images(card: Node) -> tuple[list[Image], int | None]:
    images: list[Image] = []
    seen: set[str] = set()
    for img in card.css('img[src^="https://media.rightmove.co.uk"]'):
        src = img.attributes.get("src") or ""
        if src and src not in seen:
            seen.add(src)
            images.append(Image(url=src))  # type: ignore[arg-type]
            break

    max_idx = 0
    for img_node in card.css('[data-testid^="property-image-"]'):
        tid = img_node.attributes.get("data-testid") or ""
        m = re.search(r"property-image-(\d+)$", tid)
        if m:
            max_idx = max(max_idx, int(m.group(1)))
    image_count = max_idx if max_idx > 0 else None
    if image_count is None and len(images) > 0:
        image_count = 1
    return images, image_count


def _parse_agent_from_card(card: Node) -> Agent | None:
    link = card.css_first('a[href*="/estate-agents/agent/"]')
    if link is None:
        return None
    href = link.attributes.get("href") or ""
    name, branch, agent_url = _parse_agent_href(href)
    return Agent(
        name=name,
        branch=branch,
        url=agent_url,  # type: ignore[arg-type]
    )


def _parse_agent_href(href: str) -> tuple[str | None, str | None, str | None]:
    u = _strip_fragment_and_query(_absolutize(href))
    if not u.startswith("http"):
        return None, None, None
    path = u.replace(_RIGHTMOVE_ORIGIN, "", 1)
    m = _AGENT_PATH_RE.search(path)
    if not m:
        return None, None, u
    name = m.group(1).replace("-", " ").strip()
    branch_slug = m.group(2).replace("-", " ").strip()
    return name, branch_slug, u


def _parse_marketed_by_line(raw: str | None) -> tuple[str | None, Agent | None]:
    """Split ``Reduced on … by Hockeys, Cambridge`` and strip Rightmove's duplicated tail."""
    if not raw:
        return None, None
    s = raw.strip()
    if " by " not in s:
        return None, None
    status, agent_part = s.split(" by ", 1)
    status = status.strip()
    agent_part = agent_part.strip()
    idx = agent_part.lower().rfind(status.lower())
    if idx > 0:
        agent_part = agent_part[:idx].strip().rstrip(",")

    name: str | None = None
    branch: str | None = None
    if "," in agent_part:
        name, branch = (p.strip() for p in agent_part.split(",", 1))
    else:
        name = agent_part or None
    return status, Agent(name=name, branch=branch) if name else None


def _parse_detail_agent(tree: HTMLParser) -> Agent | None:
    link = tree.css_first('a[href*="/estate-agents/agent/"]')
    if link is None:
        return None
    href = link.attributes.get("href") or ""
    name, branch, url = _parse_agent_href(href)
    return Agent(name=name, branch=branch, url=url)  # type: ignore[arg-type]


def _parse_detail_description(tree: HTMLParser) -> str | None:
    for h2 in tree.css("h2"):
        title = _clean_whitespace(h2.text(strip=True))
        if title and title.lower() == "description":
            container = h2.next
            if container is None or container.tag != "div":
                return None
            for junk in container.css("button"):
                junk.decompose()
            return _clean_whitespace(container.text(separator="\n"))
    return None


_RM_FLOORPLAN_SRC_RE: Final = re.compile(
    r"https://media\.rightmove\.co\.uk/[^\"']*(?:FLP_|floorplan)[^\"']*",
    re.IGNORECASE,
)


def _parse_detail_property_images(tree: HTMLParser, html: str) -> list[Image]:
    """Collect photo + floorplan URLs off a Rightmove detail page.

    Photos are fetched from ``<img src="…/property-photo…">`` nodes in the
    DOM. Floorplans live inside a collapsed ``<details>`` panel that
    Rightmove only expands client-side, so their URLs sit in the raw HTML as
    inline JSON ``"floorplans":[{"url":"…FLP_00…"}]`` rather than in real
    ``<img>`` tags. We scan the raw HTML for those URLs and tag them.
    """
    images: list[Image] = []
    seen: set[str] = set()
    for img in tree.css('img[src*="property-photo"]'):
        src = img.attributes.get("src") or ""
        if src.startswith("http") and src not in seen:
            seen.add(src)
            caption = FLOORPLAN_CAPTION if is_floorplan_url(src) else None
            images.append(Image(url=src, caption=caption))  # type: ignore[arg-type]
        if len(images) >= 30:
            break

    for match in _RM_FLOORPLAN_SRC_RE.finditer(html):
        fp_url = match.group(0)
        if fp_url in seen:
            continue
        seen.add(fp_url)
        images.append(Image(url=fp_url, caption=FLOORPLAN_CAPTION))  # type: ignore[arg-type]
        if len(images) >= 30:
            break
    return images


def _detail_canonical_url(tree: HTMLParser, source_url: str | None) -> str | None:
    link = tree.css_first('link[rel="canonical"][href]')
    if link is not None:
        href = link.attributes.get("href") or ""
        if "/properties/" in href:
            return _canonical_property_url(href)
    meta = tree.css_first('meta[property="og:url"][content]')
    if meta is not None:
        content = meta.attributes.get("content") or ""
        if "/properties/" in content:
            return _canonical_property_url(content)
    if source_url and "/properties/" in source_url:
        return _canonical_property_url(source_url)
    for anchor in tree.css('a[href*="/properties/"]'):
        href = anchor.attributes.get("href") or ""
        if _PROPERTIES_PATH_RE.search(href):
            return _canonical_property_url(href)
    return None


def _primary_price_and_qualifier_tail(price_blob: str | None) -> tuple[int | None, str | None]:
    if not price_blob:
        return None, None
    match = _PRICE_AMOUNT_RE.search(price_blob)
    if not match:
        return None, None
    amount = _extract_price_pence(match.group(0))
    tail = price_blob[match.end() :].strip()
    tail = tail or None
    return amount, tail


# ── Value detection (mirrors Zoopla) ─────────────────────────────────────────


def _transaction_from_url(url: str, raw_href: str | None = None) -> TransactionType:
    """Infer sale vs rent from a canonical URL and/or the original href (may contain ``channel=``)."""
    blob = f"{raw_href or ''} {url}".lower()
    if "channel=res_let" in blob or "/property-to-rent/" in blob:
        return TransactionType.RENT
    if (
        "channel=res_buy" in blob
        or "/property-for-sale/" in blob
        or "/new-homes-for-sale/" in blob
    ):
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

    lowered = raw.lower()
    if transaction_type == TransactionType.RENT or any(
        k in lowered for k in _RENT_PERIOD_MAP
    ):
        period = _detect_rent_period(lowered)
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
    if url and "/new-homes-for-sale/" in url:
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
    m = _INT_RE.search(cleaned)
    return int(m.group(1)) if m else None


def _clean_whitespace(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(text.split())
    return cleaned or None


def _strip_fragment_and_query(url: str) -> str:
    if "#" in url:
        url = url.split("#", 1)[0]
    if "?" in url:
        url = url.split("?", 1)[0]
    return url


def _canonical_property_url(href: str) -> str:
    return _strip_fragment_and_query(_absolutize(href))


def _absolutize(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return _RIGHTMOVE_ORIGIN + href
    return href


def _build_raw_fields(**fields: str | int | None) -> dict[str, str]:
    return {k: str(v) for k, v in fields.items() if v not in (None, "")}


# ── PAGE_MODEL extraction ───────────────────────────────────────────────────
#
# Rightmove ships the full React props tree as a single ``window.PAGE_MODEL``
# JS assignment. The blob is too large and too deeply nested for a regex, so
# we find the assignment and then scan for the matching closing brace byte by
# byte (string-aware so escaped quotes don't fool us). Roughly 100 kB per page
# but parses in a few hundred microseconds.

_PAGE_MODEL_RE: Final = re.compile(r"window\.PAGE_MODEL\s*=\s*")


def _extract_page_model(html: str) -> dict[str, Any] | None:
    """Pull ``window.PAGE_MODEL`` out of a detail page and ``json.loads`` it.

    Returns ``None`` when the blob is missing or malformed so callers can
    fall back to DOM-only parsing. Silently swallows JSON errors — a broken
    payload shouldn't nuke the rest of the detail extract.
    """
    match = _PAGE_MODEL_RE.search(html)
    if match is None:
        return None
    start = match.end()
    length = len(html)
    depth = 0
    in_str = False
    esc = False
    i = start
    while i < length:
        ch = html[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(html[start : i + 1])
                    except json.JSONDecodeError:
                        return None
        i += 1
    return None


# ── Rightmove PAGE_MODEL → Listing field helpers ────────────────────────────


_RM_REDUCED_ON_RE: Final = re.compile(
    r"reduced on\s+(\d{1,2}/\d{1,2}/\d{2,4})", re.IGNORECASE
)
_RM_ADDED_ON_RE: Final = re.compile(
    r"(?:added|marketed|new instruction)\s+on\s+(\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)
_RM_SOLD_STC_RE: Final = re.compile(
    r"sold\s+stc(?:\s+on\s+(\d{1,2}/\d{1,2}/\d{2,4}))?", re.IGNORECASE
)


def _parse_rm_date(raw: str) -> date | None:
    """Parse Rightmove's DD/MM/YYYY (or DD/MM/YY) date strings."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_rm_added_yyyymmdd(raw: str | None) -> date | None:
    """Rightmove's ``analyticsProperty.added`` is a ``YYYYMMDD`` string."""
    if not raw or len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def _rm_timeline_from_page_model(
    page_model: dict[str, Any],
    *,
    current_price_pence: int | None,
) -> list[PropertyTimelineEvent]:
    """Build a timeline from Rightmove's ``listingHistory`` + ``analyticsProperty.added``.

    Rightmove exposes only one line of historical context per listing (the
    ``listingUpdateReason``, e.g. ``"Reduced on 13/04/2026"``), plus the
    ``added`` YYYYMMDD in analytics. We lift both into timeline events so
    the canonical schema is uniform across portals.
    """
    events: list[PropertyTimelineEvent] = []
    history = (page_model.get("propertyData") or {}).get("listingHistory") or {}
    reason = history.get("listingUpdateReason")
    if isinstance(reason, str) and reason.strip():
        reason_text = reason.strip()
        kind = PropertyTimelineEventKind.UNKNOWN
        occurred_at: date | None = None
        if m := _RM_REDUCED_ON_RE.search(reason_text):
            kind = PropertyTimelineEventKind.REDUCED
            occurred_at = _parse_rm_date(m.group(1))
        elif m := _RM_SOLD_STC_RE.search(reason_text):
            kind = PropertyTimelineEventKind.SOLD_STC
            if m.group(1):
                occurred_at = _parse_rm_date(m.group(1))
        elif m := _RM_ADDED_ON_RE.search(reason_text):
            kind = PropertyTimelineEventKind.LISTED
            occurred_at = _parse_rm_date(m.group(1))

        events.append(
            PropertyTimelineEvent(
                kind=kind,
                occurred_at=occurred_at,
                occurred_at_text=reason_text,
                price_pence=current_price_pence,
                raw=reason_text,
            )
        )

    analytics = (
        (page_model.get("analyticsInfo") or {}).get("analyticsProperty") or {}
    )
    added_date = _parse_rm_added_yyyymmdd(analytics.get("added"))
    if added_date is not None:
        # Only insert a synthetic "listed" event if we don't already have one;
        # the ``listingUpdateReason`` often encodes the same date as a reduction
        # but the ``added`` field is the canonical first-seen timestamp.
        already_listed = any(
            event.kind == PropertyTimelineEventKind.LISTED for event in events
        )
        if not already_listed:
            events.append(
                PropertyTimelineEvent(
                    kind=PropertyTimelineEventKind.LISTED,
                    occurred_at=added_date,
                    occurred_at_text=added_date.strftime("%d/%m/%Y"),
                    price_pence=None,
                    raw=f"Added on {added_date.strftime('%d/%m/%Y')}",
                )
            )

    return events


def _rm_tenure_from_page_model(page_model: dict[str, Any]) -> Tenure:
    """Read ``propertyData.tenure.tenureType`` → canonical :class:`Tenure`."""
    tenure = ((page_model.get("propertyData") or {}).get("tenure") or {}).get(
        "tenureType"
    )
    if not isinstance(tenure, str):
        return Tenure.UNKNOWN
    return _detect_tenure(tenure.lower())


def _rm_lease_from_page_model(page_model: dict[str, Any]) -> LeaseTerms | None:
    """Assemble :class:`LeaseTerms` from ``tenure`` + ``livingCosts`` blocks.

    Rightmove splits the economics: ``yearsRemainingOnLease`` lives in
    ``propertyData.tenure`` while ground rent / service charge land in
    ``propertyData.livingCosts``. We fuse them into a single canonical object
    only when at least one field is populated — a freehold listing where
    everything is null returns ``None``.
    """
    data = page_model.get("propertyData") or {}
    tenure = data.get("tenure") or {}
    costs = data.get("livingCosts") or {}

    years = tenure.get("yearsRemainingOnLease")
    ground_rent = costs.get("annualGroundRent")
    service_charge = costs.get("annualServiceCharge")
    review_years = costs.get("groundRentReviewPeriodInYears")

    if not any(v not in (None, 0) for v in (years, ground_rent, service_charge)):
        return None

    raw: dict[str, str] = {}
    if isinstance(ground_rent, (int, float)):
        raw["ground_rent"] = f"£{int(ground_rent):,} per annum"
    if isinstance(service_charge, (int, float)):
        raw["service_charge"] = f"£{int(service_charge):,} per annum"
    if isinstance(review_years, (int, float)):
        raw["ground_rent_review"] = f"Every {int(review_years)} years"

    return LeaseTerms(
        years_remaining=int(years) if isinstance(years, (int, float)) else None,
        ground_rent_pence_per_year=int(ground_rent * 100)
        if isinstance(ground_rent, (int, float))
        else None,
        service_charge_pence_per_year=int(service_charge * 100)
        if isinstance(service_charge, (int, float))
        else None,
        ground_rent_review_period_years=int(review_years)
        if isinstance(review_years, (int, float))
        else None,
        raw=raw,
    )


def _rm_epc_from_page_model(
    page_model: dict[str, Any],
) -> EnergyRating | None:
    """Extract the EPC band from ``keyFeatures`` (e.g. ``"EPC B"``).

    Rightmove doesn't ship a structured EPC band — just a PNG in
    ``epcGraphs`` plus occasional free-text in ``keyFeatures``. Parsing the
    text is lossy but gives us something actionable.
    """
    features = (page_model.get("propertyData") or {}).get("keyFeatures") or []
    for feat in features:
        if not isinstance(feat, str):
            continue
        m = re.search(r"\bepc\s*(?:rating)?[:\s]*([A-G])\b", feat, re.IGNORECASE)
        if m:
            band = m.group(1).upper()
            return EnergyRating(current=band, raw=feat.strip())
    return None


def _rm_broadband_from_page_model(
    page_model: dict[str, Any],
) -> BroadbandSpeed | None:
    """Extract broadband speed from features + keyFeatures.

    Rightmove sometimes populates ``propertyData.features.broadband`` with
    an explicit Mbps value; when empty, fall back to free-text in
    ``keyFeatures`` (``"Superfast Broadband Available"``). Returns ``None``
    when neither surface has data so we don't invent speeds we can't prove.
    """
    data = page_model.get("propertyData") or {}
    feat_bb = (data.get("features") or {}).get("broadband") or []
    for f in feat_bb:
        if not isinstance(f, dict):
            continue
        display = f.get("displayText") or ""
        alias = f.get("alias") or ""
        if display or alias:
            tier, tech, speed = _rm_broadband_tier(display)
            return BroadbandSpeed(
                tier=tier,
                technology=tech,
                max_download_mbps=speed,
                raw=display or alias,
            )

    for feat in data.get("keyFeatures") or []:
        if not isinstance(feat, str):
            continue
        lowered = feat.lower()
        if "broadband" in lowered or "fibre" in lowered or "fttp" in lowered:
            tier, tech, speed = _rm_broadband_tier(feat)
            return BroadbandSpeed(
                tier=tier,
                technology=tech,
                max_download_mbps=speed,
                raw=feat.strip(),
            )
    return None


_RM_BROADBAND_SPEED_RE: Final = re.compile(r"(\d+)\s*mbps", re.IGNORECASE)


def _rm_broadband_tier(text: str) -> tuple[BroadbandTier, str | None, int | None]:
    """Infer tier + technology + speed from a single free-text string."""
    lowered = text.lower()
    technology: str | None = None
    if "fttp" in lowered:
        technology = "FTTP"
    elif "fttc" in lowered:
        technology = "FTTC"
    elif "cable" in lowered:
        technology = "Cable"
    elif "adsl" in lowered:
        technology = "ADSL"
    elif "fibre" in lowered or "fiber" in lowered:
        technology = "Fibre"

    speed: int | None = None
    if m := _RM_BROADBAND_SPEED_RE.search(text):
        try:
            speed = int(m.group(1))
        except ValueError:
            speed = None

    if speed is not None:
        if speed >= 1000:
            tier = BroadbandTier.GIGABIT
        elif speed >= 300:
            tier = BroadbandTier.ULTRAFAST
        elif speed >= 30:
            tier = BroadbandTier.SUPERFAST
        else:
            tier = BroadbandTier.BASIC
    elif "ultrafast" in lowered or "fttp" in lowered:
        tier = BroadbandTier.ULTRAFAST
    elif "superfast" in lowered or "fibre" in lowered or "fttc" in lowered:
        tier = BroadbandTier.SUPERFAST
    elif "gigabit" in lowered:
        tier = BroadbandTier.GIGABIT
    else:
        tier = BroadbandTier.UNKNOWN

    return tier, technology, speed


def _rm_material_information(
    page_model: dict[str, Any],
    *,
    tenure: Tenure,
    lease: LeaseTerms | None,
    epc: EnergyRating | None,
    broadband: BroadbandSpeed | None,
) -> MaterialInformation | None:
    """Consolidate Rightmove's scattered fields into one :class:`MaterialInformation`.

    Rightmove is the most permissive of the three portals about *which*
    fields are present; we only mint the model when at least one field is
    non-empty so callers can rely on ``listing.material_information`` being
    meaningful.
    """
    data = page_model.get("propertyData") or {}
    costs = data.get("livingCosts") or {}
    features = data.get("features") or {}

    council_tax_band = costs.get("councilTaxBand") if isinstance(costs, dict) else None
    parking_raw = _first_feature_label(features.get("parking"))
    heating_raw = _first_feature_label(features.get("heating"))
    electricity_raw = _first_feature_label(features.get("electricity"))
    water_raw = _first_feature_label(features.get("water"))
    sewerage_raw = _first_feature_label(features.get("sewerage"))

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
    if parking_raw:
        payload["parking_raw"] = parking_raw
    if heating_raw:
        payload["heating_raw"] = heating_raw
    if electricity_raw:
        payload["electricity_raw"] = electricity_raw
    if water_raw:
        payload["water_raw"] = water_raw
    if sewerage_raw:
        payload["sewerage_raw"] = sewerage_raw

    if not payload:
        return None
    return MaterialInformation(**payload)


def _first_feature_label(entries: Any) -> str | None:
    """Rightmove features are lists of ``{alias, displayText}`` dicts."""
    if not isinstance(entries, list) or not entries:
        return None
    first = entries[0]
    if not isinstance(first, dict):
        return None
    label = first.get("displayText") or first.get("alias")
    if isinstance(label, str) and label.strip():
        return label.strip()
    return None


def _rm_agent_from_page_model(
    page_model: dict[str, Any],
) -> Agent | None:
    """Promote the ``customer`` + ``contactInfo`` blocks into a canonical :class:`Agent`.

    Rightmove is unusually generous here: we get branch id, branch name,
    display address, logo URL, microsite URL, phone number, and brand
    (``companyTradingName``) in one payload. This is what lets us dedupe
    "Hockeys - Cambridge" across portals and pivot to ``get_agent_profile``
    later without needing a second crawl.
    """
    data = page_model.get("propertyData") or {}
    customer = data.get("customer") or {}
    contact = data.get("contactInfo") or {}

    name = customer.get("branchDisplayName") or customer.get("companyName")
    branch = customer.get("branchName")
    group_name = (
        customer.get("companyTradingName") or customer.get("companyName") or None
    )
    branch_id = customer.get("branchId")
    source_id = str(branch_id) if isinstance(branch_id, (int, str)) else None
    address = customer.get("displayAddress") or None
    if isinstance(address, str):
        address = _clean_whitespace(address.replace("\r", " ").replace("\n", " "))

    logo = customer.get("logoPath")
    profile_url = customer.get("customerProfileUrl")
    if isinstance(profile_url, str) and profile_url.startswith("/"):
        url = _RIGHTMOVE_ORIGIN + profile_url
    elif isinstance(profile_url, str) and profile_url.startswith("http"):
        url = profile_url
    else:
        url = None

    phone = None
    tels = contact.get("telephoneNumbers") or {}
    if isinstance(tels, dict):
        phone = tels.get("localNumber") or tels.get("internationalNumber")

    if not any((name, phone, source_id, url, logo)):
        return None

    return Agent(
        name=name if isinstance(name, str) else None,
        phone=phone if isinstance(phone, str) else None,
        email=None,
        branch=branch if isinstance(branch, str) else None,
        address=address,
        url=url if isinstance(url, str) else None,  # type: ignore[arg-type]
        logo_url=logo if isinstance(logo, str) and logo.startswith("http") else None,  # type: ignore[arg-type]
        source_id=source_id,
        group_name=group_name if isinstance(group_name, str) else None,
    )


def _rm_coords_from_page_model(page_model: dict[str, Any]) -> LatLng | None:
    """Use ``analyticsProperty.latitude/longitude`` when available."""
    analytics = (
        (page_model.get("analyticsInfo") or {}).get("analyticsProperty") or {}
    )
    lat = analytics.get("latitude")
    lng = analytics.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        try:
            return LatLng(lat=float(lat), lng=float(lng))
        except ValidationError:
            return None
    return None


def _rm_key_features_as_listing_features(
    page_model: dict[str, Any],
) -> list[ListingFeature]:
    """Distil ``propertyData.keyFeatures`` bullets into :class:`ListingFeature`s.

    Falls back silently when no bullets are present — caller will still
    get the existing text-blob-based detection.
    """
    features = (page_model.get("propertyData") or {}).get("keyFeatures") or []
    blob = " ".join(f for f in features if isinstance(f, str))
    if not blob:
        return []
    return _detect_features(blob=blob, url=None)
