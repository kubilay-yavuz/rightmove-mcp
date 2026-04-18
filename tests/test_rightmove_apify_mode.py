"""Tests for the Apify-delegation path in ``rightmove_mcp.apify_mode``.

Mirror of the zoopla-mcp Apify-mode test suite. See
``zoopla-mcp/tests/test_zoopla_apify_mode.py`` for design notes; this file
differs only in the actor key / source string.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from uk_property_apify_client import ApifyDelegation, DelegationError

from rightmove_mcp.apify_mode import (
    _build_actor_input,
    _map_result_to_output,
    maybe_delegate_search_listings,
)
from rightmove_mcp.tools import SearchListingsInput, search_listings


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "APIFY_API_TOKEN",
        "APIFY_USERNAME",
        "UK_PROPERTY_APIFY_MODE",
        "APIFY_ACTOR_RIGHTMOVE_LISTINGS",
    ]:
        monkeypatch.delenv(name, raising=False)


@dataclass
class _FakeActor:
    parent: _FakeApifyClient
    actor_id: str

    async def call(self, **kwargs: Any) -> dict[str, Any] | None:
        self.parent.last_actor_id = self.actor_id
        self.parent.last_run_input = kwargs["run_input"]
        return self.parent.run_response


class _FakeDataset:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    async def iterate_items(self):
        for item in self._items:
            yield item


class _FakeKv:
    def __init__(self, records: dict[str, Any]) -> None:
        self._records = records

    async def get_record(self, key: str) -> dict[str, Any] | None:
        if key not in self._records:
            return None
        return {"key": key, "value": self._records[key]}


@dataclass
class _FakeApifyClient:
    run_response: dict[str, Any] | None = None
    ds_items: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    kv_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_actor_id: str | None = None
    last_run_input: dict[str, Any] | None = None

    def actor(self, actor_id: str) -> _FakeActor:
        return _FakeActor(parent=self, actor_id=actor_id)

    def dataset(self, dataset_id: str) -> _FakeDataset:
        return _FakeDataset(self.ds_items.get(dataset_id, []))

    def key_value_store(self, kv_id: str) -> _FakeKv:
        return _FakeKv(self.kv_records.get(kv_id, {}))


def _rightmove_item(*, listing_id: str = "r1", price: int = 500_000) -> dict[str, Any]:
    return {
        "source": "rightmove",
        "source_id": listing_id,
        "source_url": f"https://www.rightmove.co.uk/properties/{listing_id}",
        "listing_type": "search_card",
        "transaction_type": "sale",
        "sale_price": {
            "amount_pence": price * 100,
            "qualifier": "asking_price",
            "raw": f"£{price:,}",
        },
        "address": {"raw": "1 Example Street, Cambridge, CB1 2QA"},
        "title": f"Listing {listing_id}",
    }


class TestMaybeDelegateOff:
    async def test_no_env_returns_none(self) -> None:
        result = await maybe_delegate_search_listings(
            SearchListingsInput(location="Cambridge"),
        )
        assert result is None


class TestBuildActorInput:
    def test_minimal_input_camelcased(self) -> None:
        actor_input = _build_actor_input(SearchListingsInput(location="Cambridge"))
        assert actor_input["queries"] == [{"location": "Cambridge", "transaction": "sale"}]
        assert actor_input["maxPagesPerQuery"] == 1
        assert actor_input["hydrateDetails"] is False

    def test_full_input_camelcased(self) -> None:
        actor_input = _build_actor_input(
            SearchListingsInput(
                location="Manchester",
                transaction="rent",
                min_price=1000,
                max_price=2500,
                min_beds=2,
                max_beds=4,
                max_pages=3,
                hydrate_details=True,
            )
        )
        assert actor_input["queries"][0]["minBeds"] == 2
        assert actor_input["queries"][0]["maxPrice"] == 2500
        assert actor_input["maxPagesPerQuery"] == 3
        assert actor_input["hydrateDetails"] is True


class TestMapResultToOutput:
    def test_valid_items_parse(self) -> None:
        items = [_rightmove_item(listing_id="r1"), _rightmove_item(listing_id="r2")]
        run_meta = {"totals": {"pages_fetched": 2, "errors": 0}}
        output = _map_result_to_output(items, run_meta)
        assert output.pages_fetched == 2
        assert [lst.source_id for lst in output.listings] == ["r1", "r2"]

    def test_foreign_source_skipped(self) -> None:
        items = [_rightmove_item(), {"source": "zoopla", "source_id": "z1"}]
        output = _map_result_to_output(items, None)
        assert len(output.listings) == 1
        assert any("zoopla" in e for e in output.errors)


class TestEndToEndDelegation:
    async def test_delegation_replaces_crawler_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        monkeypatch.setenv("APIFY_USERNAME", "me")

        captured: dict[str, Any] = {}
        fake = _FakeApifyClient(
            run_response={
                "id": "run_1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "ds",
                "defaultKeyValueStoreId": "kv",
            },
            ds_items={"ds": [_rightmove_item(listing_id="r1")]},
            kv_records={"kv": {"RUN_META": {"totals": {"pages_fetched": 1}}}},
        )

        original_call = ApifyDelegation.call

        async def patched_call(
            self: ApifyDelegation,
            actor_input: dict[str, Any],
            *,
            client_factory: Any = None,
        ):
            captured["actor_id"] = self.actor_id.full_id
            return await original_call(self, actor_input, client_factory=lambda _: fake)

        monkeypatch.setattr(ApifyDelegation, "call", patched_call)

        @asynccontextmanager
        async def _exploding_factory():
            raise AssertionError("crawler_factory must not run when delegating")
            yield  # pragma: no cover

        result = await search_listings(
            SearchListingsInput(location="Cambridge"),
            crawler_factory=_exploding_factory,
        )

        assert captured["actor_id"] == "me~rightmove-listings"
        assert result.pages_fetched == 1
        assert len(result.listings) == 1
        assert result.listings[0].source_id == "r1"

    async def test_failed_run_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        fake = _FakeApifyClient(run_response={"id": "r", "status": "ABORTED"})

        original_call = ApifyDelegation.call

        async def patched_call(
            self: ApifyDelegation,
            actor_input: dict[str, Any],
            *,
            client_factory: Any = None,
        ):
            return await original_call(self, actor_input, client_factory=lambda _: fake)

        monkeypatch.setattr(ApifyDelegation, "call", patched_call)

        with pytest.raises(DelegationError, match="ABORTED"):
            await maybe_delegate_search_listings(
                SearchListingsInput(location="Cambridge"),
            )
