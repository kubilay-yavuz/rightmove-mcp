"""Valuation Office Agency (VOA) council-tax band lookups.

Scrapes the public ``check-council-tax-band`` service at
``https://www.tax.service.gov.uk/check-council-tax-band/`` and returns typed
:class:`CouncilTaxBand` rows. The service covers **England and Wales** only;
Scottish postcodes are the Scottish Assessors' Association's territory and
return no rows here.
"""

from __future__ import annotations

from uk_property_apis.voa.client import VOAClient
from uk_property_apis.voa.models import CouncilTaxBand, CouncilTaxSearchPage

__all__ = ["CouncilTaxBand", "CouncilTaxSearchPage", "VOAClient"]
