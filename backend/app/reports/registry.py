"""The report registry: one entry per report, keyed by id.

As `app/issues.py` holds its checks, this holds `REPORTS` -- adding a report
is adding one `register()` call in a group module (`data_quality.py`,
`collection.py`, `money.py`, `purchasing.py`, `selling.py`), not touching an
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

#: The groups, in the order the catalog -- the console, the API, the CLI's
#: `list` -- shows them. Stated here rather than left to whichever order
#: `__init__.py` happens to import the group modules in.
GROUP_ORDER: tuple[str, ...] = (
    "Collection",
    "Data quality",
    "Purchasing and receiving",
    "Selling",
    "Money",
)

#: Reports of every different params model are stored side by side here,
#: so the dict's value type is necessarily the widened `Report[Any]` --
#: each report keeps its own precise type at its own call site (`register`
#: and the group module that built it), which is where it matters. Kept in
#: catalog order: by `GROUP_ORDER`, then by registration within a group.
REPORTS: dict[str, Report[Any]] = {}


def register[P: BaseModel](report: Report[P]) -> Report[P]:
    """Add `report` to `REPORTS`; refuse a second id or a group not in `GROUP_ORDER`.

    `REPORTS` is re-sorted after each addition, so iterating it always
    yields catalog order: groups in `GROUP_ORDER`, and within a group the
    order the reports were registered (the sort is stable). A group missing
    from `GROUP_ORDER` is refused rather than placed at some default spot,
    so a new group cannot appear in the catalog unordered.

    Returns `report` with its own precise type still attached --
    `register(report: Report[HoldingsParams])` returns
    `Report[HoldingsParams]`, not the widened `Report[Any]` the registry
    stores it as -- so a group module can write
    `FOO = register(Report(...))` at module scope.
    """
    if report.id in REPORTS:
        raise ValueError(f"a report is already registered as {report.id!r}")
    if report.group not in GROUP_ORDER:
        raise ValueError(
            f"report {report.id!r} names group {report.group!r}, "
            "which is not in GROUP_ORDER"
        )
    REPORTS[report.id] = report
    ordered = sorted(REPORTS.values(), key=lambda r: GROUP_ORDER.index(r.group))
    REPORTS.clear()
    REPORTS.update((r.id, r) for r in ordered)
    return report


__all__ = ["GROUP_ORDER", "REPORTS", "register"]
