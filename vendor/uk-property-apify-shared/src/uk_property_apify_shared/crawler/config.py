"""Runtime configuration for the crawler."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class CrawlerConfig:
    """All tunables for a crawl session.

    Only :attr:`proxy_url` and :attr:`discord_webhook_url` are usually needed on
    Apify; the defaults are tuned to be polite on real portals while still fast
    enough for interactive use.
    """

    proxy_url: str | None = None
    """Upstream HTTP/HTTPS proxy (e.g. Apify residential). ``None`` = direct."""

    discord_webhook_url: str | None = None
    """Webhook for anti-bot escalations. ``None`` = no alerts."""

    default_rate_per_sec: float = 1.0
    """Fallback requests-per-second for any domain not listed in
    :attr:`per_domain_rate_per_sec`."""

    per_domain_rate_per_sec: dict[str, float] = field(
        default_factory=lambda: {
            "www.zoopla.co.uk": 0.5,
            "www.rightmove.co.uk": 0.5,
            "www.onthemarket.com": 0.75,
        }
    )
    """Per-host ceiling. Tuned conservative - portals have aggressive WAFs."""

    request_timeout_s: float = 30.0
    """Per-attempt hard timeout (for both HTTP and browser fetchers)."""

    max_retries: int = 3
    """How many times to retry a transient (5xx / timeout) failure before
    escalating to the next tier (HTTP -> browser) or giving up."""

    backoff_initial_s: float = 1.0
    """Initial exponential-backoff delay between retries."""

    backoff_max_s: float = 30.0
    """Cap on the exponential-backoff delay."""

    user_agents: tuple[str, ...] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    )
    """Pool of user-agents to rotate through. Curl-cffi's Chrome impersonation
    picks the TLS/HTTP2 fingerprint; the UA header just has to match."""

    impersonate: str = "chrome124"
    """``curl_cffi`` impersonation target. Valid values: ``chrome124``,
    ``chrome120``, ``safari17_0``, ``edge99``, ``firefox_esr``, etc. Keep this
    current - stale fingerprints are easier to catch."""

    enable_browser_fallback: bool = True
    """Escalate to Playwright on tier-1 block. Disable in tests / CI without
    a browser install."""

    browser_headless: bool = True
    """Playwright headless mode. Set ``False`` for local dev only."""

    viewport_width: int = 1366
    viewport_height: int = 900
    """Browser viewport - 1366x900 is the most common desktop size."""

    warm_session: bool = True
    """Visit the site's home page before hitting search URLs to collect cookies
    and make the session look more organic."""

    accept_language: str = "en-GB,en;q=0.9"
    """``Accept-Language`` header - UK portals gate heavily on locale."""

    extra_headers: dict[str, str] = field(default_factory=dict)
    """Additional request headers merged into every request."""

    @classmethod
    def from_env(cls) -> CrawlerConfig:
        """Build from typical environment variables.

        Env vars consulted:

        * ``APIFY_PROXY_URL`` / ``PROXY_URL``
        * ``DISCORD_WEBHOOK_URL``
        * ``CRAWLER_RATE_PER_SEC``
        * ``CRAWLER_DISABLE_BROWSER`` - set to ``1`` to turn off the Playwright
          fallback tier (useful for tests / CI without a browser install).
        * ``CRAWLER_DISABLE_WARM_SESSION`` - set to ``1`` to skip the initial
          home-page GET per domain.
        * ``CRAWLER_BROWSER_HEADLESS`` - set to ``0`` / ``false`` / ``no`` to
          launch Chromium in headed (visible) mode. Zoopla's Cloudflare
          fingerprints headless Chromium even with stealth patches, so the
          headed path is the only way to bypass CF from a home IP without a
          residential proxy. On Apify Cloud (residential proxies) leave the
          default (``1`` / headless).
        """
        proxy = os.environ.get("APIFY_PROXY_URL") or os.environ.get("PROXY_URL")
        discord = os.environ.get("DISCORD_WEBHOOK_URL")
        rate = os.environ.get("CRAWLER_RATE_PER_SEC")
        kwargs: dict[str, object] = {}
        if proxy:
            kwargs["proxy_url"] = proxy
        if discord:
            kwargs["discord_webhook_url"] = discord
        if rate:
            kwargs["default_rate_per_sec"] = float(rate)
        if os.environ.get("CRAWLER_DISABLE_BROWSER") == "1":
            kwargs["enable_browser_fallback"] = False
        if os.environ.get("CRAWLER_DISABLE_WARM_SESSION") == "1":
            kwargs["warm_session"] = False
        headless = os.environ.get("CRAWLER_BROWSER_HEADLESS")
        if headless is not None and headless.strip().lower() in {"0", "false", "no", "off"}:
            kwargs["browser_headless"] = False
        return cls(**kwargs)  # type: ignore[arg-type]
