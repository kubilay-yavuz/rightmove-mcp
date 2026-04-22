"""Async client for Land Registry price-paid linked-data JSON endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis.land_registry._parse import extract_transaction_id, primary_topic_to_record
from uk_property_apis.land_registry.models import PricePaidRecord, TransactionListPage


class LandRegistryClient(BaseAPIClient):
    """Client for Land Registry PPD JSON under ``/data/ppi/``."""

    def __init__(
        self,
        *,
        auth: httpx.Auth | None = None,
        timeout: float = 60.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            base_url="https://landregistry.data.gov.uk/",
            auth=auth,
            timeout=timeout,
            semaphore=semaphore,
            headers=headers,
        )

    def _parse_list_envelope(self, payload: dict[str, Any]) -> TransactionListPage:
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            msg = "Land Registry response missing 'result' object"
            raise ValueError(msg)
        items = result.get("items") or []
        if not isinstance(items, list):
            msg = "Land Registry 'items' must be a list"
            raise ValueError(msg)
        return TransactionListPage(
            items=items,
            next_url=result.get("next"),
            page=result.get("page"),
            items_per_page=result.get("itemsPerPage"),
        )

    async def fetch_transactions_page(
        self,
        *,
        postcode: str | None = None,
        page_size: int = 100,
        page: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> TransactionListPage:
        """Fetch one page of the ``transaction.json`` list endpoint."""

        params: dict[str, Any] = {"_pageSize": page_size}
        if postcode is not None:
            params["propertyAddress.postcode"] = postcode
        if page is not None:
            params["_page"] = page
        if extra_params:
            params.update(dict(extra_params))
        query = urlencode(params)
        path = f"data/ppi/transaction.json?{query}"
        payload = await self._get(path)
        return self._parse_list_envelope(payload)

    async def get_transaction_current(self, transaction_id: str) -> PricePaidRecord:
        """Fetch the ``current`` record for a transaction UUID."""

        safe = quote(transaction_id.strip(), safe="")
        path = f"data/ppi/transaction/{safe}/current.json"
        payload = await self._get(path)
        result = payload.get("result")
        if not isinstance(result, dict):
            from uk_property_apis._core.exceptions import ValidationError

            raise ValidationError("Missing result envelope")
        topic = result.get("primaryTopic")
        if not isinstance(topic, dict):
            from uk_property_apis._core.exceptions import ValidationError

            raise ValidationError("Missing primaryTopic")
        flat = primary_topic_to_record(topic)
        return self._validate_model(PricePaidRecord, flat)

    async def fetch_transaction_records_page(
        self,
        *,
        postcode: str | None = None,
        page_size: int = 100,
        page: int | None = None,
        extra_params: Mapping[str, Any] | None = None,
    ) -> list[PricePaidRecord]:
        """Fetch one page of ``transaction-record.json``.

        Unlike ``transaction.json``, this endpoint:

        1. Supports ``propertyAddress.*`` filters (the plain ``transaction.json``
           list endpoint silently returns 0 items for postcode filters).
        2. Returns fully-expanded records in each item - no N+1 follow-up
           calls to ``/transaction/<id>/current.json`` needed.
        """

        params: dict[str, Any] = {"_pageSize": page_size}
        if postcode is not None:
            params["propertyAddress.postcode"] = postcode
        if page is not None:
            params["_page"] = page
        if extra_params:
            params.update(dict(extra_params))
        query = urlencode(params)
        path = f"data/ppi/transaction-record.json?{query}"
        payload = await self._get(path)
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            return []
        items = result.get("items") or []
        if not isinstance(items, list):
            return []
        out: list[PricePaidRecord] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            flat = primary_topic_to_record(item)
            out.append(self._validate_model(PricePaidRecord, flat))
        return out

    async def search_by_postcode(
        self,
        postcode: str,
        *,
        page_size: int = 100,
        expand: bool = True,
    ) -> list[PricePaidRecord]:
        """Return price-paid rows for ``postcode``.

        Uses the single-call ``transaction-record.json`` endpoint which already
        returns expanded rows. The ``expand`` parameter is retained for API
        compatibility; when ``expand=False`` only the transaction ids / stubs
        are returned (useful if you only need to enumerate).
        """

        if expand:
            return await self.fetch_transaction_records_page(
                postcode=postcode, page_size=page_size
            )

        # Enumeration-only path: hit the plain transaction.json list and
        # return id-only stubs, matching the historical behaviour.
        page = await self.fetch_transactions_page(postcode=postcode, page_size=page_size)
        stubs: list[PricePaidRecord] = []
        for item in page.items:
            if not isinstance(item, dict):
                continue
            tid = extract_transaction_id(item)
            if tid:
                stubs.append(
                    self._validate_model(
                        PricePaidRecord,
                        {"transaction_id": tid, "price": 0, "transfer_date": ""},
                    )
                )
        return stubs
