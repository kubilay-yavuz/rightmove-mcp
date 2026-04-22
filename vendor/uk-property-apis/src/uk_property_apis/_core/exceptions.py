"""Typed exception hierarchy for UK property API clients."""

from __future__ import annotations


class UKPropertyAPIError(Exception):
    """Base class for all package-specific API failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthError(UKPropertyAPIError):
    """Raised when the remote API rejects credentials (HTTP 401/403)."""


class NotFoundError(UKPropertyAPIError):
    """Raised when the requested resource does not exist (HTTP 404)."""


class RateLimitError(UKPropertyAPIError):
    """Raised when rate limits are hit after retries are exhausted (HTTP 429)."""


class ServerError(UKPropertyAPIError):
    """Raised when the server returns a 5xx after retries are exhausted."""


class ValidationError(UKPropertyAPIError):
    """Raised when a response cannot be parsed into the expected Pydantic model."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message, status_code=status_code)


class TransportError(UKPropertyAPIError):
    """Raised for persistent network / transport failures after retries."""
