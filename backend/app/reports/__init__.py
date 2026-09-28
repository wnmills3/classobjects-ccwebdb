"""The report registry: one entry per report, keyed by id.

As `app/issues.py` holds its checks, this holds `REPORTS` -- adding a report
is adding one `register()` call in a group module (`data_quality.py`,
`collection.py`, `purchasing.py`, `selling.py`, `money.py`), not touching an
API route, a CLI command, or the console page that all read this one dict.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .base import Report

#: Reports of every different params model are stored side by side here,
#: so the dict's value type is necessarily the widened `Report[Any]` --
#: each report keeps its own precise type at its own call site (`register`
#: and the group module that built it), which is where it matters.
REPORTS: dict[str, Report[Any]] = {}


def register[P: BaseModel](report: Report[P]) -> Report[P]:
    """Add `report` to `REPORTS`; refuse a second report with the same id.

    Returns `report` with its own precise type still attached --
    `register(report: Report[HoldingsParams])` returns
    `Report[HoldingsParams]`, not the widened `Report[Any]` the registry
    stores it as -- so a group module can write
    `FOO = register(Report(...))` at module scope.
    """
    if report.id in REPORTS:
        raise ValueError(f"a report is already registered as {report.id!r}")
    REPORTS[report.id] = report
    return report


__all__ = ["REPORTS", "register"]


# Imported at the bottom, after REPORTS and register above exist: each group
# module (data_quality.py, and the ones the plan's later phases add) calls
# register() at import time, so this is what makes REPORTS complete whenever
# `app.reports` itself is imported -- an API route, the CLI, or a test can
# read the dict without importing every group module by hand. The names are
# re-exported through __all__ rather than imported for a bare side effect,
# so a report a test wants directly (`from app.reports import DQ_ISSUES`) is
# also reachable from here, and so the import itself is not flagged unused.
from .data_quality import DQ_COMPLETENESS, DQ_ISSUES  # noqa: E402

__all__ += ["DQ_COMPLETENESS", "DQ_ISSUES"]
