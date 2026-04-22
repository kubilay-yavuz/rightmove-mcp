"""Captcha solver abstraction.

The portals we target (Zoopla, Rightmove, OnTheMarket) mostly surface
inquiries through plain HTML forms, but all three gate the final POST
behind some combination of:

* Cloudflare Turnstile (invisible bot check — the common one)
* reCAPTCHA v2 / v3
* hCaptcha
* A bespoke "I'm not a robot" click challenge

The :class:`FormSubmitter` should *not* know how any of these are solved;
it only knows *when* one has appeared and how to feed the solution back
into the page. A :class:`CaptchaSolver` is the pluggable SPI that turns a
detected challenge into a submission token (or a "can't solve this" error).

Real solvers live outside this package - the intended wiring is:

* :class:`NullCaptchaSolver` - always fails. Used in tests + for the MCP
  tools' default ``DRY_RUN`` path where we never actually submit.
* :class:`ManualCaptchaSolver` - human-in-the-loop: pauses, shows the
  challenge, waits for the operator to type a token. Useful in CLIs.
* Third-party solvers (2Captcha, CapSolver, etc.) - callers subclass
  :class:`CaptchaSolver` and wire their API token in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CaptchaKind(StrEnum):
    """What type of captcha was detected on the page."""

    TURNSTILE = "turnstile"
    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    UNKNOWN = "unknown"


class CaptchaChallenge(BaseModel):
    """A captcha challenge the form submitter needs solved.

    The sitekey is the challenge's public identifier (how solvers like
    2Captcha know *which* challenge they're solving). ``iframe_url`` is
    the URL of the challenge widget itself, not the page hosting it —
    solvers load the iframe directly so they don't need a full browser.
    """

    model_config = ConfigDict(extra="forbid")

    kind: CaptchaKind
    sitekey: str = Field(..., min_length=1)
    page_url: str = Field(..., min_length=1)
    iframe_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaptchaSolverError(RuntimeError):
    """Raised when a solver cannot solve the challenge."""


class CaptchaSolver(ABC):
    """Async SPI for resolving :class:`CaptchaChallenge` instances."""

    @abstractmethod
    async def solve(self, challenge: CaptchaChallenge) -> str:
        """Return the token the page expects to be POSTed alongside the form."""


class NullCaptchaSolver(CaptchaSolver):
    """Solver that always fails.

    Used as the default so MCP tools cannot accidentally submit forms
    against a portal without a solver being explicitly wired in. Pair
    with ``dry_run=True`` (the default) to get a deterministic
    ``CAPTCHA_UNSOLVED`` outcome when called without configuration.
    """

    async def solve(self, challenge: CaptchaChallenge) -> str:
        raise CaptchaSolverError(
            f"NullCaptchaSolver cannot solve {challenge.kind} (sitekey={challenge.sitekey}); "
            "wire in a real CaptchaSolver implementation to submit portal forms."
        )


class ManualCaptchaSolver(CaptchaSolver):
    """Human-in-the-loop solver.

    Prints the challenge metadata to stderr and awaits a token on stdin.
    Useful for local development, blocked in automated environments by
    the absence of a TTY.
    """

    def __init__(self, *, timeout_s: float = 300.0) -> None:
        self._timeout_s = timeout_s

    async def solve(self, challenge: CaptchaChallenge) -> str:
        import asyncio
        import sys

        print(
            f"[captcha] {challenge.kind} detected at {challenge.page_url} "
            f"(sitekey={challenge.sitekey}); paste token and press enter:",
            file=sys.stderr,
        )
        loop = asyncio.get_running_loop()
        try:
            token = await asyncio.wait_for(
                loop.run_in_executor(None, sys.stdin.readline),
                timeout=self._timeout_s,
            )
        except asyncio.TimeoutError as exc:
            raise CaptchaSolverError(
                f"ManualCaptchaSolver timed out after {self._timeout_s:.0f}s"
            ) from exc
        token = (token or "").strip()
        if not token:
            raise CaptchaSolverError(
                "ManualCaptchaSolver received empty token (operator aborted?)"
            )
        return token
