"""Public reference registry of UK councils running Idox Public Access.

This module deliberately ships only two councils — one per transport
— as canonical *reference configurations* for the open-source library:

* **Lambeth** — ArcGIS FeatureServer at
  ``planning.lambeth.gov.uk/server/rest/services/PALIVE/LIVEUniformPA_Planning/FeatureServer``.
  Exercises the fast path (SQL-style filters, pagination, geometry).
* **Westminster** — HTML-only (no public FeatureServer). Exercises the
  slow fallback path (CSRF-aware form POST + simple-search parse).

Together those two cover every code path in the public
:class:`ArcGISPlanningClient` and :class:`HTMLPlanningClient`. Smoke
probes, fixture tests, and the public agent tool all target this
reference set, so the library ships with a working end-to-end demo
out of the box.

The **full** curated registry of verified UK councils — plus the
ongoing maintenance of URL layouts, ArcGIS availability flips, and
HTML form variants — lives in the private
``uk-property-apify-shared`` package and powers the hosted A5
``planning-aggregator`` Apify actor. That split is deliberate: the
transport code, parsers, and :class:`CouncilConfig` model are open
(so users can hand-register any council they want at runtime), but
the verified production registry is the ongoing-work product of
the paid service.

Users who only need one or two additional councils can drop a
:class:`CouncilConfig` into their own dict at runtime — the shape is
public:

    from uk_property_apis.idox import CouncilConfig

    my_council = CouncilConfig(
        slug="camden",
        name="Camden",
        public_access_base_url="https://accountforms.camden.gov.uk",
    )
"""

from __future__ import annotations

from collections.abc import Iterable

from uk_property_apis.idox.models import CouncilConfig


def _council(
    slug: str,
    name: str,
    public_access_host: str,
    *,
    arcgis_base_url: str | None = None,
) -> CouncilConfig:
    return CouncilConfig(
        slug=slug,
        name=name,
        public_access_base_url=f"https://{public_access_host}",
        arcgis_base_url=arcgis_base_url,
    )


KNOWN_COUNCILS: dict[str, CouncilConfig] = {
    c.slug: c
    for c in (
        # Reference ArcGIS council — canonical fast-path example.
        _council(
            "lambeth",
            "Lambeth",
            "planning.lambeth.gov.uk",
            arcgis_base_url="https://planning.lambeth.gov.uk/server",
        ),
        # Reference HTML-only council — canonical fallback-path example.
        _council(
            "westminster",
            "Westminster",
            "idoxpa.westminster.gov.uk",
        ),
    )
}


def get_council(slug: str) -> CouncilConfig:
    """Look up a known council by slug (case-insensitive)."""

    key = slug.strip().lower()
    try:
        return KNOWN_COUNCILS[key]
    except KeyError as exc:
        known = ", ".join(sorted(KNOWN_COUNCILS)) or "(none)"
        msg = f"Unknown council {slug!r}; known: {known}"
        raise KeyError(msg) from exc


def arcgis_enabled_councils() -> Iterable[CouncilConfig]:
    """Yield the reference councils that publish a reachable ArcGIS FeatureServer."""

    return (c for c in KNOWN_COUNCILS.values() if c.arcgis_base_url is not None)


__all__ = [
    "KNOWN_COUNCILS",
    "arcgis_enabled_councils",
    "get_council",
]
