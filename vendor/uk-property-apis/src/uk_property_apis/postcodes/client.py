"""Async client for postcodes.io."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from urllib.parse import quote

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import NotFoundError
from uk_property_apis.postcodes.models import (
    BulkPostcodeResponse,
    OutcodeLookupResponse,
    OutcodeResult,
    PlaceResult,
    PlaceSearchResponse,
    PostcodeLookupResponse,
    PostcodeResult,
    PostcodeValidateResponse,
    ReverseGeocodeResponse,
)


def _normalise_postcode_path(postcode: str) -> str:
    return quote(postcode.strip().replace(" ", ""), safe="")


class PostcodesClient(BaseAPIClient):
    """Client for https://api.postcodes.io/ — free UK postcode geodata."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url="https://api.postcodes.io/",
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    async def lookup_postcode(self, postcode: str) -> PostcodeResult:
        """Return the full record for ``postcode``."""

        path = f"postcodes/{_normalise_postcode_path(postcode)}"
        payload = await self._get(path)
        parsed = self._validate_model(PostcodeLookupResponse, payload)
        if parsed.result is None:
            raise NotFoundError("Postcode not found", status_code=404)
        return parsed.result

    async def validate_postcode(self, postcode: str) -> bool:
        """Return whether ``postcode`` is syntactically valid and known."""

        path = f"postcodes/{_normalise_postcode_path(postcode)}/validate"
        payload = await self._get(path)
        parsed = self._validate_model(PostcodeValidateResponse, payload)
        return bool(parsed.result)

    async def bulk_lookup(self, postcodes: list[str]) -> BulkPostcodeResponse:
        """Bulk lookup up to 100 postcodes in one request."""

        if len(postcodes) > 100:
            msg = "postcodes.io allows at most 100 postcodes per bulk request"
            raise ValueError(msg)
        payload = await self._post("postcodes", json={"postcodes": postcodes})
        return self._validate_model(BulkPostcodeResponse, payload)

    async def reverse_geocode(self, lat: float, lon: float, *, radius_m: int = 100) -> ReverseGeocodeResponse:
        """Find postcodes near a coordinate within ``radius_m`` metres."""

        params = {"lat": lat, "lon": lon, "radius": radius_m}
        payload = await self._get("postcodes", params=params)
        return self._validate_model(ReverseGeocodeResponse, payload)

    async def lookup_outcode(self, outcode: str) -> OutcodeResult:
        """Return aggregate geography for an outward code (e.g. ``SW1A``)."""

        path = f"outcodes/{quote(outcode.strip(), safe='')}"
        payload = await self._get(path)
        parsed = self._validate_model(OutcodeLookupResponse, payload)
        if parsed.result is None:
            raise NotFoundError("Outcode not found", status_code=404)
        return parsed.result

    async def search_places(self, query: str, *, limit: int = 10) -> list[PlaceResult]:
        """Search OS Open Names places by free-text query.

        Backed by ``GET /places?q=<query>``. Matches cities, towns,
        villages, hamlets and neighbourhood features — each result
        carries ``latitude`` / ``longitude`` which can be chained into
        :meth:`reverse_geocode` to turn a place name into one or more
        nearby postcodes. The top result is usually the most populous
        / highest-ranked OS feature but callers should inspect
        ``local_type`` and ``region`` before picking one (e.g. the
        Cambridgeshire city vs. the Gloucestershire village).
        """

        if not query.strip():
            raise ValueError("search_places requires a non-empty query")
        params = {"q": query.strip(), "limit": limit}
        payload = await self._get("places", params=params)
        parsed = self._validate_model(PlaceSearchResponse, payload)
        return list(parsed.result or [])


async def lookup_postcode(postcode: str) -> PostcodeResult:
    """Convenience: single-shot lookup using a fresh client."""

    async with PostcodesClient() as client:
        return await client.lookup_postcode(postcode)


async def validate_postcode(postcode: str) -> bool:
    """Convenience: validate a postcode."""

    async with PostcodesClient() as client:
        return await client.validate_postcode(postcode)
