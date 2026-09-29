"""The report catalog: every report registered, reachable from one import.

`REPORTS` and `register` live in `app.reports.registry`; this module's own
job is to import every group module (`collection`, `data_quality`,
`purchasing`, `selling`) so each one's `register()` calls have run -- and
`REPORTS`
is complete -- by the time anything imports `app.reports` itself, whether
that is an API route, the CLI, or a test. A test importing a group module a
second time cannot trip `register`'s duplicate check: Python caches an
already-imported module and does not re-run it.
"""

from __future__ import annotations

from .collection import CB_HOLDINGS
from .data_quality import (
    DQ_COMPLETENESS,
    DQ_DERIVED,
    DQ_ISSUES,
    DQ_LOCATIONS,
    DQ_PHOTOS,
    DQ_PURCHASES,
)
from .purchasing import PR_OUTSTANDING
from .registry import REPORTS, register
from .selling import SL_OFFERED

__all__ = [
    "CB_HOLDINGS",
    "DQ_COMPLETENESS",
    "DQ_DERIVED",
    "DQ_ISSUES",
    "DQ_LOCATIONS",
    "DQ_PHOTOS",
    "DQ_PURCHASES",
    "PR_OUTSTANDING",
    "REPORTS",
    "SL_OFFERED",
    "register",
]
