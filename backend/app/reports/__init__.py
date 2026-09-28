"""The report registry: one entry per report, keyed by id.

As `app/issues.py` holds its checks, this holds `REPORTS` -- adding a report
is adding one `register()` call in a group module (`data_quality.py`,
`collection.py`, `purchasing.py`, `selling.py`, `money.py`), not touching an
API route, a CLI command, or the console page that all read this one dict.
"""

from __future__ import annotations

from .base import Report

REPORTS: dict[str, Report] = {}


def register(report: Report) -> Report:
    """Add `report` to `REPORTS`; refuse a second report with the same id.

    Returns `report`, so a group module can write
    `FOO = register(Report(...))` at module scope.
    """
    if report.id in REPORTS:
        raise ValueError(f"a report is already registered as {report.id!r}")
    REPORTS[report.id] = report
    return report


__all__ = ["REPORTS", "register"]
