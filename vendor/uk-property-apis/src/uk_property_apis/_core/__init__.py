"""Shared infrastructure for HTTP clients."""

from __future__ import annotations

from uk_property_apis._core.base_client import BaseAPIClient
from uk_property_apis._core.exceptions import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TransportError,
    UKPropertyAPIError,
    ValidationError,
)
from uk_property_apis._core.rate_limit import AsyncTokenBucket

__all__ = [
    "AsyncTokenBucket",
    "AuthError",
    "BaseAPIClient",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "TransportError",
    "UKPropertyAPIError",
    "ValidationError",
]
