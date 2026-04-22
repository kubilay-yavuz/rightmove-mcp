"""Internal helpers for mapping upstream payloads into the canonical :class:`Tender`.

This module keeps the source-specific JSON shapes out of the public client
surface. Both :class:`ContractsFinderClient` and :class:`FTSClient` call
into these helpers from their ``.search_tenders`` / ``.iter_tenders``
methods so consumers always receive normalised rows regardless of source.

Two normalisers are exposed:

* :func:`normalise_cf_notice` — Contracts Finder ``Notice`` /
  ``HitOfNoticeIndex`` dicts.
* :func:`normalise_fts_release` — Find a Tender OCDS ``Release`` objects.

Both intentionally take plain ``dict`` inputs (not Pydantic models) so
smoke tests and ad-hoc debugging can feed them anything JSON-decodable.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from uk_property_apis.tenders.models import (
    Tender,
    TenderClassification,
    TenderLocation,
    TenderOrg,
    TenderSource,
    TenderStatus,
    TenderValue,
)

_COUNTRY_NAME_TO_ISO: dict[str, str] = {
    "united kingdom": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
    "uk": "GB",
    "ireland": "IE",
    "france": "FR",
    "germany": "DE",
    "spain": "ES",
    "italy": "IT",
    "netherlands": "NL",
    "belgium": "BE",
    "portugal": "PT",
    "poland": "PL",
    "united states": "US",
    "usa": "US",
}


def _resolve_country_code(address_block: dict[str, Any] | None) -> str | None:
    """Prefer explicit ``countryCode`` field, fall back to mapped ``countryName``.

    FTS OCDS payloads can set either field (or both); the ISO code is
    canonical, but smaller publishers sometimes only populate the free-text
    name. Returning ``None`` is safer than mid-slicing ``"United Kingdom"``
    to ``"UN"``.
    """

    if not isinstance(address_block, dict):
        return None
    code = address_block.get("countryCode")
    if isinstance(code, str) and code.strip():
        stripped = code.strip().upper()
        if len(stripped) == 2 and stripped.isalpha():
            return stripped
    name = address_block.get("countryName")
    if isinstance(name, str) and name.strip():
        iso = _COUNTRY_NAME_TO_ISO.get(name.strip().lower())
        if iso is not None:
            return iso
    return None

_CF_STATUS_MAP: dict[str, TenderStatus] = {
    "open": TenderStatus.OPEN,
    "awarded": TenderStatus.AWARDED,
    "closed": TenderStatus.CLOSED,
    "completed": TenderStatus.COMPLETE,
    "complete": TenderStatus.COMPLETE,
    "cancelled": TenderStatus.CANCELLED,
    "canceled": TenderStatus.CANCELLED,
    "withdrawn": TenderStatus.CANCELLED,
    "planned": TenderStatus.PLANNED,
    "pipeline": TenderStatus.PLANNED,
}

_FTS_STATUS_MAP: dict[str, TenderStatus] = {
    "active": TenderStatus.OPEN,
    "planning": TenderStatus.PLANNED,
    "planned": TenderStatus.PLANNED,
    "complete": TenderStatus.COMPLETE,
    "completed": TenderStatus.COMPLETE,
    "cancelled": TenderStatus.CANCELLED,
    "canceled": TenderStatus.CANCELLED,
    "withdrawn": TenderStatus.CANCELLED,
    "unsuccessful": TenderStatus.CANCELLED,
}


def _normalise_status(raw: object, source: TenderSource) -> TenderStatus:
    if not isinstance(raw, str):
        return TenderStatus.UNKNOWN
    key = raw.strip().lower()
    if not key:
        return TenderStatus.UNKNOWN
    table = (
        _CF_STATUS_MAP if source is TenderSource.CONTRACTS_FINDER else _FTS_STATUS_MAP
    )
    return table.get(key, TenderStatus.UNKNOWN)


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _parse_date(value: object) -> date | None:
    dt = _parse_datetime(value)
    if dt is None:
        return None
    return dt.date()


def _coerce_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _cf_pick(notice: dict[str, Any], *keys: str) -> Any:
    """CF payloads mix PascalCase and camelCase; try each variant in order."""

    for key in keys:
        if key in notice and notice[key] is not None:
            return notice[key]
        lower = key[:1].lower() + key[1:]
        if lower in notice and notice[lower] is not None:
            return notice[lower]
    return None


def _cf_cpv_codes(notice: dict[str, Any]) -> list[TenderClassification]:
    raw = _cf_pick(notice, "CpvCodes", "cpvCodes") or []
    out: list[TenderClassification] = []
    # 2026+ CF search results ship ``cpvCodes`` as a single
    # whitespace-separated string. Split and treat as a list.
    if isinstance(raw, str):
        raw = raw.split()
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                out.append(TenderClassification(scheme="CPV", code=entry.strip()))
            elif isinstance(entry, dict):
                code = entry.get("code") or entry.get("Code") or entry.get("id")
                if isinstance(code, str) and code.strip():
                    desc = entry.get("description") or entry.get("Description")
                    out.append(
                        TenderClassification(
                            scheme="CPV",
                            code=code.strip(),
                            description=desc if isinstance(desc, str) else None,
                        )
                    )
    return out


def _cf_buyer(notice: dict[str, Any]) -> TenderOrg | None:
    # Pre-2024 CF nested the buyer inside ``Organisation`` / ``organisation``.
    # The 2026+ search response flattens it to ``organisationName`` + a
    # top-level ``postcode`` instead.
    org = _cf_pick(notice, "Organisation", "organisation")
    if isinstance(org, dict):
        name = org.get("name") or org.get("Name")
        if not isinstance(name, str) or not name.strip():
            return None
        contact = org.get("ContactDetails") or org.get("contactDetails") or {}
        address_parts: list[str] = []
        for key in ("AddressLine1", "AddressLine2", "Town", "County", "Postcode"):
            piece = contact.get(key) if isinstance(contact, dict) else None
            if isinstance(piece, str) and piece.strip():
                address_parts.append(piece.strip())
        postcode = contact.get("Postcode") if isinstance(contact, dict) else None
        return TenderOrg(
            name=name.strip(),
            address=", ".join(address_parts) if address_parts else None,
            postcode=postcode.strip().upper() if isinstance(postcode, str) else None,
            country_code="GB",
        )

    name = _cf_pick(notice, "OrganisationName", "organisationName")
    if not isinstance(name, str) or not name.strip():
        return None
    postcode = _cf_pick(notice, "Postcode", "postcode")
    return TenderOrg(
        name=name.strip(),
        address=None,
        postcode=(
            postcode.strip().upper()
            if isinstance(postcode, str) and postcode.strip()
            else None
        ),
        country_code="GB",
    )


def _cf_value(notice: dict[str, Any]) -> TenderValue | None:
    low = _coerce_float(_cf_pick(notice, "ValueLow", "valueLow"))
    high = _coerce_float(_cf_pick(notice, "ValueHigh", "valueHigh"))
    awarded = _coerce_float(_cf_pick(notice, "AwardedValue", "awardedValue"))
    amount = awarded if awarded is not None else None
    if amount is None and low is None and high is None:
        return None
    return TenderValue(
        amount=amount,
        amount_low=low,
        amount_high=high,
        currency="GBP",
    )


def _cf_location(notice: dict[str, Any]) -> TenderLocation | None:
    postcode = _cf_pick(notice, "Postcode", "postcode")
    # 2026+ search results ship ``regionText`` (human label) alongside
    # ``region`` (code). Prefer the label when present, since it's what
    # consumers want to display.
    region_field = _cf_pick(notice, "RegionText", "regionText", "Region", "region")
    region: str | None = None
    if isinstance(region_field, str) and region_field.strip():
        region = region_field.strip()
    elif isinstance(region_field, list):
        names = [r for r in region_field if isinstance(r, str) and r.strip()]
        if names:
            region = ", ".join(s.strip() for s in names)
    if postcode is None and region is None:
        return None
    return TenderLocation(
        region=region,
        postcode=postcode.strip().upper() if isinstance(postcode, str) else None,
        country_code="GB",
    )


def normalise_cf_notice(notice: dict[str, Any]) -> Tender:
    """Map a Contracts Finder ``Notice`` dict to a canonical :class:`Tender`.

    Accepts all three payload shapes CF has shipped in the life of this
    client:

    * Pre-2024 "full" notices wrapping content in ``"Notice": {...}``
    * 2024-era slim ``HitOfNoticeIndex`` payloads (flat)
    * 2026+ search results which wrap each hit as
      ``{"score": <float>, "item": {...}}``

    Missing fields are mapped to ``None`` / the relevant default rather
    than raising.
    """

    inner = notice
    if "Notice" in notice and isinstance(notice["Notice"], dict):
        inner = {**notice, **notice["Notice"]}
    # 2026 search_notices wraps each hit in ``{"score", "item"}`` — the
    # actual notice payload lives under ``item``. Unwrap transparently.
    if "item" in inner and isinstance(inner["item"], dict):
        inner = {**inner, **inner["item"]}

    source_id_raw = _cf_pick(inner, "Id", "NoticeId", "id")
    source_id = str(source_id_raw) if source_id_raw is not None else ""

    title = _cf_pick(inner, "Title", "title") or ""
    description = _cf_pick(inner, "Description", "description")
    notice_type = _cf_pick(inner, "NoticeType", "noticeType")
    status_raw = _cf_pick(inner, "NoticeStatus", "noticeStatus", "Status", "status")

    published_date = _parse_datetime(_cf_pick(inner, "PublishedDate", "publishedDate"))
    closing_date = _parse_datetime(
        _cf_pick(inner, "DeadlineDate", "ClosingDate", "deadlineDate", "closingDate")
    )
    start_date = _parse_date(_cf_pick(inner, "StartDate", "startDate"))
    end_date = _parse_date(_cf_pick(inner, "EndDate", "endDate"))

    notice_id = source_id
    url_candidate = _cf_pick(inner, "Link", "link")
    if isinstance(url_candidate, str) and url_candidate.strip():
        url = url_candidate.strip()
    elif notice_id:
        url = (
            f"https://www.contractsfinder.service.gov.uk/Notice/{notice_id}"
        )
    else:
        url = None

    return Tender(
        source=TenderSource.CONTRACTS_FINDER,
        source_id=source_id,
        title=str(title).strip() if isinstance(title, str) else str(title),
        description=description if isinstance(description, str) else None,
        status=_normalise_status(status_raw, TenderSource.CONTRACTS_FINDER),
        notice_type=notice_type if isinstance(notice_type, str) else None,
        published_date=published_date,
        closing_date=closing_date,
        start_date=start_date,
        end_date=end_date,
        buyer=_cf_buyer(inner),
        value=_cf_value(inner),
        classifications=_cf_cpv_codes(inner),
        location=_cf_location(inner),
        url=url,
        raw=dict(notice),
    )


def _fts_buyer(release: dict[str, Any]) -> TenderOrg | None:
    buyer = release.get("buyer")
    parties = release.get("parties")
    if not isinstance(parties, list):
        parties = []
    if not isinstance(buyer, dict):
        return None
    buyer_id = buyer.get("id")
    name = buyer.get("name")
    merged_party: dict[str, Any] | None = None
    for party in parties:
        if isinstance(party, dict) and party.get("id") == buyer_id:
            merged_party = party
            break
    effective_name: str | None = None
    if isinstance(name, str) and name.strip():
        effective_name = name.strip()
    elif merged_party and isinstance(merged_party.get("name"), str):
        effective_name = str(merged_party["name"]).strip()
    if not effective_name:
        return None

    identifier_block = (
        merged_party.get("identifier") if isinstance(merged_party, dict) else None
    )
    scheme = None
    identifier = None
    if isinstance(identifier_block, dict):
        scheme_raw = identifier_block.get("scheme")
        id_raw = identifier_block.get("id")
        if isinstance(scheme_raw, str):
            scheme = scheme_raw
        if id_raw is not None:
            identifier = str(id_raw)

    address_block = (
        merged_party.get("address") if isinstance(merged_party, dict) else None
    )
    address: str | None = None
    postcode: str | None = None
    region: str | None = None
    country_code = _resolve_country_code(
        address_block if isinstance(address_block, dict) else None
    )
    if isinstance(address_block, dict):
        parts = [
            address_block.get("streetAddress"),
            address_block.get("locality"),
            address_block.get("region"),
            address_block.get("postalCode"),
        ]
        address_parts = [p for p in parts if isinstance(p, str) and p.strip()]
        if address_parts:
            address = ", ".join(p.strip() for p in address_parts)
        pc = address_block.get("postalCode")
        if isinstance(pc, str) and pc.strip():
            postcode = pc.strip().upper()
        reg = address_block.get("region")
        if isinstance(reg, str) and reg.strip():
            region = reg.strip()

    return TenderOrg(
        name=effective_name,
        scheme=scheme,
        identifier=identifier,
        address=address,
        postcode=postcode,
        region=region,
        country_code=country_code,
    )


def _fts_suppliers(release: dict[str, Any]) -> list[TenderOrg]:
    awards = release.get("awards")
    if not isinstance(awards, list):
        return []
    parties = release.get("parties")
    party_index: dict[str, dict[str, Any]] = {}
    if isinstance(parties, list):
        for party in parties:
            if isinstance(party, dict):
                pid = party.get("id")
                if isinstance(pid, str):
                    party_index[pid] = party
    out: list[TenderOrg] = []
    seen_ids: set[str] = set()
    for award in awards:
        if not isinstance(award, dict):
            continue
        suppliers = award.get("suppliers")
        if not isinstance(suppliers, list):
            continue
        for supplier in suppliers:
            if not isinstance(supplier, dict):
                continue
            supplier_id = supplier.get("id")
            key = str(supplier_id) if supplier_id is not None else supplier.get("name")
            if not isinstance(key, str) or key in seen_ids:
                continue
            seen_ids.add(key)
            party = party_index.get(key, {}) if isinstance(key, str) else {}
            name = supplier.get("name") or party.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            ident_block = party.get("identifier") if isinstance(party, dict) else None
            scheme = None
            ident = None
            if isinstance(ident_block, dict):
                sch = ident_block.get("scheme")
                idv = ident_block.get("id")
                if isinstance(sch, str):
                    scheme = sch
                if idv is not None:
                    ident = str(idv)
            out.append(
                TenderOrg(
                    name=name.strip(),
                    scheme=scheme,
                    identifier=ident,
                    country_code="GB",
                )
            )
    return out


def _fts_value(tender: dict[str, Any], release: dict[str, Any]) -> TenderValue | None:
    value = tender.get("value") if isinstance(tender, dict) else None
    amount = None
    currency = "GBP"
    if isinstance(value, dict):
        amount = _coerce_float(value.get("amount"))
        curr = value.get("currency")
        if isinstance(curr, str) and curr.strip():
            currency = curr.strip().upper()
    awarded_amount = None
    awards = release.get("awards")
    if isinstance(awards, list):
        for award in awards:
            if not isinstance(award, dict):
                continue
            av = award.get("value")
            if isinstance(av, dict):
                candidate = _coerce_float(av.get("amount"))
                if candidate is not None:
                    awarded_amount = candidate
                    break
    resolved = awarded_amount if awarded_amount is not None else amount
    if resolved is None:
        return None
    return TenderValue(amount=resolved, currency=currency)


def _fts_classifications(tender: dict[str, Any]) -> list[TenderClassification]:
    out: list[TenderClassification] = []
    primary = tender.get("classification")
    if isinstance(primary, dict):
        code = primary.get("id")
        scheme = primary.get("scheme") or "CPV"
        desc = primary.get("description")
        if isinstance(code, str) and code.strip():
            out.append(
                TenderClassification(
                    scheme=scheme.upper() if isinstance(scheme, str) else "CPV",
                    code=code.strip(),
                    description=desc if isinstance(desc, str) else None,
                )
            )
    additional = tender.get("additionalClassifications")
    if isinstance(additional, list):
        for entry in additional:
            if not isinstance(entry, dict):
                continue
            code = entry.get("id")
            scheme = entry.get("scheme") or "CPV"
            desc = entry.get("description")
            if isinstance(code, str) and code.strip():
                out.append(
                    TenderClassification(
                        scheme=scheme.upper() if isinstance(scheme, str) else "CPV",
                        code=code.strip(),
                        description=desc if isinstance(desc, str) else None,
                    )
                )
    return out


def _fts_location(tender: dict[str, Any]) -> TenderLocation | None:
    items = tender.get("items")
    if not isinstance(items, list):
        return None
    for item in items:
        if not isinstance(item, dict):
            continue
        delivery = item.get("deliveryAddresses")
        addresses = delivery if isinstance(delivery, list) else []
        if isinstance(item.get("deliveryAddress"), dict):
            addresses = [item["deliveryAddress"], *addresses]
        for addr in addresses:
            if not isinstance(addr, dict):
                continue
            region = addr.get("region")
            postcode = addr.get("postalCode")
            parts = [
                addr.get("streetAddress"),
                addr.get("locality"),
                addr.get("region"),
                addr.get("postalCode"),
            ]
            address_parts = [p for p in parts if isinstance(p, str) and p.strip()]
            if region is None and postcode is None and not address_parts:
                continue
            return TenderLocation(
                region=region if isinstance(region, str) and region.strip() else None,
                postcode=postcode.strip().upper() if isinstance(postcode, str) else None,
                address=", ".join(p.strip() for p in address_parts)
                if address_parts
                else None,
                country_code=_resolve_country_code(addr),
            )
    return None


def normalise_fts_release(release: dict[str, Any]) -> Tender:
    """Map a Find a Tender OCDS ``Release`` dict to a canonical :class:`Tender`.

    Expects an OCDS 1.1.5-compatible release: ``{ocid, id, tag, date,
    tender: {...}, parties: [...], buyer: {...}, awards: [...]}``.
    Missing fields map to ``None`` / defaults rather than raising, so the
    function is safe on the partial payloads returned in pagination
    previews.
    """

    tender = release.get("tender") if isinstance(release, dict) else None
    if not isinstance(tender, dict):
        tender = {}
    tag = release.get("tag")
    tag_str: str | None = None
    if isinstance(tag, list):
        tag_str = ", ".join(str(t) for t in tag if isinstance(t, str))
        if not tag_str:
            tag_str = None
    elif isinstance(tag, str):
        tag_str = tag

    source_id_candidate = tender.get("id") or release.get("id")
    source_id = str(source_id_candidate) if source_id_candidate is not None else ""

    ocid = release.get("ocid")
    ocid_str = str(ocid) if isinstance(ocid, str) else None

    title = tender.get("title") or ""
    description = tender.get("description")

    status = _normalise_status(tender.get("status"), TenderSource.FIND_A_TENDER)

    published = _parse_datetime(release.get("date") or release.get("publishedDate"))
    closing = None
    tender_period = tender.get("tenderPeriod")
    if isinstance(tender_period, dict):
        closing = _parse_datetime(tender_period.get("endDate"))

    start_date = None
    end_date = None
    contract_period = tender.get("contractPeriod")
    if isinstance(contract_period, dict):
        start_date = _parse_date(contract_period.get("startDate"))
        end_date = _parse_date(contract_period.get("endDate"))

    url = None
    documents = tender.get("documents")
    if isinstance(documents, list):
        for doc in documents:
            if not isinstance(doc, dict):
                continue
            doc_url = doc.get("url")
            doc_type = doc.get("documentType")
            if isinstance(doc_url, str) and doc_url.strip() and doc_type in {
                "tenderNotice",
                "awardNotice",
                "noticeOfIntent",
            }:
                url = doc_url.strip()
                break
        if url is None:
            for doc in documents:
                if isinstance(doc, dict):
                    doc_url = doc.get("url")
                    if isinstance(doc_url, str) and doc_url.strip():
                        url = doc_url.strip()
                        break
    if url is None and ocid_str:
        url = f"https://www.find-tender.service.gov.uk/Notice/{ocid_str}"

    return Tender(
        source=TenderSource.FIND_A_TENDER,
        source_id=source_id,
        ocid=ocid_str,
        title=str(title).strip() if isinstance(title, str) else str(title),
        description=description if isinstance(description, str) else None,
        status=status,
        notice_type=tag_str,
        published_date=published,
        closing_date=closing,
        start_date=start_date,
        end_date=end_date,
        buyer=_fts_buyer(release),
        suppliers=_fts_suppliers(release),
        value=_fts_value(tender, release),
        classifications=_fts_classifications(tender),
        location=_fts_location(tender),
        url=url,
        raw=dict(release),
    )


__all__ = [
    "normalise_cf_notice",
    "normalise_fts_release",
]
