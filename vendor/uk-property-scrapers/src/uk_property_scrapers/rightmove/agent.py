"""Parser for Rightmove agent-branch pages (``/estate-agents/agent/...``).

Rightmove's branch page ships the entire React props tree as a JSON blob
inside ``<script id="__NEXT_DATA__">``. We drill into::

    __NEXT_DATA__
      .props.pageProps.data
        .branchProfileResponse.agentProfileResponse = <agent>
        .previousSoldProperties = [<sold card>, ...]
        .previousLetProperties  = [<let card>, ...]

The ``agentProfileResponse`` carries the full public branch profile —
trading name, postcode, bios, main/sales phone numbers, trade-body
``industryAffiliations``, sibling ``networkBranches``, and live stock as
``salesProperties`` / ``lettingsProperties`` objects. Rightmove does not
publish opening hours or a team roster on branch pages, so those fields
stay empty — callers should not treat a missing value as a parser bug.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from pydantic import ValidationError
from selectolax.parser import HTMLParser

from uk_property_scrapers.schema import (
    Address,
    Agent,
    AgentProfile,
    AgentStockSummary,
    Image,
    Listing,
    ListingFeature,
    ListingType,
    Price,
    PriceQualifier,
    PropertyType,
    RentPeriod,
    RentPrice,
    Source,
    TransactionType,
)
from uk_property_scrapers.rightmove.parser import (
    _absolutize,
    _detect_features,
    _infer_property_type,
)

_RIGHTMOVE_ORIGIN: Final = "https://www.rightmove.co.uk"
_MEDIA_ORIGIN: Final = "https://media.rightmove.co.uk"
_TAG_RE: Final = re.compile(r"<[^>]+>")
_WHITESPACE_RE: Final = re.compile(r"\s+")
_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)(?:\s+([0-9][A-Z]{2}))?\b"
)


# ── Public API ───────────────────────────────────────────────────────────────


def parse_branch_page(
    html: str,
    *,
    source_url: str | None = None,
) -> AgentProfile | None:
    """Parse a Rightmove branch-profile page into an :class:`AgentProfile`."""
    root = _extract_next_data(html)
    if root is None:
        return None
    data_block = (
        root.get("props", {}).get("pageProps", {}).get("data")
    )
    if not isinstance(data_block, dict):
        return None
    agent = _agent_from_data_block(data_block)
    if agent is None:
        return None

    branch_id = _str_or_none(agent.get("branchId"))
    if not branch_id:
        return None

    branch_profile_path = _str_or_none(agent.get("branchProfilePath"))
    url = _resolve_source_url(source_url, branch_profile_path, branch_id, agent)
    if url is None:
        return None

    display_name = (
        _str_or_none(agent.get("branchDisplayName"))
        or _str_or_none(agent.get("branchName"))
        or _str_or_none(agent.get("brandTradingName"))
        or "Rightmove agent"
    )
    group_name = _str_or_none(agent.get("brandTradingName")) or _str_or_none(
        agent.get("companyTradingName")
    )
    branch_label = _str_or_none(agent.get("branchName"))
    address = _format_address(_str_or_none(agent.get("branchAddress")))

    try:
        return AgentProfile(
            source=Source.RIGHTMOVE,
            source_id=branch_id,
            source_url=url,  # type: ignore[arg-type]
            name=display_name,
            group_name=group_name,
            branch=branch_label,
            address=address,
            phone=_preferred_phone(agent),
            email=None,
            website=None,
            logo_url=_preferred_logo(agent),  # type: ignore[arg-type]
            bio=_extract_bio(agent),
            opening_hours={},
            trade_bodies=_industry_affiliations(agent.get("industryAffiliations")),
            socials={},
            team=[],
            stock=_stock_summary(agent, data_block),
            raw_site_fields=_raw_site_fields(agent),
        )
    except ValidationError:
        return None


def parse_branch_stock(
    html: str,
    *,
    source_url: str | None = None,
) -> list[Listing]:
    """Return live + recently-sold stock for a branch as :class:`Listing` cards.

    Covers four Rightmove card sources: ``agentProfileResponse.salesProperties``,
    ``lettingsProperties``, and the top-level ``previousSoldProperties`` +
    ``previousLetProperties`` lists. Status-based features are set so a SSTC
    card carries :attr:`ListingFeature.SOLD_STC` instead of being silently
    mis-labelled as "available".
    """
    root = _extract_next_data(html)
    if root is None:
        return []
    data_block = (
        root.get("props", {}).get("pageProps", {}).get("data")
    )
    if not isinstance(data_block, dict):
        return []
    agent = _agent_from_data_block(data_block)
    if agent is None:
        return []

    agent_model = _agent_model(agent, data_block, source_url)
    out: list[Listing] = []

    sales = agent.get("salesProperties") or {}
    if isinstance(sales, dict):
        for raw in sales.get("properties") or []:
            if not isinstance(raw, dict):
                continue
            listing = _card_to_listing(
                raw, tx_type=TransactionType.SALE, agent=agent_model
            )
            if listing is not None:
                out.append(listing)

    lettings = agent.get("lettingsProperties") or {}
    if isinstance(lettings, dict):
        for raw in lettings.get("properties") or []:
            if not isinstance(raw, dict):
                continue
            listing = _card_to_listing(
                raw, tx_type=TransactionType.RENT, agent=agent_model
            )
            if listing is not None:
                out.append(listing)

    prev_sold = data_block.get("previousSoldProperties")
    if isinstance(prev_sold, list):
        for raw in prev_sold:
            if not isinstance(raw, dict):
                continue
            listing = _prev_card_to_listing(
                raw, tx_type=TransactionType.SALE, agent=agent_model
            )
            if listing is not None:
                out.append(listing)

    prev_let = data_block.get("previousLetProperties")
    if isinstance(prev_let, list):
        for raw in prev_let:
            if not isinstance(raw, dict):
                continue
            listing = _prev_card_to_listing(
                raw, tx_type=TransactionType.RENT, agent=agent_model
            )
            if listing is not None:
                out.append(listing)

    return out


# ── Payload extraction ───────────────────────────────────────────────────────


def _extract_next_data(html: str) -> dict[str, Any] | None:
    tree = HTMLParser(html)
    node = tree.css_first("script#__NEXT_DATA__")
    if node is None:
        return None
    raw = node.text() or ""
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _agent_from_data_block(data_block: dict[str, Any]) -> dict[str, Any] | None:
    branch_profile = data_block.get("branchProfileResponse")
    if not isinstance(branch_profile, dict):
        return None
    agent = branch_profile.get("agentProfileResponse")
    if not isinstance(agent, dict):
        return None
    return agent


# ── Field helpers ────────────────────────────────────────────────────────────


def _resolve_source_url(
    source_url: str | None,
    branch_profile_path: str | None,
    branch_id: str,
    agent: dict[str, Any],
) -> str | None:
    if source_url:
        return source_url
    if branch_profile_path:
        return _absolutize(branch_profile_path)
    company = _str_or_none(agent.get("companyName")) or _str_or_none(
        agent.get("companyTradingName")
    )
    branch_name = _str_or_none(agent.get("branchName"))
    if company and branch_name and branch_id:
        safe_company = company.replace(" ", "-")
        safe_branch = branch_name.replace(" ", "-")
        return (
            f"{_RIGHTMOVE_ORIGIN}/estate-agents/agent/"
            f"{safe_company}/{safe_branch}-{branch_id}.html"
        )
    return None


def _format_address(raw: str | None) -> str | None:
    """Collapse Rightmove's multiline address into a single comma-joined line.

    The raw value arrives as ``"10 Mill Road,\\r\\nCambridge,\\r\\nCB1 2AD"``
    where each line already ends in a trailing comma. A naive newline-to-
    comma join produces double commas; we strip the trailing comma off each
    line before re-joining, then collapse any leftover whitespace.
    """
    if not raw:
        return None
    parts: list[str] = []
    for line in raw.splitlines():
        cleaned = line.strip().rstrip(",").strip()
        if cleaned:
            parts.append(cleaned)
    return ", ".join(parts) if parts else raw


def _preferred_phone(agent: dict[str, Any]) -> str | None:
    """Sales telephone is Rightmove's primary outbound for buy-side leads;
    main telephone is the switchboard fallback. We surface the most specific
    first so the phone we return is the one the agent would actually answer
    for a sale inquiry generated from this MCP.
    """
    for key in ("branchSalesTelephone", "branchMainTelephone", "branchTelephone"):
        value = _str_or_none(agent.get(key))
        if value:
            return value
    return None


def _preferred_logo(agent: dict[str, Any]) -> str | None:
    for key in ("fullBranchLogoUrl", "branchLogoUrl"):
        value = _str_or_none(agent.get(key))
        if value:
            return _media_absolutize(value)
    return None


def _extract_bio(agent: dict[str, Any]) -> str | None:
    for key in ("branchSummary", "branchDescription", "primaryDescription"):
        raw = _str_or_none(agent.get(key))
        if raw:
            cleaned = _html_to_text(raw)
            if cleaned:
                return cleaned
    return None


def _industry_affiliations(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if isinstance(entry, dict):
            name = (
                _str_or_none(entry.get("name"))
                or _str_or_none(entry.get("title"))
                or _str_or_none(entry.get("id"))
            )
        elif isinstance(entry, str):
            name = _str_or_none(entry)
        else:
            name = None
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        labels.append(name)
    return labels


def _stock_summary(
    agent: dict[str, Any],
    data_block: dict[str, Any],
) -> AgentStockSummary | None:
    sales = agent.get("salesProperties") or {}
    lettings = agent.get("lettingsProperties") or {}

    for_sale = (
        _safe_int(sales.get("totalNumberOfProperties"))
        if isinstance(sales, dict)
        else None
    )
    to_rent = (
        _safe_int(lettings.get("totalNumberOfProperties"))
        if isinstance(lettings, dict)
        else None
    )
    prev_sold = data_block.get("previousSoldProperties")
    prev_let = data_block.get("previousLetProperties")
    sold_stc = _count_list(prev_sold) if isinstance(prev_sold, list) else None
    let_agreed = _count_list(prev_let) if isinstance(prev_let, list) else None
    median_price = _median_price_pence(sales)
    median_rent = _median_rent_pence(lettings)

    if not any((for_sale, to_rent, sold_stc, let_agreed, median_price, median_rent)):
        return None

    total_live = sum(v for v in (for_sale, to_rent) if v)

    try:
        return AgentStockSummary(
            total_live=total_live or None,
            for_sale=for_sale,
            to_rent=to_rent,
            sold_stc=sold_stc,
            sold_in_last_12m=sold_stc,
            let_agreed_in_last_12m=let_agreed,
            median_price_pence=median_price,
            median_rent_pence_per_month=median_rent,
        )
    except ValidationError:
        return None


def _median_price_pence(sales: object) -> int | None:
    if not isinstance(sales, dict):
        return None
    amounts: list[int] = []
    for raw in sales.get("properties") or []:
        if not isinstance(raw, dict):
            continue
        price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
        amount = price.get("amount") if isinstance(price, dict) else None
        if isinstance(amount, (int, float)) and amount > 0:
            amounts.append(int(amount))
    if not amounts:
        return None
    amounts.sort()
    mid = len(amounts) // 2
    median = amounts[mid] if len(amounts) % 2 else (amounts[mid - 1] + amounts[mid]) // 2
    return median * 100


def _median_rent_pence(lettings: object) -> int | None:
    """Normalise Rightmove's mixed rent cadences to a per-month value.

    Rightmove's ``frequency`` field can be ``weekly``, ``monthly``,
    ``quarterly``, ``yearly``, or ``not specified``. We convert each amount
    to a PCM equivalent (52-week year / 12 months) before taking the median,
    so the returned figure is directly comparable regardless of how each
    agent publishes prices.
    """
    if not isinstance(lettings, dict):
        return None
    amounts: list[int] = []
    for raw in lettings.get("properties") or []:
        if not isinstance(raw, dict):
            continue
        price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
        amount = price.get("amount") if isinstance(price, dict) else None
        freq = (price.get("frequency") if isinstance(price, dict) else "") or ""
        if not isinstance(amount, (int, float)) or amount <= 0:
            continue
        pcm = _rent_to_pcm(float(amount), str(freq).lower())
        if pcm is None:
            continue
        amounts.append(int(round(pcm)))
    if not amounts:
        return None
    amounts.sort()
    mid = len(amounts) // 2
    median = amounts[mid] if len(amounts) % 2 else (amounts[mid - 1] + amounts[mid]) // 2
    return median * 100


def _rent_to_pcm(amount: float, freq: str) -> float | None:
    if not freq or freq == "not specified":
        return amount
    if "week" in freq:
        return amount * 52 / 12
    if "month" in freq:
        return amount
    if "year" in freq or "annum" in freq:
        return amount / 12
    if "quarter" in freq:
        return amount / 3
    return amount


def _raw_site_fields(agent: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key_pair in (
        ("companyId", "company_id"),
        ("companyName", "company_name"),
        ("companyTradingName", "company_trading_name"),
        ("companyTypeAlias", "company_type"),
        ("brandTradingName", "brand_trading_name"),
        ("branchPostcode", "branch_postcode"),
        ("branchProfilePath", "branch_profile_path"),
        ("branchStaticMapImageUrl", "branch_static_map_url"),
    ):
        src, dest = key_pair
        value = _str_or_none(agent.get(src))
        if value:
            out[dest] = value
    for flag in (
        "sales",
        "lettings",
        "commercial",
        "buildToRent",
        "overseas",
        "development",
    ):
        if isinstance(agent.get(flag), bool):
            out[f"branch_type_{_camel_to_snake(flag)}"] = (
                "true" if agent[flag] else "false"
            )
    return out


def _camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


# ── Listing-card helpers ────────────────────────────────────────────────────


def _agent_model(
    agent: dict[str, Any],
    data_block: dict[str, Any],
    source_url: str | None,
) -> Agent | None:
    branch_id = _str_or_none(agent.get("branchId"))
    if not branch_id:
        return None
    display_name = (
        _str_or_none(agent.get("branchDisplayName"))
        or _str_or_none(agent.get("branchName"))
    )
    branch_label = _str_or_none(agent.get("branchName"))
    group_name = _str_or_none(agent.get("brandTradingName")) or _str_or_none(
        agent.get("companyTradingName")
    )
    address = _format_address(_str_or_none(agent.get("branchAddress")))
    url = _resolve_source_url(
        source_url, _str_or_none(agent.get("branchProfilePath")), branch_id, agent
    )
    try:
        return Agent(
            name=display_name,
            phone=_preferred_phone(agent),
            branch=branch_label,
            address=address,
            url=url,  # type: ignore[arg-type]
            logo_url=_preferred_logo(agent),  # type: ignore[arg-type]
            source_id=branch_id,
            group_name=group_name,
        )
    except ValidationError:
        return None


def _card_to_listing(
    raw: dict[str, Any],
    *,
    tx_type: TransactionType,
    agent: Agent | None,
) -> Listing | None:
    source_id_raw = raw.get("id")
    if source_id_raw is None:
        return None
    source_id = str(source_id_raw)
    details_url = _str_or_none(raw.get("propertyDetailsPageUrl"))
    if not details_url:
        return None
    url = _absolutize(details_url.split("#", 1)[0])

    price = raw.get("price") if isinstance(raw.get("price"), dict) else {}
    display_price = _str_or_none(price.get("displayPrice")) or ""
    display_qualifier = _str_or_none(price.get("displayPriceQualifier")) or ""
    frequency = (price.get("frequency") if isinstance(price, dict) else "") or ""
    amount_pounds = price.get("amount") if isinstance(price, dict) else None
    amount_pence = (
        int(round(float(amount_pounds) * 100))
        if isinstance(amount_pounds, (int, float)) and amount_pounds > 0
        else None
    )

    qualifier = _detect_qualifier_from_raw(display_qualifier)
    sale_price, rent_price = _materialize_prices(
        display_price=display_price,
        amount_pence=amount_pence,
        tx_type=tx_type,
        qualifier=qualifier,
        frequency=str(frequency).lower(),
    )

    beds = _safe_int(raw.get("bedrooms"))
    baths = _safe_int(raw.get("bathrooms"))
    subtype = _str_or_none(raw.get("propertySubType")) or ""
    property_type = _infer_property_type(subtype) if subtype else PropertyType.UNKNOWN

    status = _str_or_none(raw.get("status")) or ""
    listing_update = raw.get("listingUpdate") if isinstance(raw.get("listingUpdate"), dict) else {}
    update_reason = _str_or_none(listing_update.get("listingUpdateReason")) or ""

    features_blob = " ".join([status, update_reason, display_qualifier])
    features = _detect_features(blob=features_blob, url=url)
    features = _augment_status_features(features, status)

    display_address = _str_or_none(raw.get("displayAddress")) or ""
    postcode = None
    outcode = None
    match = _POSTCODE_RE.search(display_address.upper())
    if match:
        if match.group(2):
            postcode = f"{match.group(1)} {match.group(2)}"
        else:
            outcode = match.group(1)

    images = [
        img
        for img in (_image_from_card(entry) for entry in raw.get("images") or [])
        if img is not None
    ]

    try:
        return Listing(
            source=Source.RIGHTMOVE,
            source_id=source_id,
            source_url=url,  # type: ignore[arg-type]
            listing_type=ListingType.SEARCH_CARD,
            transaction_type=tx_type,
            sale_price=sale_price,
            rent_price=rent_price,
            property_type=property_type,
            property_type_raw=subtype or None,
            bedrooms=beds,
            bathrooms=baths,
            address=Address(
                raw=display_address,
                postcode=postcode,
                postcode_outcode=outcode,
            ),
            features=features,
            image_urls=images,
            agent=agent,
        )
    except ValidationError:
        return None


def _prev_card_to_listing(
    raw: dict[str, Any],
    *,
    tx_type: TransactionType,
    agent: Agent | None,
) -> Listing | None:
    """Rightmove's previousSoldProperties / previousLetProperties cards have a
    different shape from live stock: no ``propertyDetailsPageUrl``, no
    structured price object, no channel. We still emit SEARCH_CARD listings
    so ``list_agent_stock`` can return them uniformly alongside live stock.
    """
    source_id_raw = raw.get("id")
    if source_id_raw is None:
        return None
    source_id = str(source_id_raw)
    url = f"{_RIGHTMOVE_ORIGIN}/properties/{source_id}"

    display_price = _str_or_none(raw.get("price")) or ""
    amount_pence = _extract_price_pence(display_price)

    sale_price, rent_price = _materialize_prices(
        display_price=display_price,
        amount_pence=amount_pence,
        tx_type=tx_type,
        qualifier=PriceQualifier.UNKNOWN,
        frequency="",
    )

    status = _str_or_none(raw.get("status")) or ""
    features = _augment_status_features([], status)

    subtype = _str_or_none(raw.get("propertyType")) or ""
    property_type = (
        _infer_property_type(subtype) if subtype else PropertyType.UNKNOWN
    )
    display_address = _str_or_none(raw.get("displayAddress")) or ""
    match = _POSTCODE_RE.search(display_address.upper())
    postcode = (
        f"{match.group(1)} {match.group(2)}" if match and match.group(2) else None
    )
    outcode = match.group(1) if match and not (match.group(2)) else None

    images: list[Image] = []
    for entry in raw.get("images") or []:
        if not isinstance(entry, dict):
            continue
        src = _str_or_none(entry.get("mainImageSrc")) or _str_or_none(entry.get("url"))
        if not src:
            continue
        try:
            images.append(Image(url=src))  # type: ignore[arg-type]
        except ValidationError:
            continue

    try:
        return Listing(
            source=Source.RIGHTMOVE,
            source_id=source_id,
            source_url=url,  # type: ignore[arg-type]
            listing_type=ListingType.SEARCH_CARD,
            transaction_type=tx_type,
            sale_price=sale_price,
            rent_price=rent_price,
            property_type=property_type,
            property_type_raw=subtype or None,
            bedrooms=_safe_int(raw.get("bedrooms")),
            bathrooms=_safe_int(raw.get("bathrooms")),
            address=Address(
                raw=display_address,
                postcode=postcode,
                postcode_outcode=outcode,
            ),
            features=features,
            image_urls=images,
            agent=agent,
        )
    except ValidationError:
        return None


def _image_from_card(entry: object) -> Image | None:
    if not isinstance(entry, dict):
        return None
    src = _str_or_none(entry.get("url")) or _str_or_none(entry.get("mainImageSrc"))
    if not src:
        return None
    url = _media_absolutize(src)
    try:
        return Image(url=url)  # type: ignore[arg-type]
    except ValidationError:
        return None


def _media_absolutize(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return _MEDIA_ORIGIN + url
    return _MEDIA_ORIGIN + "/" + url


def _detect_qualifier_from_raw(raw: str) -> PriceQualifier:
    lowered = (raw or "").lower()
    if "guide" in lowered:
        return PriceQualifier.GUIDE_PRICE
    if "offers in excess" in lowered:
        return PriceQualifier.OFFERS_IN_EXCESS_OF
    if "offers in the region" in lowered:
        return PriceQualifier.OFFERS_IN_REGION
    if "offers over" in lowered:
        return PriceQualifier.OFFERS_OVER
    if "from" in lowered:
        return PriceQualifier.FROM
    if "fixed" in lowered:
        return PriceQualifier.FIXED_PRICE
    if "asking" in lowered:
        return PriceQualifier.ASKING_PRICE
    if "poa" in lowered or "application" in lowered:
        return PriceQualifier.POA
    return PriceQualifier.UNKNOWN


def _materialize_prices(
    *,
    display_price: str,
    amount_pence: int | None,
    tx_type: TransactionType,
    qualifier: PriceQualifier,
    frequency: str,
) -> tuple[Price | None, RentPrice | None]:
    if tx_type == TransactionType.RENT:
        period = _period_from_frequency(frequency)
        return None, RentPrice(
            amount_pence=amount_pence,
            qualifier=qualifier,
            raw=display_price,
            period=period,
        )
    return (
        Price(amount_pence=amount_pence, qualifier=qualifier, raw=display_price),
        None,
    )


def _period_from_frequency(freq: str) -> RentPeriod:
    if "week" in freq:
        return RentPeriod.PER_WEEK
    if "month" in freq:
        return RentPeriod.PER_MONTH
    if "year" in freq or "annum" in freq:
        return RentPeriod.PER_YEAR
    if "day" in freq:
        return RentPeriod.PER_DAY
    return RentPeriod.UNKNOWN


def _augment_status_features(
    features: list[ListingFeature],
    status: str,
) -> list[ListingFeature]:
    lowered = (status or "").lower()
    if "sold" in lowered and ListingFeature.SOLD_STC not in features:
        features.append(ListingFeature.SOLD_STC)
    if ("under" in lowered and "offer" in lowered) and ListingFeature.UNDER_OFFER not in features:
        features.append(ListingFeature.UNDER_OFFER)
    if "let agreed" in lowered and ListingFeature.LET_AGREED not in features:
        features.append(ListingFeature.LET_AGREED)
    return features


def _extract_price_pence(raw: str) -> int | None:
    if not raw:
        return None
    match = re.search(r"£\s*([\d,]+(?:\.\d+)?)", raw)
    if not match:
        return None
    cleaned = match.group(1).replace(",", "")
    try:
        return round(float(cleaned) * 100)
    except ValueError:
        return None


# ── Scalar helpers ──────────────────────────────────────────────────────────


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.isdigit():
            return int(cleaned)
    return None


def _count_list(raw: object) -> int | None:
    if isinstance(raw, list):
        return len(raw) or None
    return None


def _html_to_text(raw: str) -> str | None:
    no_tags = _TAG_RE.sub(" ", raw)
    no_tags = no_tags.replace("&nbsp;", " ").replace("&amp;", "&")
    no_tags = no_tags.replace("&lt;", "<").replace("&gt;", ">")
    no_tags = no_tags.replace("&#39;", "'").replace("&quot;", '"')
    cleaned = _WHITESPACE_RE.sub(" ", no_tags).strip()
    return cleaned or None


__all__ = ["parse_branch_page", "parse_branch_stock"]
