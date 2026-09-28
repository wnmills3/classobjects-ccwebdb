"""The shapes every report is built from: a column, a result, and a report.

A report group module (`data_quality.py`, `collection.py`, ...) builds these
and registers a `Report`; nothing else in the backend -- the API, the CLI,
the console -- reads anything but these shapes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

#: How a column's values are formatted, everywhere a report is shown or
#: exported. Never the query -- a report groups and filters however it
#: needs to; this only tells a reader how to print what came back.
ColumnKind = Literal["text", "count", "money", "percent", "date", "ounces"]


@dataclass(frozen=True)
class Column:
    """One column of a report's table.

    `key` names the field in each row dict; `label` is what a reader sees;
    `kind` is one of `ColumnKind`, above.
    """

    key: str
    label: str
    kind: ColumnKind


@dataclass(frozen=True)
class ReportResult:
    """What a report's `run` returns.

    `drills` is index-aligned with `rows`: `drills[i]` is the console path
    (with its query string, relative to the console base) that row `i`
    drills down to, or `None` when that row has no drill-down. The
    alignment is checked here, once, rather than trusted by every report
    and every reader -- a mismatch would silently point a click at the
    wrong row.

    `totals` is a row shaped like the others, for the columns a total means
    something for; omitted (`None`) when no total applies. `notes` is
    anything a reader must know to read the table right, such as "no spot
    price recorded; melt value omitted".
    """

    columns: list[Column]
    rows: list[dict[str, object]]
    totals: dict[str, object] | None = None
    drills: list[str | None] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Refuse a result whose `drills` do not line up with `rows`."""
        if len(self.drills) != len(self.rows):
            raise ValueError(
                f"drills has {len(self.drills)} entries for "
                f"{len(self.rows)} rows -- they must be index-aligned"
            )


#: What every report's `run` looks like: given a database session and an
#: instance of its own parameters model, it returns a result. The
#: parameters model class itself lives on `Report.params`.
ReportRun = Callable[[Session, BaseModel], ReportResult]


@dataclass(frozen=True)
class Report:
    """One entry in the catalog.

    `params` is a pydantic model *class*, not an instance -- the catalog
    reads its fields (each field's `title` becomes the label a parameter
    form shows) to describe what the report takes; `run` is called with an
    instance of it. A report with no parameters uses an empty model.
    """

    id: str
    group: str
    title: str
    purpose: str
    params: type[BaseModel]
    run: ReportRun
