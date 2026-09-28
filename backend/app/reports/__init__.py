"""The report catalog: every report registered, reachable from one import.

`REPORTS` and `register` live in `app.reports.registry`; this module's own
job is to import every group module (`data_quality`, and the ones the plan's
later phases add) so each one's `register()` calls have run -- and `REPORTS`
is complete -- by the time anything imports `app.reports` itself, whether
that is an API route, the CLI, or a test. A test importing a group module a
second time cannot trip `register`'s duplicate check: Python caches an
already-imported module and does not re-run it.
"""

from __future__ import annotations

from .collection import CB_HOLDINGS
from .data_quality import DQ_COMPLETENESS, DQ_ISSUES
from .registry import REPORTS, register

__all__ = ["CB_HOLDINGS", "DQ_COMPLETENESS", "DQ_ISSUES", "REPORTS", "register"]
