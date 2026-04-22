"""Per-portal selector bundles for the three supported portals.

Each portal ships slightly different enquiry / viewing / valuation forms,
and each form drifts over A/B tests. Rather than hardcode selectors in
the MCP layer, we keep a centralized mapping here that portal MCPs bind
their tool handlers against.

Design principles:

* **Stable selectors first** — prefer ``data-testid`` and ``name``
  attributes over CSS classes or positional selectors, so we survive
  most skin refreshes.
* **Overridable** — every bundle is a ``PortalActionBundle`` dataclass,
  so callers (e.g. integration tests, or a future self-healing config
  fetcher) can pass a custom bundle to the orchestrator.
* **Explicit about opt-in** — Rightmove, in particular, defaults its
  "yes, contact me about similar properties" checkbox to ON. We track
  the selector and rely on :class:`~uk_property_apify_shared.actions.submitter.FormSubmitter`
  to only tick it when ``submission.opt_in=True``. Downstream action
  MCP tools MUST default ``opt_in=False``.

URL validators are conservative: they reject obvious category mistakes
(e.g. passing a search page to ``send_inquiry``) but don't try to verify
that the listing is still live — that's the portal's job to report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from uk_property_apify_shared.actions.submitter import FormSelectorMap


@dataclass(frozen=True, slots=True)
class PortalActionBundle:
    """Portal-specific selectors for the three action flows.

    Any of the three maps can be ``None`` if the portal doesn't expose
    that flow (e.g. OTM doesn't have a first-class ``Request viewing``
    form separate from the inquiry form).
    """

    portal: str
    listing_url_pattern: re.Pattern[str]
    branch_url_pattern: re.Pattern[str]
    inquiry_form: FormSelectorMap
    viewing_form: FormSelectorMap | None = None
    valuation_form: FormSelectorMap | None = None
    valuation_page_url: str | None = field(
        default=None,
        metadata={
            "description": (
                "Optional portal-wide valuation landing page URL. Used when the "
                "caller doesn't pin the request to a specific branch."
            ),
        },
    )


# ── Zoopla ─────────────────────────────────────────────────────────────────

_ZOOPLA_LISTING_RE = re.compile(
    r"^https?://(?:www\.)?zoopla\.co\.uk/(?:for-sale|to-rent|new-homes)/details/\d+/?",
    re.IGNORECASE,
)
_ZOOPLA_BRANCH_RE = re.compile(
    r"^https?://(?:www\.)?zoopla\.co\.uk/find-agents/branch/[^/?#]+/?",
    re.IGNORECASE,
)

ZOOPLA_BUNDLE = PortalActionBundle(
    portal="zoopla",
    listing_url_pattern=_ZOOPLA_LISTING_RE,
    branch_url_pattern=_ZOOPLA_BRANCH_RE,
    inquiry_form=FormSelectorMap(
        reveal_trigger=(
            'button[data-testid="request-details-cta"], '
            'button[data-testid="request-details-button"]'
        ),
        container='form[data-testid="contact-form"], form[data-testid="agent-form"]',
        submit_button=(
            'form[data-testid="contact-form"] button[type="submit"], '
            'form[data-testid="agent-form"] button[type="submit"]'
        ),
        success_marker='[data-testid="contact-form-success"], [data-testid="lead-submitted"]',
        success_url_substring="/contact-confirmation",
        field_first_name='input[name="firstName"]',
        field_last_name='input[name="lastName"]',
        field_email='input[name="email"]',
        field_phone='input[name="phone"], input[name="telephone"]',
        field_message='textarea[name="message"]',
        field_interest='select[name="buyerInterest"], select[name="moveTimeframe"]',
        field_position='select[name="buyerPosition"]',
        field_mortgage_status='select[name="mortgageStatus"]',
        opt_in_checkbox='input[name="marketingOptIn"]',
    ),
    viewing_form=FormSelectorMap(
        reveal_trigger='button[data-testid="request-viewing-cta"]',
        container='form[data-testid="viewing-request-form"]',
        submit_button='form[data-testid="viewing-request-form"] button[type="submit"]',
        success_marker='[data-testid="viewing-request-success"]',
        field_first_name='input[name="firstName"]',
        field_last_name='input[name="lastName"]',
        field_email='input[name="email"]',
        field_phone='input[name="phone"]',
        field_message='textarea[name="message"]',
        field_viewing_slot_1='input[name="preferredSlot1"]',
        field_viewing_slot_2='input[name="preferredSlot2"]',
        field_viewing_slot_3='input[name="preferredSlot3"]',
        opt_in_checkbox='input[name="marketingOptIn"]',
    ),
    valuation_form=FormSelectorMap(
        container='form[data-testid="valuation-form"]',
        submit_button='form[data-testid="valuation-form"] button[type="submit"]',
        success_marker='[data-testid="valuation-request-success"]',
        field_first_name='input[name="firstName"]',
        field_last_name='input[name="lastName"]',
        field_email='input[name="email"]',
        field_phone='input[name="phone"]',
        field_address_line='input[name="address"]',
        field_postcode='input[name="postcode"]',
        field_property_type='select[name="propertyType"]',
        field_bedrooms='select[name="bedrooms"], input[name="bedrooms"]',
        field_transaction='select[name="valuationType"]',
        opt_in_checkbox='input[name="marketingOptIn"]',
    ),
    valuation_page_url="https://www.zoopla.co.uk/free-valuation/",
)


# ── Rightmove ──────────────────────────────────────────────────────────────

_RIGHTMOVE_LISTING_RE = re.compile(
    r"^https?://(?:www\.)?rightmove\.co\.uk/properties/\d+",
    re.IGNORECASE,
)
_RIGHTMOVE_BRANCH_RE = re.compile(
    r"^https?://(?:www\.)?rightmove\.co\.uk/estate-agents/"
    r"(?:agent|branch)/[^/?#]+/[^/?#]+-\d+\.html",
    re.IGNORECASE,
)

RIGHTMOVE_BUNDLE = PortalActionBundle(
    portal="rightmove",
    listing_url_pattern=_RIGHTMOVE_LISTING_RE,
    branch_url_pattern=_RIGHTMOVE_BRANCH_RE,
    inquiry_form=FormSelectorMap(
        reveal_trigger=(
            'button[data-test="contactAgentButton"], '
            'button[data-testid="contactAgentButton"]'
        ),
        container='form[data-test="contactAgentForm"]',
        submit_button='form[data-test="contactAgentForm"] button[type="submit"]',
        success_marker=(
            '[data-test="contactAgentFormSuccess"], '
            '[data-testid="enquiry-success-message"]'
        ),
        field_first_name='input[name="firstName"]',
        field_last_name='input[name="lastName"]',
        field_email='input[name="email"]',
        field_phone='input[name="telephoneNumber"], input[name="phone"]',
        field_message='textarea[name="message"]',
        field_interest='select[name="movingPlans"]',
        field_position='select[name="currentPosition"]',
        field_mortgage_status='select[name="mortgageArranged"]',
        opt_in_checkbox='input[name="marketingOptIn"], input[name="propertyLeadsOptIn"]',
    ),
    viewing_form=FormSelectorMap(
        reveal_trigger='button[data-test="requestViewingButton"]',
        container='form[data-test="requestViewingForm"]',
        submit_button='form[data-test="requestViewingForm"] button[type="submit"]',
        success_marker='[data-test="viewingRequestSuccess"]',
        field_first_name='input[name="firstName"]',
        field_last_name='input[name="lastName"]',
        field_email='input[name="email"]',
        field_phone='input[name="telephoneNumber"]',
        field_message='textarea[name="message"]',
        field_viewing_slot_1='input[name="viewingSlot1"]',
        field_viewing_slot_2='input[name="viewingSlot2"]',
        field_viewing_slot_3='input[name="viewingSlot3"]',
        opt_in_checkbox='input[name="marketingOptIn"]',
    ),
    valuation_form=FormSelectorMap(
        container='form[data-test="valuationForm"]',
        submit_button='form[data-test="valuationForm"] button[type="submit"]',
        success_marker='[data-test="valuationRequestSuccess"]',
        field_first_name='input[name="firstName"]',
        field_last_name='input[name="lastName"]',
        field_email='input[name="email"]',
        field_phone='input[name="telephoneNumber"]',
        field_address_line='input[name="address"]',
        field_postcode='input[name="postcode"]',
        field_property_type='select[name="propertyType"]',
        field_bedrooms='select[name="bedrooms"]',
        field_transaction='select[name="valuationType"]',
        opt_in_checkbox='input[name="marketingOptIn"]',
    ),
    valuation_page_url="https://www.rightmove.co.uk/house-prices/free-valuation.html",
)


# ── OnTheMarket ────────────────────────────────────────────────────────────

_OTM_LISTING_RE = re.compile(
    r"^https?://(?:www\.)?onthemarket\.com/details/\d+",
    re.IGNORECASE,
)
_OTM_BRANCH_RE = re.compile(
    r"^https?://(?:www\.)?onthemarket\.com/agents/branch/[^/?#]+/[^/?#]+/?",
    re.IGNORECASE,
)

ONTHEMARKET_BUNDLE = PortalActionBundle(
    portal="onthemarket",
    listing_url_pattern=_OTM_LISTING_RE,
    branch_url_pattern=_OTM_BRANCH_RE,
    inquiry_form=FormSelectorMap(
        reveal_trigger='button[data-testid="contact-agent-cta"]',
        container='form[data-testid="contact-agent-form"]',
        submit_button='form[data-testid="contact-agent-form"] button[type="submit"]',
        success_marker='[data-testid="enquiry-success"]',
        field_first_name='input[name="firstName"]',
        field_last_name='input[name="lastName"]',
        field_email='input[name="email"]',
        field_phone='input[name="phone"], input[name="telephone"]',
        field_message='textarea[name="message"]',
        field_interest='select[name="buyerStage"]',
        field_position='select[name="buyerPosition"]',
        field_mortgage_status='select[name="mortgageStatus"]',
        opt_in_checkbox='input[name="marketingConsent"]',
    ),
    viewing_form=FormSelectorMap(
        reveal_trigger='button[data-testid="request-viewing-cta"]',
        container='form[data-testid="viewing-request-form"]',
        submit_button='form[data-testid="viewing-request-form"] button[type="submit"]',
        success_marker='[data-testid="viewing-request-success"]',
        field_first_name='input[name="firstName"]',
        field_last_name='input[name="lastName"]',
        field_email='input[name="email"]',
        field_phone='input[name="phone"]',
        field_message='textarea[name="message"]',
        field_viewing_slot_1='input[name="preferredDate1"]',
        field_viewing_slot_2='input[name="preferredDate2"]',
        field_viewing_slot_3='input[name="preferredDate3"]',
        opt_in_checkbox='input[name="marketingConsent"]',
    ),
    valuation_form=FormSelectorMap(
        container='form[data-testid="valuation-form"]',
        submit_button='form[data-testid="valuation-form"] button[type="submit"]',
        success_marker='[data-testid="valuation-success"]',
        field_first_name='input[name="firstName"]',
        field_last_name='input[name="lastName"]',
        field_email='input[name="email"]',
        field_phone='input[name="phone"]',
        field_address_line='input[name="address"]',
        field_postcode='input[name="postcode"]',
        field_property_type='select[name="propertyType"]',
        field_bedrooms='select[name="bedrooms"]',
        field_transaction='select[name="valuationType"]',
        opt_in_checkbox='input[name="marketingConsent"]',
    ),
    valuation_page_url="https://www.onthemarket.com/free-valuation/",
)


BUNDLES_BY_PORTAL: dict[str, PortalActionBundle] = {
    "zoopla": ZOOPLA_BUNDLE,
    "rightmove": RIGHTMOVE_BUNDLE,
    "onthemarket": ONTHEMARKET_BUNDLE,
}


def get_bundle(portal: str) -> PortalActionBundle:
    """Look up a :class:`PortalActionBundle` by portal slug."""
    try:
        return BUNDLES_BY_PORTAL[portal.lower()]
    except KeyError as exc:
        raise KeyError(
            f"unknown portal '{portal}'; expected one of "
            f"{sorted(BUNDLES_BY_PORTAL)}"
        ) from exc
