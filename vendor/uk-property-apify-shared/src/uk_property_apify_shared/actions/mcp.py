"""Shared MCP-tool input / output Pydantic contracts for action tools.

The three per-portal MCPs (Zoopla, Rightmove, OnTheMarket) expose an
identical tool surface — ``send_inquiry`` / ``request_viewing`` /
``request_free_valuation``. Keeping the contracts in one place ensures:

* Models can't drift across portals (a bugfix in one shape propagates).
* Tooling that speaks to multiple portals can share deserialization
  logic (e.g. a "try Rightmove first, fall back to Zoopla" router).

Each input model is a FLAT Pydantic object (no nested models) so MCP
clients can reason about the parameter list as a simple kwargs dict.
The functions in this module compose the nested canonical request
objects from :mod:`uk_property_scrapers.schema` internally before
handing control to :mod:`.orchestrator`.

Safety-critical defaults (see docstrings):

* ``dry_run=True`` — never submit unless the caller explicitly sets it
  to False.
* ``consent_to_portal_tcs=False`` — must flip to True for live submits.
* ``opt_in=False`` — never tick the portal's marketing checkbox without
  explicit opt-in (Rightmove defaults its box to ON; we never copy that).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from uk_property_apify_shared.actions.orchestrator import (
    execute_free_valuation,
    execute_inquiry,
    execute_viewing_request,
)
from uk_property_scrapers.schema import (  # noqa: TC002 — pydantic field types
    Address,
    BuyerIdentity,
    BuyerInterest,
    BuyerMortgageStatus,
    BuyerPosition,
    FreeValuationRequest,
    InquiryRequest,
    InquiryResult,
    PropertyType,
    Source,
    ViewingRequest,
)

if TYPE_CHECKING:
    from uk_property_apify_shared.actions.portals import PortalActionBundle


# ── send_inquiry ────────────────────────────────────────────────────────────


class SendInquiryInput(BaseModel):
    """Flat input for ``send_inquiry`` MCP tool.

    Every field under the portal-canonical :class:`BuyerIdentity` is
    expanded into a top-level field so MCP clients can populate them
    without knowing the schema's composition.
    """

    model_config = ConfigDict(extra="forbid")

    listing_url: str = Field(..., description="Full portal listing URL.")
    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(
        ...,
        min_length=5,
        max_length=200,
        pattern=r"^[^@]+@[^@]+\.[^@]+$",
        description="Buyer email; same regex as BuyerIdentity.email.",
    )
    phone: str = Field(..., min_length=6, max_length=30)
    message: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Free-text message body shown to the agent.",
    )
    interest: BuyerInterest = BuyerInterest.UNKNOWN
    position: BuyerPosition = BuyerPosition.UNKNOWN
    mortgage_status: BuyerMortgageStatus = BuyerMortgageStatus.UNKNOWN
    opt_in: bool = Field(
        False,
        description=(
            "If True, tick the portal's marketing / similar-properties "
            "opt-in checkbox. Defaults to False so we never opt users in "
            "silently (especially important for Rightmove, which defaults "
            "its checkbox to ON)."
        ),
    )
    consent_to_portal_tcs: bool = Field(
        False,
        description=(
            "Caller must set True to actually submit. Dry-runs ignore this; "
            "live submits without consent raise ValueError."
        ),
    )
    dry_run: bool = Field(
        True,
        description=(
            "When True (default), validate + build + FILL the form but do "
            "NOT click submit. Use to inspect what *would* be sent."
        ),
    )


class SendInquiryOutput(BaseModel):
    """Output of ``send_inquiry`` MCP tool."""

    result: InquiryResult


async def send_inquiry(
    inp: SendInquiryInput,
    *,
    bundle: PortalActionBundle,
) -> SendInquiryOutput:
    """Drive a portal send-inquiry flow for the given portal bundle."""
    request = InquiryRequest(
        listing_url=inp.listing_url,  # type: ignore[arg-type]
        identity=BuyerIdentity(
            first_name=inp.first_name,
            last_name=inp.last_name,
            email=inp.email,
            phone=inp.phone,
        ),
        message=inp.message,
        interest=inp.interest,
        position=inp.position,
        mortgage_status=inp.mortgage_status,
        consent_to_portal_tcs=inp.consent_to_portal_tcs,
        dry_run=inp.dry_run,
    )
    result = await execute_inquiry(request, bundle=bundle, opt_in=inp.opt_in)
    return SendInquiryOutput(result=result)


# ── request_viewing ────────────────────────────────────────────────────────


class RequestViewingInput(BaseModel):
    """Flat input for ``request_viewing`` MCP tool."""

    model_config = ConfigDict(extra="forbid")

    listing_url: str = Field(..., description="Full portal listing URL.")
    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(
        ...,
        min_length=5,
        max_length=200,
        pattern=r"^[^@]+@[^@]+\.[^@]+$",
    )
    phone: str = Field(..., min_length=6, max_length=30)
    preferred_slots: list[datetime] = Field(
        default_factory=list,
        description=(
            "Up to 3 preferred viewing slots (ISO-8601 datetimes). "
            "Portals usually accept 2-3; extras are silently dropped."
        ),
        max_length=5,
    )
    message: str | None = Field(
        None,
        max_length=2000,
        description="Optional free-text note to the agent.",
    )
    opt_in: bool = False
    consent_to_portal_tcs: bool = False
    dry_run: bool = True


class RequestViewingOutput(BaseModel):
    """Output of ``request_viewing`` MCP tool."""

    result: InquiryResult


async def request_viewing(
    inp: RequestViewingInput,
    *,
    bundle: PortalActionBundle,
) -> RequestViewingOutput:
    """Drive a portal viewing-request flow."""
    request = ViewingRequest(
        listing_url=inp.listing_url,  # type: ignore[arg-type]
        identity=BuyerIdentity(
            first_name=inp.first_name,
            last_name=inp.last_name,
            email=inp.email,
            phone=inp.phone,
        ),
        preferred_slots=inp.preferred_slots,
        message=inp.message,
        consent_to_portal_tcs=inp.consent_to_portal_tcs,
        dry_run=inp.dry_run,
    )
    result = await execute_viewing_request(request, bundle=bundle, opt_in=inp.opt_in)
    return RequestViewingOutput(result=result)


# ── request_free_valuation ─────────────────────────────────────────────────


class RequestFreeValuationInput(BaseModel):
    """Flat input for ``request_free_valuation`` MCP tool."""

    model_config = ConfigDict(extra="forbid")

    address: str = Field(
        ...,
        min_length=1,
        max_length=400,
        description=(
            "Address as displayed, e.g. ``10 Downing St, London, SW1A 2AA``. "
            "Matches :class:`uk_property_scrapers.schema.Address.raw`."
        ),
    )
    postcode: str | None = Field(
        None,
        min_length=2,
        max_length=12,
        description="Full UK postcode (``CB1 2QA``). Preferred over outcode.",
    )
    postcode_outcode: str | None = Field(
        None,
        min_length=2,
        max_length=4,
        description="Outcode-only (``CB1``). Used when full postcode unknown.",
    )
    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    email: str = Field(
        ...,
        min_length=5,
        max_length=200,
        pattern=r"^[^@]+@[^@]+\.[^@]+$",
    )
    phone: str = Field(..., min_length=6, max_length=30)
    transaction: Literal["sale", "rent"] = "sale"
    property_type: PropertyType = PropertyType.UNKNOWN
    bedrooms: int | None = Field(None, ge=0, le=50)
    target_agent_source_id: str | None = Field(
        None,
        description="Optional — pin to a specific branch id.",
    )
    valuation_page_url: str | None = Field(
        None,
        description=(
            "Optional override for the portal's valuation landing page URL "
            "(e.g. to target a specific branch's /valuation/ page)."
        ),
    )
    opt_in: bool = False
    consent_to_portal_tcs: bool = False
    dry_run: bool = True


class RequestFreeValuationOutput(BaseModel):
    """Output of ``request_free_valuation`` MCP tool."""

    result: InquiryResult


async def request_free_valuation(
    inp: RequestFreeValuationInput,
    *,
    bundle: PortalActionBundle,
) -> RequestFreeValuationOutput:
    """Drive a portal free-valuation flow."""
    source_for_portal = {
        "zoopla": Source.ZOOPLA,
        "rightmove": Source.RIGHTMOVE,
        "onthemarket": Source.ONTHEMARKET,
    }[bundle.portal]
    request = FreeValuationRequest(
        address=Address(
            raw=inp.address,
            postcode=inp.postcode,
            postcode_outcode=inp.postcode_outcode,
        ),
        identity=BuyerIdentity(
            first_name=inp.first_name,
            last_name=inp.last_name,
            email=inp.email,
            phone=inp.phone,
        ),
        transaction=inp.transaction,
        property_type=inp.property_type,
        bedrooms=inp.bedrooms,
        target_portal=source_for_portal,
        target_agent_source_id=inp.target_agent_source_id,
        consent_to_portal_tcs=inp.consent_to_portal_tcs,
        dry_run=inp.dry_run,
    )
    result = await execute_free_valuation(
        request,
        bundle=bundle,
        opt_in=inp.opt_in,
        valuation_page_url=inp.valuation_page_url,
    )
    return RequestFreeValuationOutput(result=result)
