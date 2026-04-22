"""Public API for the UK-property Apify delegation client.

See :mod:`uk_property_apify_client.client` for the full design notes and
:mod:`uk_property_apify_client.actors` for the actor registry.
"""

from __future__ import annotations

from uk_property_apify_client.actors import (
    KNOWN_ACTOR_SLUGS,
    ActorId,
    ActorKey,
)
from uk_property_apify_client.client import (
    ActorCallResult,
    ApifyDelegation,
    DelegationError,
)

__all__ = [
    "KNOWN_ACTOR_SLUGS",
    "ActorCallResult",
    "ActorId",
    "ActorKey",
    "ApifyDelegation",
    "DelegationError",
]
