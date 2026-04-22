"""UK public-sector tender clients — Contracts Finder + Find a Tender Service.

Two transports, one canonical :class:`Tender`:

* :class:`ContractsFinderClient` — below-threshold UK procurement,
  public / unauthenticated, POST search with JSON ``SearchCriteria``
  body, no cursor pagination (use ``size`` + date windows to fan out).

* :class:`FTSClient` — above-threshold UK procurement, OCDS 1.1.5
  release-package schema, ``CDP-Api-Key`` header auth, cursor-paginated.

Both clients expose ``.search_tenders(query: TenderQuery)`` for normalised
rows, plus ``.*_raw`` / ``.iter_release_packages`` for callers that need
access to the upstream payload. A :class:`TenderQuery` translates to the
superset of filters both sources understand; filters a given source can't
execute natively are applied in-memory against the returned rows.

Typical property-use-case call flow::

    from uk_property_apis.tenders import (
        ContractsFinderClient,
        TenderQuery,
    )

    async with ContractsFinderClient() as cf:
        tenders = await cf.search_tenders(
            TenderQuery(
                cpv_codes=["45211000", "70000000"],
                regions=["London"],
                limit=100,
            )
        )
"""

from __future__ import annotations

from uk_property_apis.tenders.contracts_finder_client import (
    ContractsFinderClient,
)
from uk_property_apis.tenders.contracts_finder_client import (
    search_tenders as cf_search_tenders,
)
from uk_property_apis.tenders.fts_client import (
    FTSClient,
)
from uk_property_apis.tenders.fts_client import (
    search_tenders as fts_search_tenders,
)
from uk_property_apis.tenders.models import (
    Tender,
    TenderClassification,
    TenderLocation,
    TenderOrg,
    TenderQuery,
    TenderSource,
    TenderStatus,
    TenderValue,
)

__all__ = [
    "ContractsFinderClient",
    "FTSClient",
    "Tender",
    "TenderClassification",
    "TenderLocation",
    "TenderOrg",
    "TenderQuery",
    "TenderSource",
    "TenderStatus",
    "TenderValue",
    "cf_search_tenders",
    "fts_search_tenders",
]
