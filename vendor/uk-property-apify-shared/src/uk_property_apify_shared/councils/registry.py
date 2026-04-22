"""Curated registry of verified UK councils running Idox Public Access.

Each entry here has been hand-verified against its live Public Access
portal to determine:

1. The canonical public-access hostname (e.g. ``planning.lambeth.gov.uk``
   vs ``publicaccess.barnet.gov.uk`` — councils deploy Idox with no
   standard URL convention).
2. Whether the council's ArcGIS FeatureServer is publicly reachable
   from the open internet (observed by GET
   ``<host>/server/rest/services/PALIVE/LIVEUniformPA_Planning/FeatureServer?f=json``).
   Many councils run ArcGIS internally but front it behind a VPN /
   corporate WAF — in those cases the HTML transport is the only
   option.
3. That the HTML simple-search form accepts our CSRF-aware POST and
   returns parseable results (IDOX ships several slightly-different
   form variants across versions).

This list grows over time as new councils are onboarded. Keeping it
private means a public consumer can still hand-build a
:class:`CouncilConfig` for any IDOX council (the public transport
code, parsers, and :class:`CouncilConfig` model all ship openly) but
doesn't get the continuously-maintained verified list for free.
"""

from __future__ import annotations

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


PRIVATE_COUNCILS: dict[str, CouncilConfig] = {
    c.slug: c
    for c in (
        # ArcGIS-enabled — second verified FeatureServer (generalises the
        # transport beyond Lambeth, proves the code path works off a
        # different council's infrastructure).
        _council(
            "barnet",
            "Barnet",
            "publicaccess.barnet.gov.uk",
            arcgis_base_url="https://publicaccess.barnet.gov.uk/server",
        ),
        # HTML-only councils — ArcGIS blocked externally as of Apr 2026.
        _council(
            "manchester",
            "Manchester",
            "pa.manchester.gov.uk",
        ),
        _council(
            "southwark",
            "Southwark",
            "planning.southwark.gov.uk",
        ),
        _council(
            "leeds",
            "Leeds",
            "publicaccess.leeds.gov.uk",
        ),
    )
}


__all__ = ["PRIVATE_COUNCILS"]
