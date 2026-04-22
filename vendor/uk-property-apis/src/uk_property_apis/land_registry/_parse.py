"""Helpers for Land Registry JSON-LD shaped payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _lang_value(node: Any) -> str | None:
    if isinstance(node, dict) and "_value" in node:
        return str(node["_value"])
    if isinstance(node, str):
        return node
    return None


def pref_label(obj: Any) -> str | None:
    """Extract a human-readable label from a linked-data node."""

    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict):
        return None
    pref = obj.get("prefLabel")
    if isinstance(pref, list) and pref:
        return _lang_value(pref[0])
    label = obj.get("label")
    if isinstance(label, list) and label:
        return _lang_value(label[0])
    return None


def parse_transaction_date(raw: str | None) -> str:
    """Normalise Land Registry HTTP-date style strings to ``YYYY-MM-DD``."""

    if not raw:
        return ""
    for fmt in ("%a, %d %b %Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return raw


def primary_topic_to_record(topic: dict[str, Any]) -> dict[str, Any]:
    """Map ``primaryTopic`` from ``.../current.json`` into flat dict for :class:`PricePaidRecord`."""

    addr = topic.get("propertyAddress") or {}
    if not isinstance(addr, dict):
        addr = {}
    return {
        "transaction_id": str(topic.get("transactionId") or ""),
        "price": int(topic.get("pricePaid") or 0),
        "transfer_date": parse_transaction_date(topic.get("transactionDate")),
        "property_type": pref_label(topic.get("propertyType")),
        "new_build": topic.get("newBuild"),
        "tenure": pref_label(topic.get("estateType")),
        "paon": addr.get("paon"),
        "saon": addr.get("saon"),
        "street": addr.get("street"),
        "locality": addr.get("locality"),
        "town": addr.get("town"),
        "district": addr.get("district"),
        "county": addr.get("county"),
        "postcode": addr.get("postcode"),
    }


def extract_transaction_id(list_item: dict[str, Any]) -> str | None:
    """Pull a UUID transaction id from a list ``items`` row."""

    tid = list_item.get("transactionId")
    if isinstance(tid, str):
        return tid
    about = list_item.get("_about")
    if isinstance(about, str) and "/transaction/" in about:
        return about.rsplit("/", 1)[-1]
    return None
