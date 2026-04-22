"""Curated datasets used by the hosted AVM actor (A10).

These modules carry the "maintained data" that tightens the AVM moat:
the full quarterly UK HPI series (for price normalisation across multi-year
comparable pools) and the full rail-station list (for the neighbourhood
accessibility feature).

The public library packages still ship minimal reference versions so
the open-source code paths work end-to-end; the hosted actor, which
imports from here, wires in the full production datasets.
"""

from __future__ import annotations

from uk_property_apify_shared.avm_data.hpi import PRIVATE_HPI_SERIES
from uk_property_apify_shared.avm_data.stations import PRIVATE_STATIONS

__all__ = [
    "PRIVATE_HPI_SERIES",
    "PRIVATE_STATIONS",
]
