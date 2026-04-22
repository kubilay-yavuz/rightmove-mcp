"""Shared crawler types: fetch results, exceptions, transport protocol.

``FetcherError`` is imported from the public :mod:`uk_property_listings`
package so that pagination loops in public code can catch failures raised by
the private production crawler via the shared base class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from uk_property_listings import FetcherError


class FetcherTier(StrEnum):
    """Which fetcher produced (or tried to produce) a result."""

    HTTP = "http"
    BROWSER = "browser"


@dataclass(slots=True)
class TransportResponse:
    """Minimal HTTP response shape shared across transports.

    Kept deliberately narrow so both ``curl_cffi`` and ``httpx`` adaptors can
    produce it cheaply.
    """

    status_code: int
    url: str
    """Final URL after any redirects."""
    headers: dict[str, str]
    text: str
    content: bytes
    elapsed_s: float


@dataclass(slots=True)
class FetchAttempt:
    """One round-trip attempt - survives into :class:`FetchResult.attempts`
    for observability."""

    tier: FetcherTier
    started_at: datetime
    duration_ms: int
    status_code: int | None
    """HTTP status of the response, or ``None`` if the transport failed before
    a response was received (timeout / DNS / reset)."""
    final_url: str | None
    error: str | None = None
    """Short textual reason when this attempt did *not* yield usable HTML."""
    anti_bot_signal: str | None = None
    """Name of the detected anti-bot signal, if any (e.g. ``cf_challenge``)."""


@dataclass(slots=True)
class FetchResult:
    """Successful crawl output - the HTML and enough metadata to trace it."""

    url: str
    final_url: str
    status_code: int
    html: str
    tier: FetcherTier
    captured_at: datetime
    duration_ms: int
    attempts: list[FetchAttempt] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


class BlockedError(FetcherError):
    """Anti-bot detected - tier escalation or abort required.

    :attr:`signal` is the detection signal name (e.g. ``cf_challenge``), kept
    at module level so alerting and tests can match on it. :attr:`attempts`
    captures the per-tier attempts made before the tier gave up, so
    :class:`TierExhaustedError` can surface an accurate count rather than
    claiming zero attempts were made.
    """

    def __init__(
        self,
        signal: str,
        *,
        url: str,
        status_code: int | None,
        attempts: list[FetchAttempt] | None = None,
    ) -> None:
        self.signal = signal
        self.url = url
        self.status_code = status_code
        self.attempts: list[FetchAttempt] = list(attempts) if attempts else []
        super().__init__(f"Blocked by {signal} at {url} (status={status_code})")


class TierExhaustedError(FetcherError):
    """All configured tiers failed on this URL."""

    def __init__(self, url: str, attempts: list[FetchAttempt]) -> None:
        self.url = url
        self.attempts = attempts
        super().__init__(f"All fetch tiers exhausted for {url} ({len(attempts)} attempts)")


class Transport(Protocol):
    """Abstract HTTP transport.

    The production adaptor is :class:`CurlCffiTransport`, which uses
    ``curl_cffi`` to impersonate a real Chrome TLS/HTTP2 fingerprint. Tests
    use :class:`HttpxTransport` so ``respx`` can mock responses cleanly.
    """

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        """Issue a single request. Implementations MUST follow redirects."""
        ...

    async def aclose(self) -> None:
        """Release underlying resources."""
        ...
