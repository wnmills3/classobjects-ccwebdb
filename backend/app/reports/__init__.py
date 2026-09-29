"""The report catalog: every report registered, reachable from one import.

`REPORTS` and `register` live in `app.reports.registry`; this module's own
job is to import every group module (`collection`, `data_quality`, `money`,
`purchasing`, `selling`) so each one's `register()` calls have run -- and
`REPORTS`
is complete -- by the time anything imports `app.reports` itself, whether
that is an API route, the CLI, or a test. A test importing a group module a
second time cannot trip `register`'s duplicate check: Python caches an
already-imported module and does not re-run it.
"""

from __future__ import annotations

from .collection import (
    CB_ATTRIBUTES,
    CB_DESIGNS,
    CB_GRADES,
    CB_HOLDINGS,
    CB_METAL,
    CB_NOTES,
)
from .data_quality import (
    DQ_COMPLETENESS,
    DQ_DERIVED,
    DQ_ISSUES,
    DQ_LOCATIONS,
    DQ_PHOTOS,
    DQ_PURCHASES,
)
from .money import MN_BASIS, MN_TAX, MN_VALUE
from .purchasing import PR_OUTSTANDING, PR_RECEIVED, PR_SOURCES, PR_SPEND
from .registry import REPORTS, register
from .selling import SL_AGING, SL_AUCTIONS, SL_FULFILMENT, SL_OFFERED, SL_SALES

__all__ = [
    "CB_ATTRIBUTES",
    "CB_DESIGNS",
    "CB_GRADES",
    "CB_HOLDINGS",
    "CB_METAL",
    "CB_NOTES",
    "DQ_COMPLETENESS",
    "DQ_DERIVED",
    "DQ_ISSUES",
    "DQ_LOCATIONS",
    "DQ_PHOTOS",
    "DQ_PURCHASES",
    "MN_BASIS",
    "MN_TAX",
    "MN_VALUE",
    "PR_OUTSTANDING",
    "PR_RECEIVED",
    "PR_SOURCES",
    "PR_SPEND",
    "REPORTS",
    "SL_AGING",
    "SL_AUCTIONS",
    "SL_FULFILMENT",
    "SL_OFFERED",
    "SL_SALES",
    "register",
]
