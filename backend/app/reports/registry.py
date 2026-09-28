"""The report registry: one entry per report, keyed by id.

As `app/issues.py` holds its checks, this holds `REPORTS` -- adding a report
is adding one `register()` call in a group module (`data_quality.py`,
`collection.py`, `purchasing.py`, `selling.py`), not touching an
API route, a CLI command, or the console page that all read this one dict.

Kept apart from `app/reports/__init__.py` so a group module can import
`register` directly (`from .registry import register`) rather than from the
package itself: `__init__.py` imports every group module -- to make `REPORTS`
complete whenever `app.reports` is imported -- so a group module importing
`from . import register` would need that name already bound in the partly
initialized package, forcing the import to sit below the group imports at
the bottom of the file, out of normal reading order and past code ruff's
import-order rule refuses to allow there. A separate module has no such
ordering constraint.
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
