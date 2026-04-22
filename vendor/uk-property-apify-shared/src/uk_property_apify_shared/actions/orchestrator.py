"""Portal-agnostic orchestration of the canonical action requests.

Each per-portal MCP imports the executor for its flow, passes the
portal's :class:`~uk_property_apify_shared.actions.portals.PortalActionBundle`,
and gets back a fully-populated :class:`~uk_property_scrapers.schema.InquiryResult`.

The orchestrator is deliberately thin: it validates inputs (consent gate,
URL shape), maps the canonical request into a :class:`~uk_property_apify_shared.actions.submitter.FormSubmission`,
drives :class:`~uk_property_apify_shared.actions.submitter.FormSubmitter`,
then converts the :class:`~uk_property_apify_shared.actions.submitter.FormSubmissionResult`
back to an :class:`~uk_property_scrapers.schema.InquiryResult`. All
portal-specific behaviour lives in the bundle (selectors) — the rest is
shared plumbing.

Safety contract (MUST hold for every executor):

1. ``dry_run=True`` (the schema default) → we never click submit.
2. ``consent_to_portal_tcs=False`` + ``dry_run=False`` → raise
   :class:`ValueError`; we refuse to submit without explicit consent.
3. ``opt_in`` flows only when the caller explicitly sets an opt-in flag.
   Portals like Rightmove default their marketing checkbox to ON; the
   submitter only ticks when ``submission.opt_in=True``.
4. Any captcha we can't solve → outcome ``CAPTCHA_UNSOLVED``; no retry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from uk_property_apify_shared.actions.captcha import CaptchaKind
from uk_property_apify_shared.actions.submitter import (
    FormSubmission,
    FormSubmissionError,
    FormSubmissionResult,
    FormSubmitter,
)
from uk_property_scrapers.schema import (
    FreeValuationRequest,
    InquiryChannel,
    InquiryOutcome,
    InquiryRequest,
    InquiryResult,
    ViewingRequest,
)

if TYPE_CHECKING:
    from uk_property_apify_shared.actions.portals import PortalActionBundle


# ── Public API ──────────────────────────────────────────────────────────────


async def execute_inquiry(
    request: InquiryRequest,
    *,
    bundle: PortalActionBundle,
    submitter: FormSubmitter | None = None,
    opt_in: bool = False,
) -> InquiryResult:
    """Drive a portal send-inquiry flow end-to-end.

    ``request`` is the canonical :class:`InquiryRequest` from
    :mod:`uk_property_scrapers.schema`. The orchestrator:

    1. Validates the listing URL against ``bundle.listing_url_pattern``.
    2. Enforces the consent gate (``consent_to_portal_tcs`` must be True
       for a non-dry-run submission).
    3. Builds a :class:`FormSubmission` from the request's buyer identity
       and message body.
    4. Drives :class:`FormSubmitter` through the portal's inquiry form.
    5. Maps the result back to :class:`InquiryResult`.

    ``opt_in`` is passed through separately (it's not on
    :class:`InquiryRequest` yet to avoid the temptation to accidentally
    default it to True).
    """
    channel = InquiryChannel.EMAIL
    listing_url = str(request.listing_url)

    _validate_consent(request.consent_to_portal_tcs, request.dry_run)
    url_error = _validate_url(listing_url, bundle.listing_url_pattern)
    if url_error is not None:
        return _validation_failure(
            channel=channel,
            listing_url=listing_url,
            message=url_error,
        )

    fields = _inquiry_fields(request)
    submission = FormSubmission(
        page_url=listing_url,
        fields=fields,
        opt_in=opt_in,
        dry_run=request.dry_run,
    )
    return await _submit_and_convert(
        submission=submission,
        selectors=bundle.inquiry_form,
        submitter=submitter,
        channel=channel,
        listing_url=listing_url,
    )


async def execute_viewing_request(
    request: ViewingRequest,
    *,
    bundle: PortalActionBundle,
    submitter: FormSubmitter | None = None,
    opt_in: bool = False,
) -> InquiryResult:
    """Drive a portal viewing-request flow.

    Falls back to the inquiry form if the portal has no dedicated
    viewing form — in that case the viewing slots are inlined into the
    inquiry message.
    """
    channel = InquiryChannel.VIEWING_REQUEST
    listing_url = str(request.listing_url)

    _validate_consent(request.consent_to_portal_tcs, request.dry_run)
    url_error = _validate_url(listing_url, bundle.listing_url_pattern)
    if url_error is not None:
        return _validation_failure(
            channel=channel,
            listing_url=listing_url,
            message=url_error,
        )

    selectors = bundle.viewing_form or bundle.inquiry_form
    fields = _viewing_fields(request, using_inquiry_fallback=bundle.viewing_form is None)
    submission = FormSubmission(
        page_url=listing_url,
        fields=fields,
        opt_in=opt_in,
        dry_run=request.dry_run,
    )
    return await _submit_and_convert(
        submission=submission,
        selectors=selectors,
        submitter=submitter,
        channel=channel,
        listing_url=listing_url,
    )


async def execute_free_valuation(
    request: FreeValuationRequest,
    *,
    bundle: PortalActionBundle,
    submitter: FormSubmitter | None = None,
    opt_in: bool = False,
    valuation_page_url: str | None = None,
) -> InquiryResult:
    """Drive a "book a free valuation" sell-side lead submission.

    ``valuation_page_url`` overrides ``bundle.valuation_page_url`` so
    callers can pin the request to a specific branch page (e.g.
    ``/find-agents/branch/<slug>/valuation/``) when the portal supports it.
    """
    channel = InquiryChannel.VALUATION
    _validate_consent(request.consent_to_portal_tcs, request.dry_run)

    target_url = valuation_page_url or bundle.valuation_page_url
    if not target_url:
        return _validation_failure(
            channel=channel,
            listing_url=None,
            message=(
                f"portal '{bundle.portal}' has no default valuation URL; "
                "pass `valuation_page_url` explicitly"
            ),
        )
    if bundle.valuation_form is None:
        return _validation_failure(
            channel=channel,
            listing_url=None,
            message=f"portal '{bundle.portal}' has no valuation form selectors",
        )

    fields = _valuation_fields(request)
    submission = FormSubmission(
        page_url=target_url,
        fields=fields,
        opt_in=opt_in,
        dry_run=request.dry_run,
    )
    return await _submit_and_convert(
        submission=submission,
        selectors=bundle.valuation_form,
        submitter=submitter,
        channel=channel,
        listing_url=None,
    )


# ── Internals ──────────────────────────────────────────────────────────────


def _validate_consent(consent: bool, dry_run: bool) -> None:
    """Refuse to submit without explicit T&C consent.

    Dry-run mode still exercises the form (no network mutation) so
    validation is deliberately relaxed there — the model can always
    dry-run to see what would be sent.
    """
    if consent:
        return
    if dry_run:
        return
    raise ValueError(
        "consent_to_portal_tcs must be True to submit a live request; "
        "set consent=True or keep dry_run=True"
    )


def _validate_url(url: str, pattern) -> str | None:
    """Return an error message if ``url`` doesn't match ``pattern``."""
    if pattern.match(url):
        return None
    return f"URL {url!r} does not match expected portal pattern {pattern.pattern!r}"


def _inquiry_fields(request: InquiryRequest) -> dict[str, str | None]:
    """Map an :class:`InquiryRequest` onto the :class:`FormSelectorMap` slots."""
    return {
        "field_first_name": request.identity.first_name,
        "field_last_name": request.identity.last_name,
        "field_email": request.identity.email,
        "field_phone": request.identity.phone,
        "field_message": request.message,
        "field_interest": request.interest.value,
        "field_position": request.position.value,
        "field_mortgage_status": request.mortgage_status.value,
    }


def _viewing_fields(
    request: ViewingRequest,
    *,
    using_inquiry_fallback: bool,
) -> dict[str, str | None]:
    """Map a :class:`ViewingRequest` onto the selector map.

    When the portal has no dedicated viewing form, we fall back to the
    inquiry form and inline the slots into the message so the agent
    still sees them.
    """
    slots = [slot.isoformat() for slot in request.preferred_slots]
    fields: dict[str, str | None] = {
        "field_first_name": request.identity.first_name,
        "field_last_name": request.identity.last_name,
        "field_email": request.identity.email,
        "field_phone": request.identity.phone,
    }
    if using_inquiry_fallback:
        body = request.message or "I would like to arrange a viewing."
        if slots:
            body += "\nPreferred viewing slots: " + ", ".join(slots)
        fields["field_message"] = body
        return fields

    fields["field_message"] = request.message
    if len(slots) > 0:
        fields["field_viewing_slot_1"] = slots[0]
    if len(slots) > 1:
        fields["field_viewing_slot_2"] = slots[1]
    if len(slots) > 2:
        fields["field_viewing_slot_3"] = slots[2]
    return fields


def _valuation_fields(request: FreeValuationRequest) -> dict[str, str | None]:
    """Map a :class:`FreeValuationRequest` onto the selector map.

    The canonical :class:`~uk_property_scrapers.schema.Address` model
    only exposes a raw display string plus optional postcode fields (see
    its docstring — portals render addresses loosely pre-sale). We pass
    ``address.raw`` into the generic ``field_address_line`` slot and
    prefer the full postcode over the outcode when both are present.
    """
    postcode = request.address.postcode or request.address.postcode_outcode
    return {
        "field_first_name": request.identity.first_name,
        "field_last_name": request.identity.last_name,
        "field_email": request.identity.email,
        "field_phone": request.identity.phone,
        "field_address_line": request.address.raw or None,
        "field_postcode": postcode,
        "field_property_type": request.property_type.value,
        "field_bedrooms": str(request.bedrooms) if request.bedrooms is not None else None,
        "field_transaction": request.transaction,
    }


async def _submit_and_convert(
    *,
    submission: FormSubmission,
    selectors,
    submitter: FormSubmitter | None,
    channel: InquiryChannel,
    listing_url: str | None,
) -> InquiryResult:
    """Drive the submitter (or a caller-supplied one) and map the result.

    When the caller supplies ``submitter`` we don't own its lifecycle.
    When we create one, we wrap it in an ``async with`` to guarantee
    browser cleanup even if the submit raises.
    """
    own_submitter = submitter is None
    if own_submitter:
        submitter = FormSubmitter()
        await submitter.start()
    assert submitter is not None
    try:
        try:
            result = await submitter.submit(submission, selectors)
        except FormSubmissionError as exc:
            outcome = _outcome_for_submission_error(str(exc))
            return InquiryResult(
                outcome=outcome,
                channel=channel,
                listing_url=_http_url_or_none(listing_url),
                captcha_required=("captcha" in str(exc).lower()),
                captcha_solved=False if "captcha" in str(exc).lower() else None,
                portal_message=None,
                error=str(exc),
            )
    finally:
        if own_submitter and submitter is not None:
            await submitter.aclose()

    return _result_to_inquiry_result(
        result=result,
        channel=channel,
        listing_url=listing_url,
    )


def _result_to_inquiry_result(
    *,
    result: FormSubmissionResult,
    channel: InquiryChannel,
    listing_url: str | None,
) -> InquiryResult:
    """Translate a :class:`FormSubmissionResult` into an :class:`InquiryResult`."""
    if result.dry_run:
        outcome: InquiryOutcome = InquiryOutcome.DRY_RUN
    elif (
        result.captcha_detected is not None
        and result.captcha_solved is False
    ):
        outcome = InquiryOutcome.CAPTCHA_UNSOLVED
    elif result.submitted and result.success_marker_seen:
        outcome = InquiryOutcome.SUBMITTED
    elif result.submitted:
        outcome = InquiryOutcome.SUBMITTED
    else:
        outcome = InquiryOutcome.REJECTED_BY_PORTAL

    portal_ref = _maybe_extract_reference(result.html_snapshot)
    return InquiryResult(
        outcome=outcome,
        channel=channel,
        listing_url=_http_url_or_none(listing_url),
        submitted_at=result.submitted_at or (
            datetime.now(UTC) if outcome == InquiryOutcome.DRY_RUN else None
        ),
        portal_reference_id=portal_ref,
        captcha_required=result.captcha_detected is not None,
        captcha_solved=result.captcha_solved,
        portal_message=_snippet(result.html_snapshot),
        error=None,
    )


def _outcome_for_submission_error(message: str) -> Literal[
    InquiryOutcome.CAPTCHA_UNSOLVED,
    InquiryOutcome.NETWORK_ERROR,
    InquiryOutcome.REJECTED_BY_PORTAL,
]:
    lowered = message.lower()
    if "captcha" in lowered:
        return InquiryOutcome.CAPTCHA_UNSOLVED
    if "timeout" in lowered or "net::" in lowered:
        return InquiryOutcome.NETWORK_ERROR
    return InquiryOutcome.REJECTED_BY_PORTAL


def _validation_failure(
    *,
    channel: InquiryChannel,
    listing_url: str | None,
    message: str,
) -> InquiryResult:
    return InquiryResult(
        outcome=InquiryOutcome.VALIDATION_ERROR,
        channel=channel,
        listing_url=_http_url_or_none(listing_url),
        error=message,
    )


def _http_url_or_none(url: str | None):
    if not url:
        return None
    try:
        from pydantic import HttpUrl, TypeAdapter
        return TypeAdapter(HttpUrl).validate_python(url)
    except Exception:  # noqa: BLE001
        return None


def _snippet(html: str, *, max_chars: int = 400) -> str | None:
    """Strip HTML and return a short snippet for portal_message."""
    if not html:
        return None
    import re

    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None
    return text[:max_chars]


def _maybe_extract_reference(html: str) -> str | None:
    """Best-effort scan for a portal-emitted reference id on the confirmation page."""
    if not html:
        return None
    import re

    patterns = (
        r"(?:reference|ref(?:erence)?\s*(?:id|number|no\.?))\s*[:#]?\s*([A-Z0-9-]{4,30})",
        r'data-reference-id="([A-Z0-9-]{4,30})"',
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None
