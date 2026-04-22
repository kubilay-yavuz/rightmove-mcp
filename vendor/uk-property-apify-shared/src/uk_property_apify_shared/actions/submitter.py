"""Playwright-backed form submitter.

The portals we interact with (Zoopla, Rightmove, OTM) all surface their
enquiry / viewing / valuation flows as a single HTML form on the listing
or branch page (occasionally behind a "Contact agent" disclosure). The
content of those forms shifts across A/B tests and device-type splits,
so :class:`FormSubmitter` never encodes selectors directly — callers
pass a :class:`FormSelectorMap` that names fields semantically
(``field_first_name``, ``field_message``, etc.) and the submitter:

1. Navigates to the URL.
2. Detects whether the form is actually on the page (or behind a
   progressive disclosure) and opens it.
3. Auto-detects any visible captcha (Turnstile / reCAPTCHA / hCaptcha),
   asks the :class:`CaptchaSolver` to solve it, and pastes the result
   into the canonical hidden ``<input name="cf-turnstile-response">`` /
   ``<textarea name="g-recaptcha-response">`` / ``h-captcha-response``.
4. Fills every :class:`FormSelectorMap`-mapped field with the supplied
   :class:`FormSubmission.fields` values.
5. Clicks the submit button and waits for the success marker, returning
   an HTML snapshot of the confirmation page so the MCP tool layer can
   extract any reference id the portal exposes.

The submitter is network-capable but defaults to **dry-run**: pass
``dry_run=True`` (the default) to stop after step 4 and return the
filled payload without clicking submit. The MCP tools default their
user-facing surface to ``dry_run=True`` for the same reason — you must
pass explicit consent + opt-out-of-dry-run to actually send.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from uk_property_apify_shared.actions.captcha import (
    CaptchaChallenge,
    CaptchaKind,
    CaptchaSolver,
    CaptchaSolverError,
    NullCaptchaSolver,
)
from uk_property_apify_shared.crawler.browser_fetcher import _STEALTH_INIT_JS
from uk_property_apify_shared.crawler.config import CrawlerConfig

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)


class FormSelectorMap(BaseModel):
    """Map of semantic field names to CSS selectors on a specific portal form.

    Every field is optional because portals ship different forms for the
    same intent (inquiry vs viewing vs valuation) and different listing
    types. Missing selectors are silently skipped at fill time.

    The :class:`FormSubmitter` fills each mapped selector with the value
    from :class:`FormSubmission.fields` keyed by the *field name* (the
    left-hand side of this map), so "field_first_name" in the selector
    map is filled with whatever ``submission.fields["field_first_name"]``
    is. That keeps per-portal adapter code ~20 lines instead of 200.
    """

    model_config = ConfigDict(extra="forbid")

    container: str | None = Field(
        None,
        description=(
            "Optional CSS selector that must be visible before filling. Used "
            "when the form is behind a progressive-disclosure toggle "
            "(e.g. Rightmove's ``Contact agent`` button)."
        ),
    )
    reveal_trigger: str | None = Field(
        None,
        description=(
            "Optional CSS selector of a button to click before interacting "
            "with the form (e.g. to open a modal)."
        ),
    )
    submit_button: str = Field(
        ..., description="CSS selector for the submit button."
    )
    success_marker: str | None = Field(
        None,
        description=(
            "CSS selector that appears on a successful submission. The "
            "submitter waits up to ``success_timeout_s`` for it; if omitted, "
            "we consider any non-error navigation a success."
        ),
    )
    success_url_substring: str | None = Field(
        None,
        description=(
            "Alternative success signal — if the page URL transitions to "
            "include this substring after submit, treat as success."
        ),
    )

    field_first_name: str | None = None
    field_last_name: str | None = None
    field_full_name: str | None = None
    field_email: str | None = None
    field_phone: str | None = None
    field_message: str | None = None
    field_interest: str | None = None
    field_position: str | None = None
    field_mortgage_status: str | None = None
    field_viewing_slot_1: str | None = None
    field_viewing_slot_2: str | None = None
    field_viewing_slot_3: str | None = None
    field_address_line: str | None = None
    field_postcode: str | None = None
    field_property_type: str | None = None
    field_bedrooms: str | None = None
    field_transaction: str | None = None

    opt_in_checkbox: str | None = Field(
        None,
        description=(
            "Portal-specific marketing opt-in checkbox. Must be explicitly "
            "toggled on by the caller via ``submission.opt_in`` — defaults "
            "to ``False`` so we never auto-opt-in buyers."
        ),
    )


class FormSubmission(BaseModel):
    """Data to feed into the form.

    ``fields`` is a loose map of the selector-map field names to the
    string values to fill. We accept ``None`` so callers can omit values
    without splatting empty strings. Select / multi-select values are
    passed through the same dict — :class:`FormSubmitter` uses
    Playwright's element-type heuristics to decide whether to
    :meth:`~playwright.async_api.Page.fill` or :meth:`~playwright.async_api.Page.select_option`.
    """

    model_config = ConfigDict(extra="forbid")

    page_url: str = Field(..., min_length=1)
    fields: dict[str, str | None] = Field(default_factory=dict)
    opt_in: bool = False
    dry_run: bool = True
    wait_after_load_s: float = Field(
        0.8,
        ge=0.0,
        le=30.0,
        description="Sleep after initial navigation to let hydration settle.",
    )
    success_timeout_s: float = Field(
        20.0,
        ge=1.0,
        le=120.0,
        description="How long to wait for a success marker or URL change.",
    )


class FormSubmissionError(RuntimeError):
    """Raised when the submitter cannot complete a submission."""


@dataclass
class FormSubmissionResult:
    """Structured result of :meth:`FormSubmitter.submit`."""

    submitted: bool
    dry_run: bool
    final_url: str
    html_snapshot: str
    captcha_detected: CaptchaKind | None = None
    captcha_solved: bool | None = None
    success_marker_seen: bool = False
    fields_filled: list[str] = field(default_factory=list)
    fields_skipped: list[str] = field(default_factory=list)
    submitted_at: datetime | None = None


class FormSubmitter:
    """Playwright-based form submission primitive.

    Lifecycle is symmetric with :class:`~uk_property_apify_shared.crawler.browser_fetcher.BrowserFetcher`:
    use it as an async context manager, or call :meth:`start` + :meth:`aclose`
    manually. The browser is lazy-started on first :meth:`submit` so importing
    the module does not pay the Chromium-launch cost.

    Each :meth:`submit` opens a fresh :class:`~playwright.async_api.BrowserContext`
    so portals cannot link multiple inquiries to a single cookie jar. The
    submitter applies the same stealth patch set used by
    :mod:`~uk_property_apify_shared.crawler.browser_fetcher`, so it blends
    in with the rest of the moat's Playwright footprint.
    """

    def __init__(
        self,
        *,
        config: CrawlerConfig | None = None,
        captcha_solver: CaptchaSolver | None = None,
    ) -> None:
        self._config = config or CrawlerConfig()
        self._captcha_solver = captcha_solver or NullCaptchaSolver()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> FormSubmitter:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def start(self) -> None:
        await self._ensure_browser()

    async def aclose(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    async def submit(
        self,
        submission: FormSubmission,
        selectors: FormSelectorMap,
    ) -> FormSubmissionResult:
        """Drive a form submission end-to-end.

        Steps executed (subject to ``submission.dry_run``):
        1. Navigate to the URL.
        2. Open the form (optional reveal trigger).
        3. Detect + solve any captcha challenge.
        4. Fill every mapped field.
        5. Optionally toggle the opt-in checkbox.
        6. Click submit.
        7. Wait for success marker / URL transition.

        When ``dry_run`` is True we stop at step 4 and return an
        "un-submitted" result with the HTML snapshot + fields filled, so
        callers can persist what *would* have been sent without ever
        hitting the portal.
        """
        await self._ensure_browser()
        assert self._browser is not None

        user_agent = random.choice(self._config.user_agents)
        context = await self._browser.new_context(
            user_agent=user_agent,
            viewport={
                "width": self._config.viewport_width,
                "height": self._config.viewport_height,
            },
            locale="en-GB",
            extra_http_headers={
                "Accept-Language": self._config.accept_language,
                **self._config.extra_headers,
            },
        )
        await context.add_init_script(_STEALTH_INIT_JS)
        page = await context.new_page()
        filled: list[str] = []
        skipped: list[str] = []
        captcha_kind: CaptchaKind | None = None
        captcha_solved: bool | None = None
        success_marker_seen = False
        try:
            await page.goto(
                submission.page_url,
                timeout=self._config.request_timeout_s * 1000,
                wait_until="domcontentloaded",
            )
            await asyncio.sleep(submission.wait_after_load_s)

            if selectors.reveal_trigger:
                with contextlib.suppress(Exception):
                    await page.click(
                        selectors.reveal_trigger,
                        timeout=self._config.request_timeout_s * 1000,
                    )
                    await asyncio.sleep(0.3)

            if selectors.container:
                with contextlib.suppress(Exception):
                    await page.wait_for_selector(
                        selectors.container,
                        timeout=self._config.request_timeout_s * 1000,
                    )

            captcha_kind, captcha_solved = await self._maybe_solve_captcha(
                page, submission.page_url
            )

            await self._fill_fields(
                page, submission.fields, selectors, filled=filled, skipped=skipped
            )

            if submission.opt_in and selectors.opt_in_checkbox:
                with contextlib.suppress(Exception):
                    await page.check(
                        selectors.opt_in_checkbox,
                        timeout=self._config.request_timeout_s * 1000,
                    )
                    filled.append("opt_in_checkbox")

            if submission.dry_run:
                html_snapshot = await page.content()
                return FormSubmissionResult(
                    submitted=False,
                    dry_run=True,
                    final_url=page.url,
                    html_snapshot=html_snapshot,
                    captcha_detected=captcha_kind,
                    captcha_solved=captcha_solved,
                    success_marker_seen=False,
                    fields_filled=filled,
                    fields_skipped=skipped,
                    submitted_at=None,
                )

            if captcha_kind is not None and not captcha_solved:
                raise FormSubmissionError(
                    f"captcha ({captcha_kind}) present but unsolved; "
                    "wire a CaptchaSolver or keep dry_run=True"
                )

            submitted_at = datetime.now(UTC)
            await page.click(selectors.submit_button)

            success_marker_seen = await self._wait_for_success(
                page,
                success_marker=selectors.success_marker,
                success_url_substring=selectors.success_url_substring,
                timeout_s=submission.success_timeout_s,
            )
            html_snapshot = await page.content()
            return FormSubmissionResult(
                submitted=True,
                dry_run=False,
                final_url=page.url,
                html_snapshot=html_snapshot,
                captcha_detected=captcha_kind,
                captcha_solved=captcha_solved,
                success_marker_seen=success_marker_seen,
                fields_filled=filled,
                fields_skipped=skipped,
                submitted_at=submitted_at,
            )
        finally:
            with contextlib.suppress(Exception):
                await page.close()
            await context.close()

    async def _ensure_browser(self) -> None:
        async with self._lock:
            if self._browser is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise RuntimeError(
                    "Playwright is required for FormSubmitter - install with "
                    "`pip install uk-property-apify-shared[crawler]` and run "
                    "`playwright install chromium`."
                ) from exc

            self._playwright = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {
                "headless": self._config.browser_headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            }
            if self._config.proxy_url:
                launch_kwargs["proxy"] = {"server": self._config.proxy_url}
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)

    async def _maybe_solve_captcha(
        self,
        page: Page,
        page_url: str,
    ) -> tuple[CaptchaKind | None, bool | None]:
        """Detect + optionally solve the first captcha we find on the page.

        Returns ``(kind, solved)`` where ``solved`` is None if no captcha
        was detected, True/False otherwise. We intentionally don't raise
        on solver failure — callers either stay in dry-run or decide how
        to fail based on the explicit flag.
        """
        detectors: tuple[tuple[CaptchaKind, str, str], ...] = (
            # (kind, css_selector, data-attribute-for-sitekey)
            (CaptchaKind.TURNSTILE, ".cf-turnstile[data-sitekey]", "data-sitekey"),
            (CaptchaKind.RECAPTCHA_V2, ".g-recaptcha[data-sitekey]", "data-sitekey"),
            (CaptchaKind.HCAPTCHA, ".h-captcha[data-sitekey]", "data-sitekey"),
        )
        for kind, selector, attr in detectors:
            element = await page.query_selector(selector)
            if element is None:
                continue
            sitekey = await element.get_attribute(attr)
            if not sitekey:
                continue
            challenge = CaptchaChallenge(
                kind=kind,
                sitekey=sitekey,
                page_url=page_url,
            )
            try:
                token = await self._captcha_solver.solve(challenge)
            except CaptchaSolverError as exc:
                logger.warning("captcha solver declined: %s", exc)
                return kind, False
            await self._inject_captcha_token(page, kind, token)
            return kind, True
        return None, None

    async def _inject_captcha_token(
        self,
        page: Page,
        kind: CaptchaKind,
        token: str,
    ) -> None:
        """Paste a captcha token into the hidden response field."""
        selector_map = {
            CaptchaKind.TURNSTILE: 'textarea[name="cf-turnstile-response"], input[name="cf-turnstile-response"]',
            CaptchaKind.RECAPTCHA_V2: 'textarea[name="g-recaptcha-response"]',
            CaptchaKind.RECAPTCHA_V3: 'input[name="g-recaptcha-response"]',
            CaptchaKind.HCAPTCHA: 'textarea[name="h-captcha-response"]',
        }
        selector = selector_map.get(kind)
        if selector is None:
            return
        await page.evaluate(
            """({ selector, token }) => {
              const el = document.querySelector(selector);
              if (!el) return false;
              el.value = token;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
              return true;
            }""",
            {"selector": selector, "token": token},
        )

    async def _fill_fields(
        self,
        page: Page,
        fields: dict[str, str | None],
        selectors: FormSelectorMap,
        *,
        filled: list[str],
        skipped: list[str],
    ) -> None:
        """Fill every mapped selector. Unmapped names are recorded as skipped."""
        mapping = selectors.model_dump(exclude_none=True)
        for key, value in fields.items():
            if value is None:
                skipped.append(key)
                continue
            selector = mapping.get(key)
            if not selector:
                skipped.append(key)
                continue
            ok = await self._fill_one(page, selector, value)
            (filled if ok else skipped).append(key)

    async def _fill_one(self, page: Page, selector: str, value: str) -> bool:
        """Fill a single field, auto-choosing fill/select/check by element role."""
        el = await page.query_selector(selector)
        if el is None:
            return False
        try:
            tag = (
                await page.evaluate("(el) => el.tagName.toLowerCase()", el)
            ) or ""
            if tag == "select":
                await page.select_option(selector, value)
            else:
                el_type = (
                    await page.evaluate("(el) => (el.type || '').toLowerCase()", el)
                ) or ""
                if el_type in {"checkbox", "radio"}:
                    truthy = value.strip().lower() in {"1", "true", "yes", "on"}
                    if truthy:
                        await page.check(selector)
                    else:
                        await page.uncheck(selector)
                else:
                    await page.fill(selector, value)
            return True
        except Exception as exc:  # noqa: BLE001 — best-effort per-field fill
            logger.debug("failed to fill %s with %r: %s", selector, value, exc)
            return False

    async def _wait_for_success(
        self,
        page: Page,
        *,
        success_marker: str | None,
        success_url_substring: str | None,
        timeout_s: float,
    ) -> bool:
        """Wait for either the success selector or a URL-substring transition.

        Returns True if we saw an explicit positive signal; False if we
        timed out. An exception-free False means we submitted but the
        portal didn't expose a predictable confirmation marker — the
        caller should treat this as ambiguous.
        """
        deadline = asyncio.get_running_loop().time() + timeout_s
        if success_marker:
            try:
                await page.wait_for_selector(
                    success_marker,
                    timeout=int(timeout_s * 1000),
                )
                return True
            except Exception:  # noqa: BLE001 - timeout is a valid outcome
                pass

        if success_url_substring:
            while asyncio.get_running_loop().time() < deadline:
                if success_url_substring in page.url:
                    return True
                await asyncio.sleep(0.25)
        return False
