"""Convert a canonical :class:`~uk_property_scrapers.schema.Listing` to a :class:`ListingSnapshot`.

We persist a deliberately *small* subset of the full listing in the
delta store: just the fields that meaningfully move over a listing's
life-cycle (price, status, photos, description, agent, features,
physical attributes). Everything else is recoverable by re-fetching
the source URL, so we don't pay for it in SQLite row size.

The ``fingerprint`` we compute here is what the diff engine uses to
skip no-op inserts: if the new snapshot has the same fingerprint as
the previous one, nothing interesting changed and we don't bother
writing a new row.
"""

from __future__ import annotations

from datetime import datetime

from uk_property_scrapers.schema import (
    Listing,
    ListingSnapshot,
)

from uk_property_apify_shared.delta.fingerprint import (
    fingerprint_description,
    fingerprint_image_url,
    fingerprint_payload,
)

__all__ = ["snapshot_from_listing"]


def _price_pence(listing: Listing) -> int | None:
    """Return the transaction-appropriate price in pence, or ``None``."""
    if listing.sale_price is not None:
        return listing.sale_price.amount_pence
    if listing.rent_price is not None:
        return listing.rent_price.amount_pence
    return None


def _price_qualifier(listing: Listing):
    if listing.sale_price is not None:
        return listing.sale_price.qualifier
    if listing.rent_price is not None:
        return listing.rent_price.qualifier
    return None


def snapshot_from_listing(
    listing: Listing,
    *,
    status_text: str | None = None,
    captured_at: datetime | None = None,
) -> ListingSnapshot:
    """Project a :class:`Listing` onto a :class:`ListingSnapshot`.

    Args:
        listing: The source listing to snapshot.
        status_text: Optional raw status ribbon (``"Reduced on 13/04/2026"``,
            ``"Sold STC"``). Portals expose this in different places
            (Zoopla's ``statusText``, Rightmove's ``displayStatus``,
            OTM's ribbon) — the caller resolves the per-portal field.
        captured_at: Override the capture timestamp; defaults to now.

    Returns:
        A :class:`ListingSnapshot` with a computed, deterministic
        ``fingerprint`` that omits ``captured_at`` — so a listing
        re-fetched an hour later with no real change produces the same
        fingerprint.
    """
    image_fingerprints = [
        fingerprint_image_url(str(image.url)) for image in listing.image_urls
    ]
    description_fp = fingerprint_description(listing.description)
    features_sorted = sorted(f.value for f in listing.features)

    price_qualifier = _price_qualifier(listing)
    payload = {
        "source": listing.source.value,
        "source_id": listing.source_id,
        "price_pence": _price_pence(listing),
        "price_qualifier": price_qualifier.value if price_qualifier else None,
        "features": features_sorted,
        "image_count": listing.image_count,
        "image_fingerprints": image_fingerprints,
        "description_fingerprint": description_fp,
        "bedrooms": listing.bedrooms,
        "bathrooms": listing.bathrooms,
        "reception_rooms": listing.reception_rooms,
        "floor_area_sqft": listing.floor_area_sqft,
        "property_type": listing.property_type.value,
        "tenure": listing.tenure.value,
        "agent_source_id": listing.agent.source_id if listing.agent else None,
        "status_text": (status_text or "").strip() or None,
    }
    fingerprint = fingerprint_payload(payload)

    kwargs = {
        "source": listing.source,
        "source_id": listing.source_id,
        "source_url": listing.source_url,
        "fingerprint": fingerprint,
        "price_pence": payload["price_pence"],
        "features": list(listing.features),
        "image_count": listing.image_count,
        "image_fingerprints": image_fingerprints,
        "description_fingerprint": description_fp,
        "bedrooms": listing.bedrooms,
        "bathrooms": listing.bathrooms,
        "reception_rooms": listing.reception_rooms,
        "floor_area_sqft": listing.floor_area_sqft,
        "property_type": listing.property_type,
        "tenure": listing.tenure,
        "agent_source_id": listing.agent.source_id if listing.agent else None,
        "status_text": payload["status_text"],
    }
    if price_qualifier is not None:
        kwargs["price_qualifier"] = price_qualifier
    if captured_at is not None:
        kwargs["captured_at"] = captured_at

    return ListingSnapshot(**kwargs)
