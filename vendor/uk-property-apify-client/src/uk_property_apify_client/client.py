"""Env-driven delegation to hosted Apify actors.

Three moving parts:

* :class:`ApifyDelegation` carries the resolved token, actor ID, and run
  parameters for one call. :meth:`ApifyDelegation.resolve` reads the
  environment and returns either a delegation or ``None`` (the "fall back
  to local" signal every consumer uses).
* :meth:`ApifyDelegation.call` fires the run through ``apify-client``,
  awaits it, materialises the dataset + RUN_META + ERRORS KV records, and
  returns an :class:`ActorCallResult`.
* :class:`DelegationError` wraps every failure — misconfiguration, non-success
  run status, missing SDK dep — so callers have exactly one exception type
  to catch.

The env contract is documented in :doc:`README`; in short:

* ``APIFY_API_TOKEN`` — required for any delegation.
* ``APIFY_USERNAME`` — default actor owner; combines with the actor key to
  form ``username~slug``.
* ``APIFY_ACTOR_<KEY>`` — per-actor override, must be in ``username~slug``
  form. Takes precedence over ``APIFY_USERNAME``.
* ``UK_PROPERTY_APIFY_MODE`` — ``auto`` (default, delegate if token is
  present), ``off`` (never delegate), ``force`` (raise if anything is
  missing).

The Apify client SDK is imported lazily so tests — and any caller that
doesn't have the ``apify`` dep installed — can import this module, call
``resolve`` (which will return ``None`` if not configured), and never touch
the SDK.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from uk_property_apify_client.actors import (
    KNOWN_ACTOR_SLUGS,
    ActorId,
    ActorKey,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("uk_property_apify_client")


class DelegationError(RuntimeError):
    """Raised when an Apify delegation cannot complete.

    Covers: missing ``apify-client`` dep at call time, ``resolve`` errors
    under ``UK_PROPERTY_APIFY_MODE=force``, non-``SUCCEEDED`` run statuses,
    malformed env overrides, and any uncaught SDK transport error re-raised
    from :meth:`ApifyDelegation.call`. Consumers map this to a user-facing
    error or (more usually) fall back to the local path.
    """


@dataclass(frozen=True, slots=True)
class ActorCallResult:
    """Materialised output of one hosted-actor run.

    Shipped deliberately eager: both the dataset and the KV records are
    pulled into memory by :meth:`ApifyDelegation.call` before this struct
    is returned, so consumers don't need to know about the ``apify-client``
    chain. If a future caller needs streaming, the right move is a separate
    ``iter_items`` helper, not changing the shape of this type.
    """

    status: str
    """Final run status as reported by Apify. Always ``"SUCCEEDED"`` in
    practice because :meth:`ApifyDelegation.call` raises
    :class:`DelegationError` on any other value; surfaced here for logging /
    telemetry."""

    run_id: str
    """Apify run ID (UUID-ish). Useful for linking back to the run console
    or filing support tickets."""

    actor_id: ActorId
    """The actor this result came from (``username~slug`` preserved). Not
    strictly necessary since the caller already knows, but it's cheap and
    makes structured logging cleaner."""

    items: list[dict[str, Any]] = field(default_factory=list)
    """Full default-dataset contents, one dict per row. Shape depends on
    the actor: listings actors push :class:`Listing` records, A4 pushes a
    row per postcode, A5 pushes a row per council-application, A7 pushes a
    row per seed with an embedded graph. Consumers are expected to know
    which actor they called."""

    run_meta: dict[str, Any] | None = None
    """Value of the ``RUN_META`` key-value-store record, or ``None`` if the
    actor didn't write one. Every actor in this repo writes a dict with
    ``started_at`` / ``actor_version`` / ``source`` / ``totals`` plus a
    per-item breakdown under a per-actor key (``per_council`` / ``per_seed``
    / ``per_postcode`` / etc.)."""

    errors: list[dict[str, Any]] | None = None
    """Value of the ``ERRORS`` KV record, or ``None`` if the run completed
    without any unit-level errors. Contents mirror :attr:`run_meta` but
    filtered to just the failing rows."""

    stats: dict[str, Any] = field(default_factory=dict)
    """Raw ``run.stats`` blob from Apify (runtime, memory, request count,
    compute units consumed). Useful for billing attribution."""


@dataclass(frozen=True, slots=True)
class ApifyDelegation:
    """Resolved delegation config for one actor.

    Construct via :meth:`resolve` in normal use; direct instantiation is
    reserved for tests and for non-env-backed callers (e.g. a future
    config-file-driven pipeline).
    """

    api_token: str
    """Apify personal or organisation token (``APIFY_API_TOKEN``). Never
    logged; :meth:`__repr__` redacts it below."""

    actor_id: ActorId
    """Target actor identifier."""

    timeout_s: float = 600.0
    """Wall-clock cap on the run, forwarded to the SDK as
    ``timeout_secs``. Default of 10 minutes comfortably covers the heaviest
    current actor (A5 with ``hydrateDetails`` across all councils); bump
    explicitly for long-fan-out jobs."""

    memory_mb: int = 1024
    """Memory cap forwarded as ``memory_mbytes``. Default matches the base
    Apify plan; raise for A5 full-council sweeps with hydration (~2 GB),
    keep at 1 GB for the rest."""

    build: str | None = None
    """Optional actor build tag (``"latest"``, ``"beta"``, or a specific
    version). ``None`` means Apify picks the default (latest)."""

    def __repr__(self) -> str:
        return (
            f"ApifyDelegation(actor_id={self.actor_id.full_id!r}, "
            f"timeout_s={self.timeout_s}, memory_mb={self.memory_mb}, "
            f"build={self.build!r}, api_token=<redacted>)"
        )

    @classmethod
    def resolve(cls, actor_key: ActorKey) -> ApifyDelegation | None:
        """Resolve delegation config for ``actor_key`` from the environment.

        Returns ``None`` when delegation is off for this process, which
        consumers treat as "fall back to the local path". Raises
        :class:`DelegationError` only when ``UK_PROPERTY_APIFY_MODE=force``
        is set but the config can't be completed — a force-mode caller is
        asking us to crash loudly on misconfig rather than silently do the
        wrong thing.

        See the module docstring for the precedence rules.
        """
        if actor_key not in KNOWN_ACTOR_SLUGS:  # pragma: no cover - Literal guard
            raise DelegationError(
                f"Unknown actor key {actor_key!r}; "
                f"expected one of {KNOWN_ACTOR_SLUGS}"
            )

        mode = os.getenv("UK_PROPERTY_APIFY_MODE", "auto").strip().lower()
        if mode in {"off", "no", "0", "false", "disabled"}:
            return None
        if mode not in {"auto", "on", "force"}:
            raise DelegationError(
                f"UK_PROPERTY_APIFY_MODE={mode!r} is not one of "
                "'auto' | 'off' | 'on' | 'force'"
            )

        token = (os.getenv("APIFY_API_TOKEN") or "").strip()
        if not token:
            if mode == "force":
                raise DelegationError(
                    "UK_PROPERTY_APIFY_MODE=force but APIFY_API_TOKEN is unset"
                )
            return None

        per_actor_var = _actor_env_var(actor_key)
        override = (os.getenv(per_actor_var) or "").strip()
        if override:
            try:
                actor_id = ActorId.parse(override)
            except ValueError as exc:
                raise DelegationError(
                    f"{per_actor_var}={override!r} is not a valid "
                    "'username~actor-slug' identifier"
                ) from exc
        else:
            username = (os.getenv("APIFY_USERNAME") or "").strip()
            if not username:
                if mode == "force":
                    raise DelegationError(
                        f"No actor ID for {actor_key!r}: set {per_actor_var} "
                        "or APIFY_USERNAME"
                    )
                return None
            actor_id = ActorId(username=username, slug=actor_key)

        timeout_s = _env_float("UK_PROPERTY_APIFY_TIMEOUT_S", default=600.0)
        memory_mb = _env_int("UK_PROPERTY_APIFY_MEMORY_MB", default=1024)
        build = (os.getenv("UK_PROPERTY_APIFY_BUILD") or None)

        return cls(
            api_token=token,
            actor_id=actor_id,
            timeout_s=timeout_s,
            memory_mb=memory_mb,
            build=build,
        )

    async def call(
        self,
        actor_input: dict[str, Any],
        *,
        client_factory: Callable[[str], Any] | None = None,
    ) -> ActorCallResult:
        """Fire the actor with ``actor_input``, await completion, and return
        the materialised result.

        ``client_factory`` is a seam for tests: inject a callable that takes
        the API token and returns a fake ``apify-client``-like object
        exposing ``.actor(id).call(...)``, ``.dataset(id).iterate_items()``,
        and ``.key_value_store(id).get_record(key)``. Production callers
        leave it as ``None`` and the real SDK is imported lazily.

        Raises :class:`DelegationError` on any non-success path (missing
        SDK, non-``SUCCEEDED`` run, malformed response, transport error).
        """
        if client_factory is None:
            try:
                from apify_client import ApifyClientAsync
            except ImportError as exc:  # pragma: no cover - runtime-only dep
                raise DelegationError(
                    "apify-client is not installed. Install "
                    "'uk-property-apify-client' with its default deps "
                    "(which pulls apify-client) or add apify-client "
                    "directly to your environment."
                ) from exc
            client_factory = ApifyClientAsync  # type: ignore[assignment]

        assert client_factory is not None
        client = client_factory(self.api_token)

        try:
            run = await client.actor(self.actor_id.full_id).call(
                run_input=actor_input,
                timeout_secs=int(self.timeout_s),
                memory_mbytes=self.memory_mb,
                build=self.build,
            )
        except DelegationError:
            raise
        except Exception as exc:
            raise DelegationError(
                f"Apify actor {self.actor_id.full_id} call failed: {exc}"
            ) from exc

        if run is None:
            raise DelegationError(
                f"Apify actor {self.actor_id.full_id} returned no run record"
            )

        status = str(run.get("status") or "").strip().upper()
        run_id = str(run.get("id") or "")
        if status != "SUCCEEDED":
            logger.warning(
                "apify delegation failed: actor=%s run=%s status=%s",
                self.actor_id.full_id,
                run_id,
                status,
            )
            raise DelegationError(
                f"Apify actor {self.actor_id.full_id} run {run_id!r} "
                f"ended with status={status!r}"
            )

        dataset_id = run.get("defaultDatasetId")
        items: list[dict[str, Any]] = []
        if dataset_id:
            async for item in client.dataset(str(dataset_id)).iterate_items():
                items.append(dict(item))

        kv_id = run.get("defaultKeyValueStoreId")
        run_meta: dict[str, Any] | None = None
        errors: list[dict[str, Any]] | None = None
        if kv_id:
            kv_store = client.key_value_store(str(kv_id))
            meta_record = await kv_store.get_record("RUN_META")
            if meta_record is not None:
                value = meta_record.get("value")
                if isinstance(value, dict):
                    run_meta = dict(value)
            errors_record = await kv_store.get_record("ERRORS")
            if errors_record is not None:
                value = errors_record.get("value")
                if isinstance(value, list):
                    errors = [dict(row) if isinstance(row, dict) else row for row in value]

        stats = run.get("stats") or {}
        return ActorCallResult(
            status=status,
            run_id=run_id,
            actor_id=self.actor_id,
            items=items,
            run_meta=run_meta,
            errors=errors,
            stats=dict(stats) if isinstance(stats, dict) else {},
        )


def _actor_env_var(actor_key: str) -> str:
    """``APIFY_ACTOR_ZOOPLA_LISTINGS`` etc. Upper + hyphens to underscores."""
    return "APIFY_ACTOR_" + actor_key.upper().replace("-", "_")


def _env_float(name: str, *, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise DelegationError(f"{name}={raw!r} is not a valid float") from exc


def _env_int(name: str, *, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise DelegationError(f"{name}={raw!r} is not a valid integer") from exc


__all__ = [
    "ActorCallResult",
    "ApifyDelegation",
    "DelegationError",
]
