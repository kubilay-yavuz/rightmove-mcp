"""Models for Land Registry linked-data JSON endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PricePaidRecord(BaseModel):
    """Normalised price-paid transaction suitable for analytics pipelines."""

    model_config = ConfigDict(extra="ignore")

    transaction_id: str
    price: int
    transfer_date: str
    property_type: str | None = None
    new_build: bool | None = None
    tenure: str | None = None
    paon: str | None = None
    saon: str | None = None
    street: str | None = None
    locality: str | None = None
    town: str | None = None
    district: str | None = None
    county: str | None = None
    postcode: str | None = None


class TransactionListItem(BaseModel):
    """Minimal stub returned in ``transaction.json`` list pages."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    transaction_id: str | None = Field(default=None, alias="transactionId")
    about: str | None = Field(default=None, alias="_about")
    has_transaction_record: str | None = Field(default=None, alias="hasTransactionRecord")


class TransactionListPage(BaseModel):
    """Page metadata for ``/data/ppi/transaction.json``."""

    model_config = ConfigDict(extra="allow")

    items: list[dict[str, Any]] = Field(default_factory=list)
    next_url: str | None = Field(default=None, alias="next")
    page: int | None = None
    items_per_page: int | None = Field(default=None, alias="itemsPerPage")
