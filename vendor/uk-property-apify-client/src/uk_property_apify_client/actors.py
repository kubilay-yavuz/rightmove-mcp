"""Canonical registry of the hosted UK-property Apify actors.

Each entry in :data:`KNOWN_ACTOR_SLUGS` matches a directory under the private
``uk-property-apify/actors/`` workspace and the slug you'd use on the Apify
platform (``username~<slug>``). :class:`ActorKey` is a ``Literal`` derived from
the same tuple — use it anywhere you want the type system to reject typos.

Adding a new actor:

1. Append its slug to :data:`KNOWN_ACTOR_SLUGS`.
2. That's it — :class:`ActorKey`, the env-var resolver in :mod:`.client`,
   and every consumer that switches on ``ActorKey`` will pick it up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

KNOWN_ACTOR_SLUGS: Final[tuple[str, ...]] = (
    "zoopla-listings",
    "rightmove-listings",
    "onthemarket-listings",
    "epc-ct-ppd-unified",
    "planning-aggregator",
    "uk-auctions",
    "landlord-network",
    "uk-tenders",
    "uk-demographics",
    "uk-avm",
    "uk-climate-risk",
    "uk-location-intel",
    "uk-listings-hydrate",
    "uk-sold-prices",
)
"""Tuple of every actor slug shipped under ``uk-property-apify/actors/``.

Ordered by actor number (A1..A14) so the output of :func:`list` round-trips
to the project-plan tables without surprise.
"""


ActorKey = Literal[
    "zoopla-listings",
    "rightmove-listings",
    "onthemarket-listings",
    "epc-ct-ppd-unified",
    "planning-aggregator",
    "uk-auctions",
    "landlord-network",
    "uk-tenders",
    "uk-demographics",
    "uk-avm",
    "uk-climate-risk",
    "uk-location-intel",
    "uk-listings-hydrate",
    "uk-sold-prices",
]
"""Literal alias for every slug in :data:`KNOWN_ACTOR_SLUGS`.

Kept as a hand-written Literal rather than a computed alias so static checkers
(mypy, pyright) narrow correctly on each branch. The two definitions must stay
in sync — :func:`_assert_known_actor_slugs_match_literal` guards that at
import time for every test run.
"""


@dataclass(frozen=True, slots=True)
class ActorId:
    """Fully-qualified Apify actor identifier.

    Apify uses ``username~slug`` (tilde, not slash) as the canonical actor
    identifier in both the REST API and the Python client. We keep the two
    parts separate so consumers can log them independently and so the env
    resolver has a clean place to validate each half.
    """

    username: str
    """Apify account name that owns the actor (e.g. ``kubilay-yavuz``).

    ``ApifyDelegation.resolve`` derives this from either the per-actor
    override env var or the global ``APIFY_USERNAME`` fallback. Never empty.
    """

    slug: str
    """Actor slug (e.g. ``zoopla-listings``). Must match one of
    :data:`KNOWN_ACTOR_SLUGS` when constructed via env resolution; external
    callers are free to instantiate :class:`ActorId` directly for unregistered
    actors if they want to use this type as a container."""

    def __post_init__(self) -> None:
        if not self.username:
            raise ValueError("ActorId.username must not be empty")
        if not self.slug:
            raise ValueError("ActorId.slug must not be empty")
        if "~" in self.username or "~" in self.slug:
            raise ValueError(
                "ActorId.username / .slug must not contain '~'; pass the "
                "two halves separately"
            )

    @property
    def full_id(self) -> str:
        """Canonical ``username~slug`` identifier accepted by the Apify API."""
        return f"{self.username}~{self.slug}"

    @classmethod
    def parse(cls, full_id: str) -> ActorId:
        """Parse a ``username~slug`` string (the form env vars carry) into
        an :class:`ActorId`. Raises :class:`ValueError` on malformed input.
        """
        if "~" not in full_id:
            raise ValueError(
                f"Actor ID {full_id!r} must be in 'username~actor-slug' form"
            )
        username, _, slug = full_id.partition("~")
        return cls(username=username, slug=slug)

    def __str__(self) -> str:
        return self.full_id


def _assert_known_actor_slugs_match_literal() -> None:
    """Import-time guard that :class:`ActorKey` and :data:`KNOWN_ACTOR_SLUGS`
    stay in sync. If this fires in a test run, someone added an actor slug
    to the registry but forgot to update :class:`ActorKey` (or vice versa).
    """
    import typing

    literal_values = set(typing.get_args(ActorKey))
    registry_values = set(KNOWN_ACTOR_SLUGS)
    if literal_values != registry_values:
        missing_from_literal = registry_values - literal_values
        missing_from_registry = literal_values - registry_values
        raise RuntimeError(
            "ActorKey literal and KNOWN_ACTOR_SLUGS drifted — "
            f"add to ActorKey: {sorted(missing_from_literal)}; "
            f"add to KNOWN_ACTOR_SLUGS: {sorted(missing_from_registry)}"
        )


_assert_known_actor_slugs_match_literal()


__all__ = [
    "KNOWN_ACTOR_SLUGS",
    "ActorId",
    "ActorKey",
]
