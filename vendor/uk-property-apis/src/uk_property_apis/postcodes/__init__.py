"""postcodes.io client."""

from __future__ import annotations

from uk_property_apis.postcodes.client import PostcodesClient, lookup_postcode, validate_postcode
from uk_property_apis.postcodes.models import (
    BulkPostcodeResponse,
    NearestPostcodeItem,
    OutcodeLookupResponse,
    OutcodeResult,
    PlaceResult,
    PlaceSearchResponse,
    PostcodeLookupResponse,
    PostcodeResult,
    PostcodeValidateResponse,
    ReverseGeocodeResponse,
)

__all__ = [
    "BulkPostcodeResponse",
    "NearestPostcodeItem",
    "OutcodeLookupResponse",
    "OutcodeResult",
    "PlaceResult",
    "PlaceSearchResponse",
    "PostcodeLookupResponse",
    "PostcodeResult",
    "PostcodeValidateResponse",
    "PostcodesClient",
    "ReverseGeocodeResponse",
    "lookup_postcode",
    "validate_postcode",
]
