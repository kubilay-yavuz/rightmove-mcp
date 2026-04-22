"""Parser for OnTheMarket agent-branch pages (``/agents/branch/...``).

Unlike listing pages — where OTM leaves ``pageProps`` empty and stashes
everything in ``initialReduxState.property`` — branch pages keep the same
pattern but under a different key. We look at::

    __NEXT_DATA__.props.initialReduxState.agent

which carries branch metadata (``name``, ``address``, ``postcode``,
``telephone``, ``description``), live stock in ``sale.properties`` +
``rent.properties``, the external-site ``website-redirect-url``, and the
hero/logo image URLs. OTM does not expose opening hours, a team roster,
or a per-branch total-properties count — those remain absent. Trade-body
memberships appear in ``affiliation-logos`` only when the agent has opted
into the paid affiliation-display tier; the fixture for Abbotts Cambridge
has that disabled so the list is empty.
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

_OTM_ORIGIN: Final = "https://www.onthemarket.com"
_NEXT_DATA_RE: Final = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(?P<body>.*?)</script>', re.DOTALL
)
_TAG_RE: Final = re.compile(r"<[^>]+>")
_WHITESPACE_RE: Final = re.compile(r"\s+")
_GROUP_SEP_RE: Final = re.compile(r"\s*[-–—]\s*")
_POSTCODE_RE: Final = re.compile(
    r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)(?:\s+([0-9][A-Z]{2}))?\b"
)
_PRICE_AMOUNT_RE: Final = re.compile(r"£\s*([\d,]+(?:\.\d+)?)\s*(k|m)?", re.IGNORECASE)

_PROPERTY_TYPE_HINTS: Final[tuple[tuple[str, PropertyType], ...]] = (
    ("end of terrace", PropertyType.END_OF_TERRACE),
    ("end terrace", PropertyType.END_OF_TERRACE),
    ("semi-detached", PropertyType.SEMI_DETACHED),
    ("semi detached", PropertyType.SEMI_DETACHED),
    ("terraced house", PropertyType.TERRACED),
    ("terraced home", PropertyType.TERRACED),
    ("terraced", PropertyType.TERRACED),
    ("detached house", PropertyType.DETACHED),
    ("detached bungalow", PropertyType.BUNGALOW),
    ("detached", PropertyType.DETACHED),
    ("apartment", PropertyType.APARTMENT),
    ("maisonette", PropertyType.MAISONETTE),
    ("bungalow", PropertyType.BUNGALOW),
    ("cottage", PropertyType.COTTAGE),
    ("studio", PropertyType.STUDIO),
    ("flat", PropertyType.FLAT),
    ("park home", PropertyType.PARK_HOME),
    ("houseboat", PropertyType.HOUSEBOAT),
    ("land", PropertyType.LAND),
    ("commercial", PropertyType.COMMERCIAL),
    ("house", PropertyType.DETACHED),
)


# ── Public API ───────────────────────────────────────────────────────────────


def parse_branch_page(
    html: str,
    *,
    source_url: str | None = None,
) -> AgentProfile | None:
    """Parse an OnTheMarket branch HTML page into an :class:`AgentProfile`."""
    agent = _extract_agent_payload(html)
    if agent is None:
        return None

    branch_id = _str_or_none(agent.get("branch_id"))
    if not branch_id:
        return None
    details_url = _str_or_none(agent.get("details-url"))
    url = _resolve_source_url(source_url, details_url, branch_id)
    if url is None:
        return None

    name = _str_or_none(agent.get("name")) or "OnTheMarket agent"
    group_name, branch_label = _split_display_name(name)

    try:
        return AgentProfile(
            source=Source.ONTHEMARKET,
            source_id=branch_id,
            source_url=url,  # type: ignore[arg-type]
            name=name,
            group_name=group_name,
            branch=branch_label,
            address=_format_address(agent.get("address")),
            phone=_str_or_none(agent.get("telephone")),
            email=None,
            website=_website_from_redirect(agent),
            logo_url=_logo_url(agent),  # type: ignore[arg-type]
            bio=_html_to_text(_str_or_none(agent.get("description"))),
            opening_hours={},
            trade_bodies=_affiliations(agent.get("affiliation-logos")),
            socials={},
            team=[],
            stock=_stock_summary(agent),
            raw_site_fields=_raw_site_fields(agent),
        )
    except ValidationError:
        return None


def parse_branch_stock(
    html: str,
    *,
    source_url: str | None = None,
) -> list[Listing]:
    """Return the branch's live stock as :class:`Listing` search cards.

    OTM caps the branch-page preview at ~6 sale + 6 rent cards per tab, so
    this is *not* the branch's full inventory — it's the "properties
    nearby" rail OTM renders server-side. For full stock use the MCP's
    ``search_listings`` with the ``branchId`` filter.
    """
    agent = _extract_agent_payload(html)
    if agent is None:
        return []

    branch_id = _str_or_none(agent.get("branch_id"))
    name = _str_or_none(agent.get("name"))
    group_name, branch_label = _split_display_name(name or "")
    address = _format_address(agent.get("address"))
    phone = _str_or_none(agent.get("telephone"))
    details_url = _str_or_none(agent.get("details-url"))
    branch_url = _resolve_source_url(source_url, details_url, branch_id or "")

    agent_model: Agent | None = None
    if branch_id:
        try:
            agent_model = Agent(
                name=name,
                phone=phone,
                branch=branch_label,
                address=address,
                url=branch_url,  # type: ignore[arg-type]
                logo_url=_logo_url(agent),  # type: ignore[arg-type]
                source_id=branch_id,
                group_name=group_name,
            )
        except ValidationError:
            agent_model = None

    out: list[Listing] = []
    sale = agent.get("sale") or {}
    if isinstance(sale, dict):
        for raw in sale.get("properties") or []:
            if not isinstance(raw, dict):
                continue
            listing = _card_to_listing(
                raw, tx_type=TransactionType.SALE, agent=agent_model
            )
            if listing is not None:
                out.append(listing)
    rent = agent.get("rent") or {}
    if isinstance(rent, dict):
        for raw in rent.get("properties") or []:
            if not isinstance(raw, dict):
                continue
            listing = _card_to_listing(
                raw, tx_type=TransactionType.RENT, agent=agent_model
            )
            if listing is not None:
                out.append(listing)
    return out


# ── Payload extraction ───────────────────────────────────────────────────────


def _extract_agent_payload(html: str) -> dict[str, Any] | None:
    """Pull the ``initialReduxState.agent`` dict out of ``__NEXT_DATA__``.

    selectolax's ``script.text()`` sometimes returns an empty string for
    large JSON-only script bodies (we've seen this specifically on OTM's
    agent pages — likely because of how the parser normalises whitespace
    inside long ``<script>`` blocks). We fall back to a regex scan of the
    raw HTML when the DOM-based lookup returns an empty body.
    """
    body: str | None = None
    try:
        tree = HTMLParser(html)
        node = tree.css_first("script#__NEXT_DATA__")
        if node is not None:
            body = (node.text() or "").strip() or None
    except Exception:  # noqa: BLE001 — defensive; parser errors are best-effort
        body = None

    if body is None:
        match = _NEXT_DATA_RE.search(html)
        if match is None:
            return None
        body = match.group("body").strip()
        if not body:
            return None

    try:
        obj = json.loads(body)
    except (ValueError, TypeError):
        return None

    agent = (
        obj.get("props", {})
        .get("initialReduxState", {})
        .get("agent")
    )
    if not isinstance(agent, dict):
        return None
    return agent


# ── Field helpers ────────────────────────────────────────────────────────────


def _resolve_source_url(
    source_url: str | None,
    details_url: str | None,
    branch_id: str,
) -> str | None:
    if source_url:
        return source_url
    if details_url:
        return _absolutize(details_url)
    if branch_id:
        return f"{_OTM_ORIGIN}/agents/branch/{branch_id}/"
    return None


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _absolutize(href: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return _OTM_ORIGIN + href
    return href


def _format_address(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    parts = [line.strip() for line in raw.splitlines() if line.strip()]
    return ", ".join(parts) if parts else None


def _split_display_name(name: str) -> tuple[str | None, str | None]:
    if not name:
        return None, None
    parts = [p.strip() for p in _GROUP_SEP_RE.split(name) if p.strip()]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    if len(parts) == 1:
        return parts[0], None
    return None, None


def _website_from_redirect(agent: dict[str, Any]) -> str | None:
    """OTM hides the agent's direct website behind a tracking redirect.

    The redirect is stable (signed token, same URL across page loads) so
    we surface it as-is; callers resolve the real destination only if
    needed. The redirect URL itself is still a valid :class:`HttpUrl`.
    """
    redirect = _str_or_none(agent.get("website-redirect-url"))
    if not redirect:
        return None
    return _absolutize(redirect)


def _logo_url(agent: dict[str, Any]) -> str | None:
    logo = agent.get("display-logo")
    if isinstance(logo, dict):
        url = _str_or_none(logo.get("url"))
        if url:
            return _absolutize(url)
    return None


def _affiliations(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if isinstance(entry, dict):
            name = (
                _str_or_none(entry.get("name"))
                or _str_or_none(entry.get("title"))
                or _str_or_none(entry.get("alt"))
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
        out.append(name)
    return out


def _stock_summary(agent: dict[str, Any]) -> AgentStockSummary | None:
    sale = agent.get("sale") or {}
    rent = agent.get("rent") or {}
    sale_props = sale.get("properties") if isinstance(sale, dict) else None
    rent_props = rent.get("properties") if isinstance(rent, dict) else None
    for_sale = len(sale_props) if isinstance(sale_props, list) and sale_props else None
    to_rent = len(rent_props) if isinstance(rent_props, list) and rent_props else None

    median_sale = _median_price_pence(sale_props)
    median_rent = _median_rent_pcm_pence(rent_props)

    if not any((for_sale, to_rent, median_sale, median_rent)):
        return None

    total_live = sum(v for v in (for_sale, to_rent) if v)

    try:
        return AgentStockSummary(
            total_live=total_live or None,
            for_sale=for_sale,
            to_rent=to_rent,
            median_price_pence=median_sale,
            median_rent_pence_per_month=median_rent,
        )
    except ValidationError:
        return None


def _median_price_pence(properties: object) -> int | None:
    if not isinstance(properties, list):
        return None
    amounts: list[int] = []
    for raw in properties:
        if not isinstance(raw, dict):
            continue
        price = _str_or_none(raw.get("price")) or _str_or_none(raw.get("short-price"))
        if not price:
            continue
        pence = _extract_price_pence(price)
        if pence is not None and pence > 0:
            amounts.append(pence)
    if not amounts:
        return None
    amounts.sort()
    mid = len(amounts) // 2
    return (
        amounts[mid]
        if len(amounts) % 2
        else (amounts[mid - 1] + amounts[mid]) // 2
    )


def _median_rent_pcm_pence(properties: object) -> int | None:
    if not isinstance(properties, list):
        return None
    amounts: list[int] = []
    for raw in properties:
        if not isinstance(raw, dict):
            continue
        price = _str_or_none(raw.get("price")) or _str_or_none(raw.get("short-price"))
        if not price:
            continue
        pence = _extract_price_pence(price)
        if pence is None or pence <= 0:
            continue
        period = _period_from_raw(price)
        amounts.append(_rent_to_pcm_pence(pence, period))
    if not amounts:
        return None
    amounts.sort()
    mid = len(amounts) // 2
    return (
        amounts[mid]
        if len(amounts) % 2
        else (amounts[mid - 1] + amounts[mid]) // 2
    )


def _rent_to_pcm_pence(pence: int, period: RentPeriod) -> int:
    if period == RentPeriod.PER_WEEK:
        return int(pence * 52 / 12)
    if period == RentPeriod.PER_YEAR:
        return pence // 12
    if period == RentPeriod.PER_DAY:
        return int(pence * 365 / 12)
    return pence


def _period_from_raw(raw: str) -> RentPeriod:
    lowered = raw.lower()
    if "pcm" in lowered or "per month" in lowered or "/ month" in lowered:
        return RentPeriod.PER_MONTH
    if "pw" in lowered or "per week" in lowered or "/ week" in lowered:
        return RentPeriod.PER_WEEK
    if "per annum" in lowered or "pa" in lowered or "year" in lowered:
        return RentPeriod.PER_YEAR
    if "per day" in lowered:
        return RentPeriod.PER_DAY
    return RentPeriod.UNKNOWN


def _raw_site_fields(agent: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for src, dest in (
        ("postcode", "postcode"),
        ("rank", "rank"),
        ("agent-ref", "agent_ref"),
        ("details-url", "details_url"),
        ("contact-agent-url", "contact_url"),
        ("search-for-sale-url", "search_for_sale_url"),
        ("search-to-rent-url", "search_to_rent_url"),
        ("search-overseas-url", "search_overseas_url"),
    ):
        value = _str_or_none(agent.get(src))
        if value:
            out[dest] = value
    for flag_key in ("resale?", "lettings?", "virtual?", "enhanced?", "overseas?"):
        if isinstance(agent.get(flag_key), bool):
            out[f"branch_flag_{flag_key.rstrip('?')}"] = (
                "true" if agent[flag_key] else "false"
            )
    loc = agent.get("location")
    if isinstance(loc, dict):
        lat = loc.get("lat")
        lng = loc.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            out["latlng"] = f"{lat:.6f},{lng:.6f}"
    return out


# ── Listing-card adapter ────────────────────────────────────────────────────


def _card_to_listing(
    raw: dict[str, Any],
    *,
    tx_type: TransactionType,
    agent: Agent | None,
) -> Listing | None:
    source_id = _str_or_none(raw.get("id"))
    property_link = _str_or_none(raw.get("property-link"))
    if not source_id or not property_link:
        return None
    url = _absolutize(property_link)

    price_raw = _str_or_none(raw.get("price")) or _str_or_none(raw.get("short-price")) or ""
    amount_pence = _extract_price_pence(price_raw) if price_raw else None
    qualifier = _qualifier_from_raw(raw)
    sale_price, rent_price = _materialize_prices(
        raw=price_raw,
        amount_pence=amount_pence,
        tx_type=tx_type,
        qualifier=qualifier,
    )

    subtype = _str_or_none(raw.get("humanised-property-type"))
    property_type = (
        _infer_property_type(subtype) if subtype else PropertyType.UNKNOWN
    )

    beds = _safe_int(raw.get("bedrooms"))
    baths = _safe_int(raw.get("bathrooms"))
    receptions = _safe_int(raw.get("sitting-rooms"))

    display_address = _str_or_none(raw.get("display_address")) or ""
    postcode: str | None = None
    outcode = _str_or_none(raw.get("postcode1"))
    match = _POSTCODE_RE.search(display_address.upper())
    if match and match.group(2):
        postcode = f"{match.group(1)} {match.group(2)}"
    if postcode and outcode and not outcode:
        outcode = None

    images: list[Image] = []
    cover = _str_or_none(raw.get("cover-image"))
    if cover:
        try:
            images.append(Image(url=cover))  # type: ignore[arg-type]
        except ValidationError:
            pass
    image_list = raw.get("images")
    if isinstance(image_list, list):
        for entry in image_list:
            if not isinstance(entry, dict):
                continue
            u = _str_or_none(entry.get("default"))
            if u:
                try:
                    images.append(Image(url=u))  # type: ignore[arg-type]
                except ValidationError:
                    continue

    image_count = _safe_int(raw.get("images-count"))
    features = _features_from_flags(raw)

    title = _str_or_none(raw.get("property-title"))
    summary = _str_or_none(raw.get("summary"))

    try:
        return Listing(
            source=Source.ONTHEMARKET,
            source_id=source_id,
            source_url=url,  # type: ignore[arg-type]
            listing_type=ListingType.SEARCH_CARD,
            transaction_type=tx_type,
            sale_price=sale_price,
            rent_price=rent_price,
            property_type=property_type,
            property_type_raw=subtype,
            bedrooms=beds,
            bathrooms=baths,
            reception_rooms=receptions,
            address=Address(
                raw=display_address,
                postcode=postcode,
                postcode_outcode=outcode if not postcode else None,
            ),
            title=title,
            summary=summary,
            features=features,
            image_urls=images,
            image_count=image_count,
            agent=agent,
        )
    except ValidationError:
        return None


def _materialize_prices(
    *,
    raw: str,
    amount_pence: int | None,
    tx_type: TransactionType,
    qualifier: PriceQualifier,
) -> tuple[Price | None, RentPrice | None]:
    if tx_type == TransactionType.RENT:
        return None, RentPrice(
            amount_pence=amount_pence,
            qualifier=qualifier,
            raw=raw,
            period=_period_from_raw(raw),
        )
    return (
        Price(amount_pence=amount_pence, qualifier=qualifier, raw=raw),
        None,
    )


def _features_from_flags(raw: dict[str, Any]) -> list[ListingFeature]:
    """Synthesize :class:`ListingFeature` values from OTM's boolean flags.

    OTM publishes one boolean per status (``reduced?``, ``under-offer?``,
    ``sstc?``, ``let-agreed?``, ``premium?``, ``recently-added?``,
    ``online-viewing?``, ``matterport-virtual-tour?``, ``exclusive?``).
    We map each true flag to the corresponding feature and de-duplicate.
    """
    features: list[ListingFeature] = []

    def _flag(key: str) -> bool:
        value = raw.get(key)
        return isinstance(value, bool) and value

    if _flag("reduced?"):
        features.append(ListingFeature.REDUCED)
    if _flag("sstc?") or _flag("sstcm?"):
        features.append(ListingFeature.SOLD_STC)
    if _flag("let-agreed?"):
        features.append(ListingFeature.LET_AGREED)
    if _flag("under-offer?"):
        features.append(ListingFeature.UNDER_OFFER)
    if _flag("premium?") or _flag("spotlight?"):
        features.append(ListingFeature.PREMIUM)
    if _flag("recently-added?"):
        features.append(ListingFeature.NEW_LISTING)
    if _flag("new-home-flag"):
        features.append(ListingFeature.NEW_HOME)
    if _flag("matterport-virtual-tour?") or _flag("virtual-tours?"):
        features.append(ListingFeature.VIRTUAL_TOUR)
    if _flag("online-viewing?"):
        if ListingFeature.VIRTUAL_TOUR not in features:
            features.append(ListingFeature.VIRTUAL_TOUR)
    if _flag("videos?"):
        features.append(ListingFeature.VIDEO_TOUR)
    # De-duplicate while preserving order.
    return list(dict.fromkeys(features))


def _qualifier_from_raw(raw: dict[str, Any]) -> PriceQualifier:
    qualifier_raw = _str_or_none(raw.get("price-qualifier")) or ""
    lowered = qualifier_raw.lower()
    if "guide" in lowered:
        return PriceQualifier.GUIDE_PRICE
    if "offers in excess" in lowered or "oieo" in lowered:
        return PriceQualifier.OFFERS_IN_EXCESS_OF
    if "offers in the region" in lowered or "oiro" in lowered:
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


def _extract_price_pence(raw: str) -> int | None:
    match = _PRICE_AMOUNT_RE.search(raw)
    if not match:
        return None
    amount = float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").lower()
    multiplier = 1
    if suffix == "k":
        multiplier = 1_000
    elif suffix == "m":
        multiplier = 1_000_000
    return int(round(amount * multiplier * 100))


def _infer_property_type(raw: str) -> PropertyType:
    lowered = raw.lower()
    for phrase, ptype in _PROPERTY_TYPE_HINTS:
        if phrase in lowered:
            return ptype
    return PropertyType.OTHER


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


def _html_to_text(raw: str | None) -> str | None:
    if not raw:
        return None
    no_tags = _TAG_RE.sub(" ", raw)
    no_tags = no_tags.replace("&nbsp;", " ").replace("\xa0", " ")
    no_tags = no_tags.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    no_tags = no_tags.replace("&#39;", "'").replace("&quot;", '"')
    cleaned = _WHITESPACE_RE.sub(" ", no_tags).strip()
    return cleaned or None


__all__ = ["parse_branch_page", "parse_branch_stock"]
