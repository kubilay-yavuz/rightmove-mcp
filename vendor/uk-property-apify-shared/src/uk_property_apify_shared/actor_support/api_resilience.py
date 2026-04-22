"""Shared resilience knobs for API-backed actors.

Inputs that mix this in get ``maxAttemptsPerUnit`` / ``unitTimeoutSec`` /
``backoffInitialSec`` / ``backoffMaxSec`` with sensible defaults, matching
the knob surface of :class:`ActorInput` for listings actors.

Apply via composition in the actor's own pydantic input model::

    class MyInput(ApiResilienceInput):
        ...  # actor-specific fields

Then forward to hooks::

    hooks = ApiActorHooks(
        ...,
        **parsed.api_resilience_hook_kwargs(),
    )
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ApiResilienceInput(BaseModel):
    """Mix-in pydantic model exposing the run_api_actor resilience knobs.

    Designed to be composed into actor input models. Validation enforces
    that ``backoffMaxSec >= backoffInitialSec`` so config errors fail
    fast at parse time.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    max_attempts_per_unit: int = Field(
        1,
        ge=1,
        le=10,
        alias="maxAttemptsPerUnit",
        description=(
            "Retry each unit up to this many times on transient "
            "failures. Between attempts we apply capped exponential "
            "backoff with jitter. Default 1 keeps legacy behaviour "
            "(no retries)."
        ),
    )
    unit_timeout_s: float | None = Field(
        None,
        gt=0,
        le=14_400,
        alias="unitTimeoutSec",
        description=(
            "Hard per-unit time budget in seconds. ``None`` disables "
            "the timeout and leans on each HTTP client's per-request "
            "budget instead."
        ),
    )
    backoff_initial_s: float = Field(
        1.0,
        ge=0.1,
        le=60.0,
        alias="backoffInitialSec",
        description="Initial retry delay. Grows exponentially to backoffMaxSec.",
    )
    backoff_max_s: float = Field(
        30.0,
        ge=1.0,
        le=600.0,
        alias="backoffMaxSec",
        description="Cap on the exponential backoff between retries.",
    )

    @model_validator(mode="after")
    def _coherent_backoff(self) -> ApiResilienceInput:
        if self.backoff_max_s < self.backoff_initial_s:
            raise ValueError(
                f"backoffMaxSec ({self.backoff_max_s}) must be >= "
                f"backoffInitialSec ({self.backoff_initial_s})"
            )
        return self

    def api_resilience_hook_kwargs(self) -> dict[str, Any]:
        """Return kwargs suitable for :class:`ApiActorHooks` construction.

        Shape matches the ``*_per_unit`` / ``*_s`` callable fields:

        * ``max_attempts_per_unit``
        * ``unit_timeout_s``
        * ``backoff_initial_s``
        * ``backoff_max_s``
        """

        max_attempts = self.max_attempts_per_unit
        timeout = self.unit_timeout_s
        initial = self.backoff_initial_s
        mx = self.backoff_max_s
        return {
            "max_attempts_per_unit": lambda _inp: max_attempts,
            "unit_timeout_s": lambda _inp: timeout,
            "backoff_initial_s": lambda _inp: initial,
            "backoff_max_s": lambda _inp: mx,
        }


API_RESILIENCE_SCHEMA_PROPERTIES: dict[str, dict[str, Any]] = {
    "maxAttemptsPerUnit": {
        "title": "Max attempts per unit",
        "type": "integer",
        "default": 1,
        "minimum": 1,
        "maximum": 10,
        "description": (
            "Retry each unit of work (postcode / council / seed / etc.) "
            "up to this many times on transient failures. Exponential "
            "backoff with jitter between attempts."
        ),
        "editor": "number",
    },
    "unitTimeoutSec": {
        "title": "Unit timeout (seconds)",
        "type": "integer",
        "minimum": 1,
        "maximum": 14400,
        "nullable": True,
        "description": (
            "Hard per-unit time budget. Leave blank to rely on each "
            "client's native per-request timeouts."
        ),
        "editor": "number",
    },
    "backoffInitialSec": {
        "title": "Backoff initial (seconds)",
        "type": "integer",
        "default": 1,
        "minimum": 1,
        "maximum": 60,
        "description": "Initial retry delay; grows exponentially to backoffMaxSec.",
        "editor": "number",
    },
    "backoffMaxSec": {
        "title": "Backoff max (seconds)",
        "type": "integer",
        "default": 30,
        "minimum": 1,
        "maximum": 600,
        "description": "Cap on exponential backoff between retries.",
        "editor": "number",
    },
}
"""Drop-in JSON schema fragment for Apify ``input_schema.json`` files.

Each value is a JSON Schema property definition with Apify-specific
``editor`` hints so the UI renders a clean number input. Merge into the
``properties`` block of any API-actor input schema::

    {
      "properties": {
        ...actor-specific...,
        **API_RESILIENCE_SCHEMA_PROPERTIES
      }
    }
"""
