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

    `link_column` is the key of the column whose cell carries a row's
    drill-down link -- the cell that names what the link opens, such as a
    listing's item code rather than its venue. `None` means the first
    column. A key that names no column is refused here, for the same reason
    the drills are checked: a typo would silently drop every row's link.
    """

    columns: list[Column]
    rows: list[dict[str, object]]
    totals: dict[str, object] | None = None
    drills: list[str | None] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    link_column: str | None = None

    def __post_init__(self) -> None:
        """Refuse misaligned `drills`, or a `link_column` naming no column."""
        if len(self.drills) != len(self.rows):
            raise ValueError(
                f"drills has {len(self.drills)} entries for "
                f"{len(self.rows)} rows -- they must be index-aligned"
            )
        keys = [column.key for column in self.columns]
        if self.link_column is not None and self.link_column not in keys:
            raise ValueError(
                f"link_column {self.link_column!r} is not one of the columns {keys}"
            )

    @property
    def linked_key(self) -> str | None:
        """The key of the column that carries the links: `link_column`, or the first."""
        if self.link_column is not None:
            return self.link_column
        return self.columns[0].key if self.columns else None


@dataclass(frozen=True)
class Report[P: BaseModel]:
    """One entry in the catalog, generic in its own parameters model.

    `params` is a pydantic model *class*, not an instance -- the catalog
    reads its fields (each field's `title` becomes the label a parameter
    form shows) to describe what the report takes; `run` is called with an
    instance of it. A report with no parameters uses an empty model.

    Generic in `P`, the report's own params subclass, so `run(db, params)`
    is typed on that subclass rather than the widened `BaseModel` --
    otherwise every report's `run` would need a `cast` or `type: ignore`
    where it is assigned here, since a function taking only
    `HoldingsParams` cannot soundly satisfy a slot declared to take any
    `BaseModel`.
    """

    id: str
    group: str
    title: str
    purpose: str
    params: type[P]
    run: Callable[[Session, P], ReportResult]


__all__ = ["Column", "ColumnKind", "Report", "ReportResult"]
