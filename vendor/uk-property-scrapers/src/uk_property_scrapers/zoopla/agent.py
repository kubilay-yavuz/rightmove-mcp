"""Parser for Zoopla agent-branch pages (``/find-agents/branch/...``).

Zoopla ships the entire branch object as structured JSON inside
``<script id="__NEXT_DATA__">``. The parser here only touches that blob —
no CSS selectors are required and no text-level heuristics are needed.
The shape we depend on is::

    __NEXT_DATA__.props.pageProps.branch = {
        branchId, displayName, fullAddress, postcode, outcode, phone,
        details (HTML bio), detailsUri, contactUri,
        branchTypeSales, branchTypeLettings, logoUrl, branchImages,
        location: {lat, lng},
        memberships: [{id, name, logoUrl, url}, ...],
        openingTimes: {monday: "...", ...} | null,
        socialUrls: {facebook, twitter, instagram, linkedin, youtube, website} | null,
        staffMembers: [{name, role, phone, email, photoUrl}] | null,
        stats: {
            residentialSale: {total, averagePrice, weeksOnMarket} | null,
            residentialRent: ... | null,
        },
        listings: {
            residentialSale: [<card>], residentialRent: [...],
            commercialSale: [...], commercialRent: [...],
        },
    }

The ``branch.listings.*`` arrays contain card-level stock. We surface them
as :class:`Listing` search-cards via :func:`parse_branch_stock` so the
MCP ``list_agent_stock`` tool can return a uniform shape across portals.
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
    BranchTeamMember,
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
from uk_property_scrapers.zoopla.parser import (
    _absolutize,
    _detect_features,
    _extract_full_postcode,
    _extract_postcode_outcode,
    _extract_price_pence,
    _infer_property_type,
)

_ZOOPLA_ORIGIN: Final = "https://www.zoopla.co.uk"
_TAG_RE: Final = re.compile(r"<[^>]+>")
_WHITESPACE_RE: Final = re.compile(r"\s+")
_GROUP_SEP_RE: Final = re.compile(r"\s*[-–—,]\s*")

_SOCIAL_HOST_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("facebook.com", "facebook"),
    ("twitter.com", "twitter"),
    ("x.com", "twitter"),
    ("instagram.com", "instagram"),
    ("linkedin.com", "linkedin"),
    ("youtube.com", "youtube"),
    ("tiktok.com", "tiktok"),
)


# ── Public API ──────────────────────────────────────────────────────────────


def parse_branch_page(
    html: str,
    *,
    source_url: str | None = None,
) -> AgentProfile | None:
    """Parse a Zoopla agent-branch HTML page into an :class:`AgentProfile`.

    Returns ``None`` when the embedded ``__NEXT_DATA__`` payload is missing
    or does not contain a ``branch`` object (404 pages, login walls, etc.).
    """
    branch = _extract_branch_payload(html)
    if branch is None:
        return None

    branch_id = _str_or_none(branch.get("branchId"))
    if not branch_id:
        return None

    details_uri = _str_or_none(branch.get("detailsUri"))
    resolved_url = _resolve_source_url(source_url, details_uri, branch_id)
    if resolved_url is None:
        return None

    display_name = _str_or_none(branch.get("displayName")) or "Zoopla agent"
    group_name, branch_label = _split_display_name(display_name)

    try:
        return AgentProfile(
            source=Source.ZOOPLA,
            source_id=branch_id,
            source_url=resolved_url,  # type: ignore[arg-type]
            name=display_name,
            group_name=group_name,
            branch=branch_label,
            address=_str_or_none(branch.get("fullAddress")),
            phone=_str_or_none(branch.get("phone")),
            email=None,
            website=_extract_website_url(branch),
            logo_url=_str_or_none(branch.get("logoUrl")),  # type: ignore[arg-type]
            bio=_html_to_text(_str_or_none(branch.get("details"))),
            opening_hours=_opening_hours(branch.get("openingTimes")),
            trade_bodies=_trade_bodies(branch.get("memberships")),
            socials=_socials(branch.get("socialUrls")),
            team=_team(branch.get("staffMembers")),
            stock=_stock_summary(branch),
            raw_site_fields=_raw_site_fields(branch),
        )
    except ValidationError:
        return None


def parse_branch_stock(
    html: str,
    *,
    source_url: str | None = None,
) -> list[Listing]:
    """Return the branch's live stock as :class:`Listing` search cards.

    Stock is taken verbatim from the RSC ``branch.listings`` dictionary.
    Cards that fail :class:`Listing` validation (rare — these are server-
    side sanitised) are silently skipped so one malformed row never kills
    the whole response.
    """
    branch = _extract_branch_payload(html)
    if branch is None:
        return []

    branch_id = _str_or_none(branch.get("branchId"))
    display_name = _str_or_none(branch.get("displayName"))
    phone = _str_or_none(branch.get("phone"))
    full_address = _str_or_none(branch.get("fullAddress"))
    details_uri = _str_or_none(branch.get("detailsUri"))
    resolved_branch_url = _resolve_source_url(source_url, details_uri, branch_id or "")
    group_name, branch_label = _split_display_name(display_name or "")

    agent = Agent(
        name=display_name,
        phone=phone,
        branch=branch_label,
        address=full_address,
        source_id=branch_id,
        group_name=group_name,
        url=resolved_branch_url,  # type: ignore[arg-type]
    ) if branch_id else None

    listings_dict = branch.get("listings") or {}
    out: list[Listing] = []
    for tx_key, tx_type in _TX_KIND_MAP.items():
        raw_cards = listings_dict.get(tx_key) or []
        if not isinstance(raw_cards, list):
            continue
        for raw in raw_cards:
            if not isinstance(raw, dict):
                continue
            card = _card_to_listing(raw, tx_type=tx_type, agent=agent)
            if card is not None:
                out.append(card)
    return out


# ── Payload extraction ───────────────────────────────────────────────────────


def _extract_branch_payload(html: str) -> dict[str, Any] | None:
    tree = HTMLParser(html)
    node = tree.css_first("script#__NEXT_DATA__")
    if node is None:
        return None
    raw = node.text() or ""
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    branch = (
        data.get("props", {})
        .get("pageProps", {})
        .get("branch")
    )
    if not isinstance(branch, dict):
        return None
    return branch


# ── Field helpers ────────────────────────────────────────────────────────────


def _resolve_source_url(
    source_url: str | None,
    details_uri: str | None,
    branch_id: str,
) -> str | None:
    if source_url:
        return source_url
    if details_uri:
        return _absolutize(details_uri)
    if branch_id:
        return f"{_ZOOPLA_ORIGIN}/find-agents/branch/{branch_id}/"
    return None


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _split_display_name(name: str) -> tuple[str | None, str | None]:
    """Split Zoopla's ``"Connells - Cambourne"`` into ``("Connells", "Cambourne")``.

    Zoopla joins group+branch with an en-dash, hyphen, or em-dash. Single-
    word names (e.g. "Hockeys" with no branch label) fall through to
    ``(group, None)``.
    """
    if not name:
        return None, None
    parts = [p.strip() for p in _GROUP_SEP_RE.split(name) if p.strip()]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    if len(parts) == 1:
        return parts[0], None
    return None, None


def _html_to_text(raw: str | None) -> str | None:
    if not raw:
        return None
    no_tags = _TAG_RE.sub(" ", raw)
    no_tags = no_tags.replace("&nbsp;", " ").replace("&amp;", "&")
    no_tags = no_tags.replace("&lt;", "<").replace("&gt;", ">")
    no_tags = no_tags.replace("&#39;", "'").replace("&quot;", '"')
    cleaned = _WHITESPACE_RE.sub(" ", no_tags).strip()
    return cleaned or None


def _opening_hours(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for day, value in raw.items():
        if isinstance(day, str) and isinstance(value, str):
            out[day.lower()] = value
    return out


def _trade_bodies(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = _str_or_none(entry.get("name")) or _str_or_none(entry.get("id"))
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        labels.append(name)
    return labels


def _socials(raw: object) -> dict[str, str]:
    """Normalise Zoopla's ``socialUrls`` dict into a flat ``{network: url}``.

    Zoopla publishes keys verbatim (``facebook``, ``twitter``, etc.). We
    pass them through and use URL hostnames as a fallback classifier so
    future additions (``tiktok``, ``threads``) still map correctly.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str) or not value:
            continue
        url = value.strip()
        if not url.startswith(("http://", "https://")):
            continue
        normalised_key = key.lower() if isinstance(key, str) else ""
        if not normalised_key:
            normalised_key = _infer_social_network(url) or "website"
        out[normalised_key] = url
    return out


def _infer_social_network(url: str) -> str | None:
    lower = url.lower()
    for host, network in _SOCIAL_HOST_HINTS:
        if host in lower:
            return network
    return None


def _extract_website_url(branch: dict[str, Any]) -> str | None:
    """Zoopla occasionally stores the agent's external website under
    ``socialUrls.website`` — we surface that as :attr:`AgentProfile.website`
    rather than as a social link so consumers don't have to guess.
    """
    socials = branch.get("socialUrls")
    if isinstance(socials, dict):
        website = _str_or_none(socials.get("website")) or _str_or_none(
            socials.get("companyWebsite")
        )
        if website and website.startswith(("http://", "https://")):
            return website
    return None


def _team(raw: object) -> list[BranchTeamMember]:
    if not isinstance(raw, list):
        return []
    members: list[BranchTeamMember] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = _str_or_none(entry.get("name")) or _str_or_none(entry.get("fullName"))
        if not name:
            continue
        try:
            members.append(
                BranchTeamMember(
                    name=name,
                    role=_str_or_none(entry.get("role"))
                    or _str_or_none(entry.get("jobTitle")),
                    phone=_str_or_none(entry.get("phone"))
                    or _str_or_none(entry.get("phoneNumber")),
                    email=_str_or_none(entry.get("email")),
                    photo_url=_str_or_none(entry.get("photoUrl"))  # type: ignore[arg-type]
                    or _str_or_none(entry.get("imageUrl")),
                )
            )
        except ValidationError:
            continue
    return members


def _stock_summary(branch: dict[str, Any]) -> AgentStockSummary | None:
    stats = branch.get("stats") or {}
    listings = branch.get("listings") or {}
    if not isinstance(stats, dict):
        stats = {}
    if not isinstance(listings, dict):
        listings = {}

    for_sale = _safe_stat_int(stats.get("residentialSale"), "total") or _count_list(
        listings.get("residentialSale")
    )
    to_rent = _safe_stat_int(stats.get("residentialRent"), "total") or _count_list(
        listings.get("residentialRent")
    )
    commercial_sale = _count_list(listings.get("commercialSale"))
    commercial_rent = _count_list(listings.get("commercialRent"))

    if not any((for_sale, to_rent, commercial_sale, commercial_rent)):
        return None

    total_live = sum(
        v for v in (for_sale, to_rent, commercial_sale, commercial_rent) if v
    )
    median_price = _safe_stat_int(stats.get("residentialSale"), "averagePrice")
    median_rent = _safe_stat_int(stats.get("residentialRent"), "averagePrice")

    try:
        return AgentStockSummary(
            total_live=total_live or None,
            for_sale=for_sale,
            to_rent=to_rent,
            median_price_pence=(median_price * 100) if median_price else None,
            median_rent_pence_per_month=(median_rent * 100) if median_rent else None,
        )
    except ValidationError:
        return None


def _safe_stat_int(raw: object, key: str) -> int | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return None


def _count_list(raw: object) -> int | None:
    if isinstance(raw, list):
        return len(raw) or None
    return None


def _raw_site_fields(branch: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("outcode", "postcode", "contactUri", "detailsUri"):
        value = _str_or_none(branch.get(key))
        if value:
            out[key] = value
    if isinstance(branch.get("branchTypeSales"), bool):
        out["branch_type_sales"] = "true" if branch["branchTypeSales"] else "false"
    if isinstance(branch.get("branchTypeLettings"), bool):
        out["branch_type_lettings"] = (
            "true" if branch["branchTypeLettings"] else "false"
        )
    loc = branch.get("location")
    if isinstance(loc, dict):
        lat = loc.get("lat")
        lng = loc.get("lng")
        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            out["latlng"] = f"{lat:.6f},{lng:.6f}"
    return out


# ── Listing-card adapter ─────────────────────────────────────────────────────


_TX_KIND_MAP: Final[dict[str, TransactionType]] = {
    "residentialSale": TransactionType.SALE,
    "residentialRent": TransactionType.RENT,
    "commercialSale": TransactionType.SALE,
    "commercialRent": TransactionType.RENT,
}


def _card_to_listing(
    raw: dict[str, Any],
    *,
    tx_type: TransactionType,
    agent: Agent | None,
) -> Listing | None:
    source_id = _str_or_none(raw.get("listingId"))
    if not source_id:
        return None
    details_uri = _str_or_none(raw.get("detailsUri"))
    if not details_uri:
        return None
    url = _absolutize(details_uri)

    price_raw = _str_or_none(raw.get("price")) or ""
    amount_pence = _extract_price_pence(price_raw) if price_raw else None
    sale_price, rent_price = _materialize_card_prices(
        raw=price_raw,
        amount_pence=amount_pence,
        tx_type=tx_type,
    )

    amenities = raw.get("amenities") or {}
    if not isinstance(amenities, dict):
        amenities = {}
    beds = _coerce_int(amenities.get("bedrooms"))
    baths = _coerce_int(amenities.get("bathrooms"))
    receptions = _coerce_int(amenities.get("livingRooms"))

    title = _str_or_none(raw.get("title"))
    summary = _str_or_none(raw.get("summaryDescription"))
    display_address = _str_or_none(raw.get("displayAddress")) or ""
    postcode = _extract_full_postcode(display_address)
    outcode = (
        _extract_postcode_outcode(display_address) if not postcode else None
    )

    image_node = raw.get("image")
    image_src: str | None = None
    if isinstance(image_node, dict):
        image_src = _str_or_none(image_node.get("src"))
    image_urls: list[Image] = []
    if image_src:
        try:
            image_urls.append(Image(url=image_src))  # type: ignore[arg-type]
        except ValidationError:
            pass

    features = _detect_features(blob=(title or "") + " " + (summary or ""), url=url)
    if raw.get("underOffer"):
        if ListingFeature.UNDER_OFFER not in features:
            features.append(ListingFeature.UNDER_OFFER)

    property_type = _infer_property_type(title or "") if title else PropertyType.UNKNOWN

    try:
        return Listing(
            source=Source.ZOOPLA,
            source_id=source_id,
            source_url=url,  # type: ignore[arg-type]
            listing_type=ListingType.SEARCH_CARD,
            transaction_type=tx_type,
            sale_price=sale_price,
            rent_price=rent_price,
            property_type=property_type,
            property_type_raw=title,
            bedrooms=beds,
            bathrooms=baths,
            reception_rooms=receptions,
            address=Address(
                raw=display_address,
                postcode=postcode,
                postcode_outcode=outcode,
            ),
            title=title,
            summary=summary,
            features=features,
            image_urls=image_urls,
            agent=agent,
        )
    except ValidationError:
        return None


def _materialize_card_prices(
    *,
    raw: str,
    amount_pence: int | None,
    tx_type: TransactionType,
) -> tuple[Price | None, RentPrice | None]:
    if tx_type == TransactionType.RENT:
        return None, RentPrice(
            amount_pence=amount_pence,
            qualifier=PriceQualifier.UNKNOWN,
            raw=raw,
            period=RentPeriod.PER_MONTH,
        )
    return (
        Price(
            amount_pence=amount_pence,
            qualifier=PriceQualifier.UNKNOWN,
            raw=raw,
        ),
        None,
    )


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit():
            return int(cleaned)
    return None


__all__ = ["parse_branch_page", "parse_branch_stock"]
