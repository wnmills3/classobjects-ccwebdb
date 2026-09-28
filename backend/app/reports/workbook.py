r"""A report's result as an `.xlsx` workbook, read by both the API and the CLI.

One sheet, named from the report's title (Excel's own limits: at most 31
characters, none of ``[]:*?/\\``). Above the table: the title, one row per
parameter (its label and the value the report actually ran with), a "Run at"
row, a blank row, then the header row, the data rows, the totals row directly below when
there is one (bold), a blank row, then each note on its own row -- the
layout `docs/specs/reporting-design.md`'s "API" section lays out. Money and
percent cells are written as numbers, never as text, so a spreadsheet can
still sum them; a column's ``None`` is an empty cell, `workbook_backup`'s own
convention for NULL.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import Cell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from pydantic import BaseModel

from .base import Column, ColumnKind, Report, ReportResult

__all__ = ["workbook_filename", "write_workbook"]

#: Characters Excel refuses in a sheet name.
_ILLEGAL_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
_MAX_SHEET_NAME = 31

_MONEY_FORMAT = "#,##0.00"
_PERCENT_FORMAT = "0.0"
_DATE_FORMAT = "yyyy-mm-dd"
_OUNCES_FORMAT = "#,##0.000"

#: A column's width, from its label and its longest cell, kept inside these.
_MIN_WIDTH = 8.0
_MAX_WIDTH = 40.0
_WIDTH_MARGIN = 2.0

#: What this module ever hands openpyxl's `Cell.value` setter -- a strict
#: subset of the type it actually accepts (`bool | float | Decimal | str |
#: CellRichText | datetime | date | time | timedelta | DataTableFormula |
#: ArrayFormula | None | bytes`), narrow enough that mypy can check every
#: assignment below rather than trusting an `object` past this module's edge.
_CellValue = float | Decimal | str | date | datetime | None


def _sheet_name(title: str) -> str:
    """`title`, Excel's illegal characters stripped and cut to 31 characters."""
    cleaned = _ILLEGAL_SHEET_CHARS.sub("", title).strip()
    return (cleaned or "Report")[:_MAX_SHEET_NAME]


def _numeric(kind: str, value: object) -> float:
    """`value` as the float a percent, count or ounces cell holds.

    A `Decimal` converts exactly enough for this schema's scale -- at most 12
    digits, well inside a double's 15, the same reasoning
    `workbook_backup.to_cell` uses for the same conversion -- and a plain
    `int` (every count column) converts the same way: openpyxl's own
    `Cell.value` accepts no plain `int` at all (its stub union,
    `openpyxl.cell._CellGetValue`, has no `int` member, only `bool`, `float`
    and `Decimal`), so a count is written as this float conversion rather
    than as the `int` it actually is.
    """
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        raise TypeError(f"expected a number for a {kind!r} cell, got {value!r}")
    return float(value)


def _money(value: object) -> Decimal:
    """`value` as the `Decimal` a money cell holds.

    Written as the `Decimal` itself, not converted to `float`: openpyxl's own
    `Cell.value` accepts a `Decimal` directly (it is in the stub union
    `_CellGetValue` alongside `float`), so a money cell keeps its exact
    value rather than paying for a conversion that exists only to work
    around a limitation this column's own type does not have.
    """
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise TypeError(f"expected a Decimal for a 'money' cell, got {value!r}")
    return value


def _cell_value(kind: ColumnKind, value: object) -> _CellValue:
    """The wire value of one column's cell, as `Cell.value` may hold it."""
    if value is None:
        return None
    if kind == "money":
        return _money(value)
    if kind in ("percent", "count", "ounces"):
        return _numeric(kind, value)
    if kind == "date":
        if not isinstance(value, date):
            raise TypeError(f"expected a date for a 'date' cell, got {value!r}")
        return value
    if not isinstance(value, str):
        raise TypeError(f"expected text for a {kind!r} cell, got {value!r}")
    return value


def _display(value: object) -> str:
    """How `value` prints, for sizing a column's width -- never for a cell."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


def _write_cell(
    sheet: Worksheet,
    row: int,
    col: int,
    kind: ColumnKind,
    value: object,
    *,
    bold: bool = False,
) -> Cell:
    """One data or totals cell, formatted by its column's `kind`."""
    cell = sheet.cell(row=row, column=col, value=_cell_value(kind, value))
    if kind == "money":
        cell.number_format = _MONEY_FORMAT
    elif kind == "percent":
        cell.number_format = _PERCENT_FORMAT
    elif kind == "date":
        cell.number_format = _DATE_FORMAT
    elif kind == "ounces":
        cell.number_format = _OUNCES_FORMAT
    if bold:
        cell.font = Font(bold=True)
    return cell


def _param_cell_value(value: object) -> float | str:
    """One parameter's own resolved value, as its workbook cell holds it.

    Every report parameter today is an `int` or a `Literal` string; an `int`
    is written as `Cell.value` requires -- as a float, the same conversion
    `_numeric` uses -- and a string is written as it is.
    """
    if isinstance(value, bool):
        raise TypeError(f"a bool is not a report parameter value: {value!r}")
    if isinstance(value, int):
        return float(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"unexpected parameter value: {value!r}")


def _column_width(label: str, values: list[object]) -> float:
    """A reasonable width for one column, from its label and its longest cell."""
    longest = max([len(label), *(len(_display(v)) for v in values)])
    return min(max(longest + _WIDTH_MARGIN, _MIN_WIDTH), _MAX_WIDTH)


def _size_columns(
    sheet: Worksheet, columns: list[Column], result: ReportResult
) -> None:
    """Size every data column from its label and its longest cell."""
    for col, column in enumerate(columns, start=1):
        values: list[object] = [r.get(column.key) for r in result.rows]
        if result.totals is not None:
            values.append(result.totals.get(column.key))
        sheet.column_dimensions[get_column_letter(col)].width = _column_width(
            column.label, values
        )


def write_workbook(
    report: Report[Any],
    params: BaseModel,
    result: ReportResult,
    run_at: datetime,
) -> Workbook:
    """`result`, as a workbook: one sheet named from `report`'s own title."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None  # `Workbook()` always creates one active sheet.
    sheet.title = _sheet_name(report.title)

    row = 1
    sheet.cell(row=row, column=1, value=report.title)
    row += 1

    for name, field in report.params.model_fields.items():
        label = field.title or name
        value = getattr(params, name)
        sheet.cell(row=row, column=1, value=label)
        sheet.cell(row=row, column=2, value=_param_cell_value(value))
        row += 1

    sheet.cell(row=row, column=1, value="Run at")
    # A timestamp is written as ISO text, not a cell of its own: openpyxl
    # refuses a timezone-aware `datetime` outright, and `run_at` always
    # carries one -- the same reason `workbook_backup`'s own export writes
    # timestamps as text.
    sheet.cell(row=row, column=2, value=run_at.isoformat())
    row += 1

    row += 1  # blank row, separating the parameters from the table

    header_row = row
    for col, column in enumerate(result.columns, start=1):
        sheet.cell(row=header_row, column=col, value=column.label)
    row += 1

    for data_row in result.rows:
        for col, column in enumerate(result.columns, start=1):
            _write_cell(sheet, row, col, column.kind, data_row.get(column.key))
        row += 1

    if result.totals is not None:
        for col, column in enumerate(result.columns, start=1):
            _write_cell(
                sheet,
                row,
                col,
                column.kind,
                result.totals.get(column.key),
                bold=True,
            )
        row += 1

    if result.notes:
        row += 1  # blank row, separating the table from the notes
        for note in result.notes:
            sheet.cell(row=row, column=1, value=note)
            row += 1

    # Everything above and including the header stays visible while the data
    # rows scroll -- "below the header" means the freeze boundary sits on the
    # first data row.
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1).coordinate

    _size_columns(sheet, result.columns, result)

    return book


def workbook_filename(report_id: str, run_at: datetime) -> str:
    """`<id>_<YYYY-MM-DD>.xlsx`, dated by `run_at` -- the run date, not today's."""
    return f"{report_id}_{run_at:%Y-%m-%d}.xlsx"
