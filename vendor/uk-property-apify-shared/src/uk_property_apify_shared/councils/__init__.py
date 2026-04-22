"""Private extension of the public IDOX council registry.

The public package :mod:`uk_property_apis.idox.councils` ships a small
**reference registry** — two councils, one per transport (Lambeth for
ArcGIS, Westminster for HTML) — so open-source consumers can exercise
every code path end-to-end without a private dependency.

This private module carries the *production* registry: every council
that has been reverse-engineered, verified, and enrolled in the hosted
A5 ``planning-aggregator`` actor. Entries here move public → private
purely as the deliberate expression of the business model:

* The library is open-source, so the transport, parser, and dispatch
  code live in public. Anyone can add their own councils at runtime
  by constructing a :class:`CouncilConfig` and dropping it into their
  own registry.
* The *curated* list of "which councils we've verified, which transport
  they accept today, what their URL layout is" is the thing we
  maintain continuously as council sites change, councils get onboarded,
  and ArcGIS availability flips. That ongoing maintenance is what the
  paid A5 actor monetises.

Consumers:

* **A5 ``planning-aggregator`` actor** — imports :func:`all_councils`
  for validation + dispatch so all paid runs see the full verified
  registry.
* **Smoke harness** (public) — uses the public reference registry;
  Lambeth + Westminster are always enough to verify both transports
  still work in the wild.
* **OSS agent** — uses the public reference registry; users who need
  more councils either hand-build :class:`CouncilConfig` objects or
  upgrade to the hosted actor.
"""

from __future__ import annotations

from uk_property_apis.idox.councils import KNOWN_COUNCILS as PUBLIC_COUNCILS
from uk_property_apis.idox.models import CouncilConfig  # noqa: TC002 - Pydantic schema use

from uk_property_apify_shared.councils.registry import PRIVATE_COUNCILS


def all_councils() -> dict[str, CouncilConfig]:
    """Return the merged public + private council registry.

    The returned dict is a fresh copy — callers may mutate it without
    leaking changes back into the module-level sources. Collision is
    resolved with private winning (lets us override a public reference
    config with a more permissive one, e.g. if a council flips from
    HTML-only to ArcGIS-enabled).
    """

    merged: dict[str, CouncilConfig] = {}
    merged.update(PUBLIC_COUNCILS)
    merged.update(PRIVATE_COUNCILS)
    return merged


def get_council(slug: str) -> CouncilConfig:
    """Look up a council by slug against the merged registry."""

    registry = all_councils()
    key = slug.strip().lower()
    try:
        return registry[key]
    except KeyError as exc:
        known = ", ".join(sorted(registry)) or "(none)"
        msg = f"Unknown council {slug!r}; known: {known}"
        raise KeyError(msg) from exc


def arcgis_enabled_councils() -> list[CouncilConfig]:
    """Councils (public + private) that publish a reachable ArcGIS FeatureServer."""

    return [c for c in all_councils().values() if c.arcgis_base_url is not None]


__all__ = [
    "PRIVATE_COUNCILS",
    "PUBLIC_COUNCILS",
    "all_councils",
    "arcgis_enabled_councils",
    "get_council",
]
