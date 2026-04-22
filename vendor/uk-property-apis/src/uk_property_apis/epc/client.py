"""Async client for EPC Open Data Communities API."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import ValidationError
from uk_property_apis.epc.models import EPCCertificateRow, EPCSearchPage


def _auth_from_env() -> httpx.BasicAuth | None:
    email = os.environ.get("EPC_AUTH_EMAIL")
    token = os.environ.get("EPC_AUTH_TOKEN")
    if email and token:
        return httpx.BasicAuth(email, token)
    return None


class EPCClient(BaseAPIClient):
    """Client for https://epc.opendatacommunities.org/api/v1/ (Basic auth)."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 60.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        resolved = auth if auth is not None else _auth_from_env()
        if resolved is None:
            msg = "EPC credentials required: pass auth=httpx.BasicAuth(email, token) or set EPC_AUTH_EMAIL and EPC_AUTH_TOKEN"
            raise ValueError(msg)
        merged_headers = {"Accept": "application/json", **(dict(headers) if headers else {})}
        super().__init__(
            base_url="https://epc.opendatacommunities.org/api/v1/",
            auth=resolved,
            timeout=timeout,
            semaphore=semaphore,
            headers=merged_headers,
        )

    async def _search(
        self,
        path: str,
        *,
        params: dict[str, Any],
    ) -> EPCSearchPage:
        response, data = await self._get_with_response(path, params=params)
        if isinstance(data, list):
            rows_data = data
        elif isinstance(data, dict) and "rows" in data:
            rows_obj = data["rows"]
            if not isinstance(rows_obj, list):
                raise ValidationError("EPC search 'rows' must be a list")
            rows_data = rows_obj
        else:
            raise ValidationError("EPC search response must be a list or an object with 'rows'")
        rows = [self._validate_model(EPCCertificateRow, row) for row in rows_data]
        next_token = response.headers.get("x-next-search-after")
        return EPCSearchPage(rows=rows, next_search_after=next_token)

    async def search_domestic(
        self,
        *,
        postcode: str | None = None,
        size: int = 1000,
        search_after: str | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> EPCSearchPage:
        """Search domestic EPCs (paginated via ``search_after`` / ``X-Next-Search-After``)."""

        params: dict[str, Any] = {"size": size}
        if postcode is not None:
            params["postcode"] = postcode
        if search_after is not None:
            params["search-after"] = search_after
        if extra_params:
            params.update(dict(extra_params))
        return await self._search("domestic/search", params=params)

    async def get_domestic_certificate(self, lmk_key: str) -> EPCCertificateRow:
        """Fetch a single domestic certificate by ``lmk-key``."""

        path = f"domestic/certificate/{lmk_key}"
        data = await self._request_json("GET", path)
        if isinstance(data, list) and data:
            return self._validate_model(EPCCertificateRow, data[0])
        if isinstance(data, dict):
            return self._validate_model(EPCCertificateRow, data)
        raise ValidationError("Unexpected domestic certificate payload")

    async def search_non_domestic(
        self,
        *,
        postcode: str | None = None,
        size: int = 1000,
        search_after: str | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> EPCSearchPage:
        """Search non-domestic EPCs."""

        params: dict[str, Any] = {"size": size}
        if postcode is not None:
            params["postcode"] = postcode
        if search_after is not None:
            params["search-after"] = search_after
        if extra_params:
            params.update(dict(extra_params))
        return await self._search("non-domestic/search", params=params)

    async def search_display(
        self,
        *,
        postcode: str | None = None,
        size: int = 1000,
        search_after: str | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> EPCSearchPage:
        """Search display / DEC certificates."""

        params: dict[str, Any] = {"size": size}
        if postcode is not None:
            params["postcode"] = postcode
        if search_after is not None:
            params["search-after"] = search_after
        if extra_params:
            params.update(dict(extra_params))
        return await self._search("display/search", params=params)
