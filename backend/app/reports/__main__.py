"""The reports command line, over the same registry the API uses.

    python -m app.reports list
    python -m app.reports run <id> [--param name=value ...] [--workbook FILE]

`list` prints every registered report's id, group and title. `run` executes
one report and prints it as a plain-text table -- title, parameters, header,
rows, totals, notes -- with `--param` repeatable for the report's own
parameters and `--workbook FILE` also writing the same `.xlsx` the API's
`/workbook` endpoint serves. Read-only, like every report, so there is no
`--commit`.

A bad `--param` (an unknown name, or a value the report's params model
rejects) and an unknown report id are both reported on stderr and exit `2`,
the same code argparse itself uses for a malformed command line.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, get_args

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from sqlalchemy.orm import Session

from ..database import SessionLocal
from .base import Column, ColumnKind, Report, ReportResult
from .params import ParamError, resolve_params
from .registry import REPORTS
from .workbook import write_workbook

__all__ = ["main"]

#: Column kinds printed right-aligned, matching how a spreadsheet shows a
#: number; every other kind (text, date) prints left-aligned.
_RIGHT_ALIGN: frozenset[ColumnKind] = frozenset({"money", "count", "percent", "ounces"})


def _display(kind: ColumnKind, value: object) -> str:
    """One cell's plain-text form; a `Decimal` (money, percent) prints as `str` does."""
    if value is None:
        return ""
    if kind == "date" and hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _column_widths(result: ReportResult) -> list[int]:
    """Each column's print width: its label, or its longest cell if wider."""
    widths = [len(column.label) for column in result.columns]
    rows = [*result.rows, *([result.totals] if result.totals is not None else [])]
    for row in rows:
        for index, column in enumerate(result.columns):
            widths[index] = max(
                widths[index], len(_display(column.kind, row.get(column.key)))
            )
    return widths


def _header_line(columns: list[Column], widths: list[int]) -> str:
    """The header row: every column's label, left-aligned to its width."""
    cells = (c.label.ljust(w) for c, w in zip(columns, widths, strict=True))
    return "  ".join(cells).rstrip()


def _data_line(columns: list[Column], widths: list[int], row: dict[str, object]) -> str:
    """One data or totals row, each cell aligned by its column's kind."""
    cells = []
    for column, width in zip(columns, widths, strict=True):
        text = _display(column.kind, row.get(column.key))
        aligned = (
            text.rjust(width) if column.kind in _RIGHT_ALIGN else text.ljust(width)
        )
        cells.append(aligned)
    return "  ".join(cells).rstrip()


def _is_date_field(field: FieldInfo) -> bool:
    """Whether a parameter field is `date` or `date | None`.

    The same test `serialize.py`'s own `_param_type` makes for the catalog's
    `type`, so the CLI and the console agree about which parameters are
    dates without importing one another's private helper.
    """
    return field.annotation is date or date in get_args(field.annotation)


def _params_line(report: Report[Any], params: BaseModel) -> str:
    """Every parameter the report ran with, in words: `"Overdue after (days): 21"`.

    An absent date bound prints as `any`, not `None` -- the same word the
    console's own print heading uses (`reports/values.js`) for a blank
    `date_from`/`date_to`, since a report parameter left unset never means
    the value "None".
    """
    parts = []
    for name, field in report.params.model_fields.items():
        value = getattr(params, name)
        if value is None and _is_date_field(field):
            value = "any"
        parts.append(f"{field.title or name}: {value}")
    return ", ".join(parts)


def format_report(
    report: Report[Any], params: BaseModel, result: ReportResult, run_at: datetime
) -> str:
    """`result`, as the plain-text table `run` prints: title through notes."""
    lines = [report.title]
    params_line = _params_line(report, params)
    if params_line:
        lines.append(params_line)
    lines.append(f"Run at: {run_at.isoformat()}")
    lines.append("")

    widths = _column_widths(result)
    lines.append(_header_line(result.columns, widths))
    lines.extend(_data_line(result.columns, widths, row) for row in result.rows)
    if result.totals is not None:
        lines.append(_data_line(result.columns, widths, result.totals))

    if result.notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  {note}" for note in result.notes)
    return "\n".join(lines)


def _format_catalog() -> str:
    """One line per registered report: id, group and title, aligned in columns."""
    reports = list(REPORTS.values())
    id_width = max((len(r.id) for r in reports), default=0)
    group_width = max((len(r.group) for r in reports), default=0)
    return "\n".join(
        f"{r.id.ljust(id_width)}  {r.group.ljust(group_width)}  {r.title}"
        for r in reports
    )


def _param_values(pairs: list[str]) -> dict[str, str]:
    """`--param name=value` arguments as a dict; a bare name with no `=` is refused."""
    values: dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep:
            raise ParamError(f"malformed --param (want name=value): {pair!r}")
        values[name] = value
    return values


def _run(
    db: Session, report_id: str, param_args: list[str], workbook_path: str | None
) -> int:
    """`run <id>`: validate, execute, print the table, and export if asked."""
    report = REPORTS.get(report_id)
    if report is None:
        valid = ", ".join(sorted(REPORTS))
        print(f"unknown report: {report_id!r} -- valid ids: {valid}", file=sys.stderr)
        return 2
    try:
        params = resolve_params(report, _param_values(param_args))
    except ParamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = report.run(db, params)
    run_at = datetime.now().astimezone()
    print(format_report(report, params, result, run_at))

    if workbook_path is not None:
        book = write_workbook(report, params, result, run_at)
        book.save(workbook_path)
        print(f"\nWrote {workbook_path}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    """The `list`/`run` command line, as the module docstring documents it."""
    parser = argparse.ArgumentParser(prog="python -m app.reports", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="every registered report: id, group, title")

    run_parser = sub.add_parser("run", help="run one report and print its table")
    run_parser.add_argument("id", help="a report id, as `list` shows it")
    run_parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="name=value",
        help="a report parameter; repeatable",
    )
    run_parser.add_argument(
        "--workbook", metavar="FILE", help="also write the report's .xlsx to FILE"
    )
    return parser


def main(argv: Sequence[str] | None = None, *, db: Session | None = None) -> int:
    """`list` the catalog, or `run` one report.

    `db` is the session to run a report against; production (`__main__`
    below) opens the application's own `SessionLocal`. A caller that already
    has a session -- namely the tests, which must never let this open a
    connection to the live database -- passes it instead.
    """
    args = build_parser().parse_args(argv)
    if args.command == "list":
        print(_format_catalog())
        return 0
    if db is not None:
        return _run(db, args.id, args.param, args.workbook)
    with SessionLocal() as session:
        return _run(session, args.id, args.param, args.workbook)


if __name__ == "__main__":
    sys.exit(main())
