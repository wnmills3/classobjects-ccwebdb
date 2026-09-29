"""The shapes every report is built from: a column, a result, and a report.

A report group module (`data_quality.py`, `collection.py`, ...) builds these
and registers a `Report`; nothing else in the backend -- the API, the CLI,
the console -- reads anything but these shapes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import ColumnElement, Date, cast, func, literal
from sqlalchemy.orm import QueryableAttribute, Session

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


class DateRange(BaseModel):
    """A from/to date filter, subclassed by any report scoped to a range.

    `date_from` and `date_to` are each optional and independent -- either,
    both or neither may be set. An absent bound means no limit on that
    side, not today's date or any other stand-in default: a report reading
    `params.date_from is None` skips that half of its own `WHERE` clause
    rather than binding a sentinel date. `date_from` after `date_to` would
    describe an empty range, so it is refused here, once, rather than left
    for every subclass's own query to handle -- or fail to.
    """

    date_from: date | None = Field(default=None, title="From")
    date_to: date | None = Field(default=None, title="To")

    @model_validator(mode="after")
    def _check_range(self) -> DateRange:
        """Refuse `date_from` later than `date_to`; equal bounds are fine."""
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise ValueError("From must not be after To")
        return self


#: The three ways a report may bucket a date column into a time period.
#: `pr_spend` and (Ruling P2-2) `mn_tax` share this one Literal, one
#: `period_start` and one `period_label`, rather than each writing its own
#: `date_trunc` and its own text for "2026 Q3" -- so the two reports can
#: never drift apart on what a period is or how it reads.
Period = Literal["month", "quarter", "year"]


def period_start(
    period: Period, column: ColumnElement[Any] | QueryableAttribute[Any]
) -> ColumnElement[date]:
    """The first day of `column`'s bucket for `period`: `date_trunc`, cast to a date.

    `period` only ever reaches here as one of the three `Period` values
    above -- a pydantic `Literal` field has already refused anything else
    before a report's `run` is called -- but it is still passed through
    `literal()` as a bound parameter, not interpolated into the SQL text,
    so this function carries its own guarantee rather than resting entirely
    on the caller's.
    """
    return cast(func.date_trunc(literal(period), column), Date)


def period_label(period: Period, start: date) -> str:
    """`start`'s own label: `"2026-09"` for a month, `"2026 Q3"`, or `"2026"`."""
    if period == "month":
        return f"{start.year:04d}-{start.month:02d}"
    if period == "quarter":
        quarter = (start.month - 1) // 3 + 1
        return f"{start.year} Q{quarter}"
    return str(start.year)


def local_date(moment: datetime) -> date:
    """`moment`'s calendar date in the system's own local time zone.

    Shared by any report reading a `timestamptz` column that must be
    compared, as a calendar day, against a local date such as
    `date.today()` or a `DateRange` bound: the driver hands a `timestamptz`
    back tagged with whatever zone the *database session* is in, which need
    not be the zone the application server itself runs in.
    `astimezone()` with no argument converts to the local zone first -- the
    same zone `date.today()` reads from -- so the two always agree about
    which calendar day a moment falls on regardless of the session's own
    zone. Pinned by `test_reports_selling.py`'s
    `test_local_date_uses_the_local_zone_not_the_session_zone`, which forces
    the session to a different zone than the system's own.
    """
    return moment.astimezone().date()


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


__all__ = [
    "Column",
    "ColumnKind",
    "DateRange",
    "Period",
    "Report",
    "ReportResult",
    "local_date",
    "period_label",
    "period_start",
]
