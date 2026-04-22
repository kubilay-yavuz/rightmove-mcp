"""Form-submission primitives for portal action tools.

Unlike :mod:`uk_property_apify_shared.crawler`, these modules *mutate* remote
state (send an email, request a viewing, book a valuation). They are tiny,
deliberately opinionated wrappers around Playwright plus a pluggable captcha
solver interface, and they are designed to degrade safely into ``DRY_RUN``
outcomes when called from the MCP tools - so a model can call
``send_inquiry`` exploratorily and the default behaviour is to build and
validate the payload without ever hitting the portal.

Concurrency model: :class:`FormSubmitter` owns a Playwright browser
lifecycle via :class:`~uk_property_apify_shared.crawler.browser_fetcher.BrowserFetcher`'s
launch plumbing. Each submission opens a fresh :class:`~playwright.async_api.BrowserContext`
so cookies / session storage do not bleed between submissions.
"""

from __future__ import annotations

from uk_property_apify_shared.actions.captcha import (
    CaptchaChallenge,
    CaptchaKind,
    CaptchaSolver,
    CaptchaSolverError,
    ManualCaptchaSolver,
    NullCaptchaSolver,
)
from uk_property_apify_shared.actions.mcp import (
    RequestFreeValuationInput,
    RequestFreeValuationOutput,
    RequestViewingInput,
    RequestViewingOutput,
    SendInquiryInput,
    SendInquiryOutput,
    request_free_valuation,
    request_viewing,
    send_inquiry,
)
from uk_property_apify_shared.actions.orchestrator import (
    execute_free_valuation,
    execute_inquiry,
    execute_viewing_request,
)
from uk_property_apify_shared.actions.portals import (
    BUNDLES_BY_PORTAL,
    ONTHEMARKET_BUNDLE,
    RIGHTMOVE_BUNDLE,
    ZOOPLA_BUNDLE,
    PortalActionBundle,
    get_bundle,
)
from uk_property_apify_shared.actions.submitter import (
    FormSelectorMap,
    FormSubmission,
    FormSubmissionError,
    FormSubmissionResult,
    FormSubmitter,
)

__all__ = [
    "BUNDLES_BY_PORTAL",
    "CaptchaChallenge",
    "CaptchaKind",
    "CaptchaSolver",
    "CaptchaSolverError",
    "FormSelectorMap",
    "FormSubmission",
    "FormSubmissionError",
    "FormSubmissionResult",
    "FormSubmitter",
    "ManualCaptchaSolver",
    "NullCaptchaSolver",
    "ONTHEMARKET_BUNDLE",
    "PortalActionBundle",
    "RIGHTMOVE_BUNDLE",
    "RequestFreeValuationInput",
    "RequestFreeValuationOutput",
    "RequestViewingInput",
    "RequestViewingOutput",
    "SendInquiryInput",
    "SendInquiryOutput",
    "ZOOPLA_BUNDLE",
    "execute_free_valuation",
    "execute_inquiry",
    "execute_viewing_request",
    "get_bundle",
    "request_free_valuation",
    "request_viewing",
    "send_inquiry",
]
