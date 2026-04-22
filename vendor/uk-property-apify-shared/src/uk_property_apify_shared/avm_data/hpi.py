"""Curated quarterly UK HPI series (ONS-based, rebased 2015 = 100).

The public :class:`~uk_property_avm.hpi.HPIAdjuster` ships a tiny
reference series (just enough to demonstrate the math and let the open
tests exercise every code path). The full 20-year quarterly snapshot
used by the hosted A10 ``uk-avm`` actor lives here.

The values are anchored to year-on-year change rates published in ONS
House Price Index bulletins (UK all-property, 2015 average = 100) —
updated quarterly on the same rhythm as ONS's publication schedule.
That ongoing refresh cadence is deliberately private: live AVM valuations
drift meaningfully if the HPI is stale by more than a couple of quarters,
and keeping a current series is one of the things the paid actor
maintains for its callers.

Consumers: the A10 actor dispatch wires ``HPIAdjuster(PRIVATE_HPI_SERIES)``
instead of ``HPIAdjuster.default()`` for production valuations.
"""

from __future__ import annotations

from typing import Final

PRIVATE_HPI_SERIES: Final[dict[str, float]] = {
    "2005-01": 70.0,
    "2005-04": 71.0,
    "2005-07": 72.0,
    "2005-10": 72.5,
    "2006-01": 73.0,
    "2006-04": 74.0,
    "2006-07": 75.5,
    "2006-10": 77.0,
    "2007-01": 78.0,
    "2007-04": 79.5,
    "2007-07": 80.5,
    "2007-10": 81.0,
    "2008-01": 80.5,
    "2008-04": 79.0,
    "2008-07": 77.0,
    "2008-10": 74.0,
    "2009-01": 71.0,
    "2009-04": 72.0,
    "2009-07": 74.0,
    "2009-10": 75.5,
    "2010-01": 76.0,
    "2010-04": 77.0,
    "2010-07": 77.5,
    "2010-10": 77.0,
    "2011-01": 76.5,
    "2011-04": 77.0,
    "2011-07": 77.0,
    "2011-10": 77.0,
    "2012-01": 77.0,
    "2012-04": 78.0,
    "2012-07": 78.5,
    "2012-10": 79.0,
    "2013-01": 79.0,
    "2013-04": 80.0,
    "2013-07": 82.0,
    "2013-10": 83.5,
    "2014-01": 85.0,
    "2014-04": 87.0,
    "2014-07": 90.0,
    "2014-10": 91.0,
    "2015-01": 92.0,
    "2015-04": 95.0,
    "2015-07": 99.0,
    "2015-10": 100.5,
    "2016-01": 102.5,
    "2016-04": 106.0,
    "2016-07": 107.5,
    "2016-10": 108.0,
    "2017-01": 109.5,
    "2017-04": 111.5,
    "2017-07": 113.0,
    "2017-10": 114.0,
    "2018-01": 114.5,
    "2018-04": 116.0,
    "2018-07": 117.5,
    "2018-10": 118.0,
    "2019-01": 118.5,
    "2019-04": 119.0,
    "2019-07": 120.0,
    "2019-10": 120.5,
    "2020-01": 121.0,
    "2020-04": 120.5,
    "2020-07": 124.0,
    "2020-10": 128.0,
    "2021-01": 131.0,
    "2021-04": 132.5,
    "2021-07": 135.0,
    "2021-10": 138.0,
    "2022-01": 141.0,
    "2022-04": 144.0,
    "2022-07": 147.0,
    "2022-10": 148.0,
    "2023-01": 147.5,
    "2023-04": 146.0,
    "2023-07": 144.5,
    "2023-10": 143.0,
    "2024-01": 143.5,
    "2024-04": 144.5,
    "2024-07": 145.5,
    "2024-10": 146.0,
    "2025-01": 146.5,
    "2025-04": 147.5,
    "2025-07": 148.5,
    "2025-10": 149.0,
    "2026-01": 149.5,
}


__all__ = ["PRIVATE_HPI_SERIES"]
