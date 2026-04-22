"""Anti-bot response classifier.

Looks at HTTP status + HTML signatures + headers to decide whether we got a
real page, a soft-block (CF challenge / login wall), or a hard block (403
access denied). Every detection is encoded as an :class:`AntiBotSignal` so
alerts and tests can match on stable names rather than ad-hoc strings.

This module is deliberately **pure** - no network, no logging - so both the
HTTP and browser fetchers can feed their responses into the same classifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class AntiBotSignal(StrEnum):
    """Named anti-bot detections; keep stable for alerting/dashboards."""

    OK = "ok"
    CF_CHALLENGE = "cf_challenge"
    """Cloudflare JS challenge / Turnstile captcha."""
    CF_ACCESS_DENIED = "cf_access_denied"
    """Hard 1020-style Cloudflare access denial."""
    AKAMAI_BOT_MANAGER = "akamai_bot_manager"
    """Akamai bot manager block page."""
    DATADOME_BLOCK = "datadome_block"
    """DataDome challenge or block page."""
    PERIMETERX_BLOCK = "perimeterx_block"
    """PerimeterX/HUMAN Security challenge or block page."""
    RECAPTCHA = "recaptcha"
    """Google reCAPTCHA v2/v3 gate."""
    LOGIN_WALL = "login_wall"
    """Site is asking us to log in before revealing content."""
    RATE_LIMITED = "rate_limited"
    """HTTP 429 or explicit "too many requests" messaging."""
    FORBIDDEN = "forbidden"
    """HTTP 403 with no recognisable WAF signature."""
    EMPTY_RESULT = "empty_result"
    """200 OK but the body is suspiciously small or lacks expected markers."""


@dataclass(slots=True, frozen=True)
class AntiBotVerdict:
    """Result of classifying a response."""

    signal: AntiBotSignal
    blocked: bool
    """``True`` if the caller should treat this as a block and retry/escalate."""
    reason: str
    """Short human-readable explanation."""


_MIN_BYTES_FOR_PORTAL = 20_000
"""Real portal search/detail pages are always >> 20kB; tiny 200s are suspect."""

_CF_CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "cf-challenge",
    "challenges.cloudflare.com",
    "/cdn-cgi/challenge-platform",
    "cf_chl_opt",
    "Just a moment...",
    "Please complete the security check",
    "Attention Required! | Cloudflare",
    "turnstile-widget",
)
_CF_ACCESS_DENIED_MARKERS = (
    "error code: 1020",
    "Error 1020",
    "Ray ID: ",
    "Sorry, you have been blocked",
)
_AKAMAI_MARKERS = (
    "reference #18.",
    "Access Denied | akamai",
    "/_Incapsula_Resource",
    "ak-challenge",
)
_DATADOME_MARKERS = (
    "datadome",
    "captcha-delivery.com",
    "dd-sjk",
)
_PERIMETERX_MARKERS = (
    "_pxCaptcha",
    "perimeterx",
    "/_px/",
    "captcha.perimeterx.net",
)
_RECAPTCHA_MARKERS = (
    "www.google.com/recaptcha",
    "g-recaptcha",
    "grecaptcha.execute",
)
_LOGIN_WALL_MARKERS = (
    "Please sign in to continue",
    "You must be signed in",
    'action="/sign-in"',
    "log in to view this property",
)
_RATE_LIMIT_MARKERS = (
    "Too many requests",
    "rate limit exceeded",
    "Retry after",
)

_SEARCH_RESULT_MARKERS: tuple[str, ...] = (
    "regular-listings",
    "listing-card-content",
    "propertycard-",
    "search-result-property-card",
    "propertyCard-wrapper",
    "l-searchResult",
    "result-property-card",
    "data-testid=\"listing-card",
    "data-component=\"search-result-property-card\"",
    "data-testid=\"listing",
)


def _body_has(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(m.lower() in lowered for m in markers)


_WHITESPACE_RE = re.compile(r"\s+")


def classify_response(
    *,
    status_code: int,
    html: str,
    headers: dict[str, str] | None = None,
    expect_search_markers: bool = False,
) -> AntiBotVerdict:
    """Classify an HTTP response.

    Args:
        status_code: HTTP response status.
        html: Decoded response body.
        headers: Response headers (keys are expected to be lowercase).
        expect_search_markers: If ``True`` a 200 response that doesn't contain
            any known portal search-result marker is flagged as
            :attr:`AntiBotSignal.EMPTY_RESULT`.

    Returns:
        An :class:`AntiBotVerdict`. A :attr:`~AntiBotVerdict.blocked` value of
        ``True`` means the caller should escalate (HTTP -> browser) or surface
        an alert.
    """
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}

    if status_code == 429 or _body_has(html, _RATE_LIMIT_MARKERS):
        return AntiBotVerdict(
            AntiBotSignal.RATE_LIMITED,
            blocked=True,
            reason="HTTP 429 / rate limit messaging",
        )

    if status_code in (401, 407):
        return AntiBotVerdict(
            AntiBotSignal.FORBIDDEN,
            blocked=True,
            reason=f"HTTP {status_code}",
        )

    if _body_has(html, _CF_ACCESS_DENIED_MARKERS):
        return AntiBotVerdict(
            AntiBotSignal.CF_ACCESS_DENIED,
            blocked=True,
            reason="Cloudflare access denied (1020-style)",
        )

    server = hdrs.get("server", "").lower()
    if "cloudflare" in server and _body_has(html, _CF_CHALLENGE_MARKERS):
        return AntiBotVerdict(
            AntiBotSignal.CF_CHALLENGE,
            blocked=True,
            reason="Cloudflare challenge page",
        )
    if _body_has(html, _CF_CHALLENGE_MARKERS):
        return AntiBotVerdict(
            AntiBotSignal.CF_CHALLENGE,
            blocked=True,
            reason="Cloudflare challenge markers",
        )

    if _body_has(html, _AKAMAI_MARKERS):
        return AntiBotVerdict(
            AntiBotSignal.AKAMAI_BOT_MANAGER,
            blocked=True,
            reason="Akamai bot-manager block",
        )
    if _body_has(html, _DATADOME_MARKERS):
        return AntiBotVerdict(
            AntiBotSignal.DATADOME_BLOCK,
            blocked=True,
            reason="DataDome block/challenge",
        )
    if _body_has(html, _PERIMETERX_MARKERS):
        return AntiBotVerdict(
            AntiBotSignal.PERIMETERX_BLOCK,
            blocked=True,
            reason="PerimeterX block/challenge",
        )
    if _body_has(html, _RECAPTCHA_MARKERS):
        return AntiBotVerdict(
            AntiBotSignal.RECAPTCHA,
            blocked=True,
            reason="reCAPTCHA gate",
        )
    if _body_has(html, _LOGIN_WALL_MARKERS):
        return AntiBotVerdict(
            AntiBotSignal.LOGIN_WALL,
            blocked=True,
            reason="Login wall detected",
        )

    if status_code == 403:
        return AntiBotVerdict(
            AntiBotSignal.FORBIDDEN,
            blocked=True,
            reason="HTTP 403 with no known WAF signature",
        )

    if 500 <= status_code < 600:
        return AntiBotVerdict(
            AntiBotSignal.OK,
            blocked=False,
            reason=f"HTTP {status_code} (transient upstream)",
        )

    if status_code == 200:
        compacted = _WHITESPACE_RE.sub(" ", html).strip()
        if len(compacted) < _MIN_BYTES_FOR_PORTAL:
            return AntiBotVerdict(
                AntiBotSignal.EMPTY_RESULT,
                blocked=True,
                reason=f"200 OK but body is {len(compacted)} chars (< {_MIN_BYTES_FOR_PORTAL})",
            )
        if expect_search_markers and not _body_has(html, _SEARCH_RESULT_MARKERS):
            return AntiBotVerdict(
                AntiBotSignal.EMPTY_RESULT,
                blocked=True,
                reason="200 OK but no portal search markers present",
            )

    return AntiBotVerdict(AntiBotSignal.OK, blocked=False, reason="ok")
