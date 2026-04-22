"""Alerting - surface anti-bot escalations to humans.

The crawler only calls the sink when tier-2 (browser) has also failed, so
the signal-to-noise ratio stays reasonable. Sinks are deliberately tiny so
tests can plug in a fake :class:`AlertSink` without touching HTTP.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class AlertSink(Protocol):
    """Something that receives anti-bot escalation events."""

    async def alert(
        self,
        *,
        title: str,
        message: str,
        fields: dict[str, str] | None = None,
    ) -> None:
        """Send a single escalation. Implementations must never raise."""
        ...


class NullAlertSink:
    """No-op sink - default when no webhook is configured."""

    async def alert(
        self,
        *,
        title: str,
        message: str,
        fields: dict[str, str] | None = None,
    ) -> None:
        logger.warning("alert suppressed (no sink): %s - %s", title, message)


class DiscordAlertSink:
    """Post escalations to a Discord webhook.

    Uses :mod:`httpx` directly so it works alongside either transport. Errors
    in the sink itself are logged, never propagated - losing an alert should
    never take the crawler down.
    """

    def __init__(self, webhook_url: str, *, username: str = "uk-property-crawler") -> None:
        if not webhook_url:
            raise ValueError("webhook_url is required")
        self._webhook_url = webhook_url
        self._username = username

    async def alert(
        self,
        *,
        title: str,
        message: str,
        fields: dict[str, str] | None = None,
    ) -> None:
        import httpx

        embed: dict[str, Any] = {
            "title": title,
            "description": message,
            "color": 0xE74C3C,
        }
        if fields:
            embed["fields"] = [
                {"name": k, "value": v[:1024] or "(empty)", "inline": True}
                for k, v in fields.items()
            ]
        payload = {"username": self._username, "embeds": [embed]}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self._webhook_url, json=payload)
                if response.status_code >= 400:
                    logger.error(
                        "discord webhook returned %s: %s",
                        response.status_code,
                        response.text[:200],
                    )
        except Exception:
            logger.exception("discord alert sink failed")
