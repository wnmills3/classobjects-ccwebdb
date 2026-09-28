"""The report catalog's and result's JSON shape, as the HTTP API sends them.

`routers/reports.py` sends both. The command line (`app.reports.__main__`)
uses neither: it reads `REPORTS` directly and prints with its own plain-text
formatter. The wire values follow the
whole application's own convention: a `Decimal` is sent as the string it
prints (`"12.50"`), never a float; a `date` is its ISO text; everything
else -- `int`, `str`, `None` -- crosses unchanged, so an `int` column stays
a JSON number and a total is exact string arithmetic on the client, not a
float that could drift.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from .base import Report, ReportResult
from .registry import REPORTS

__all__ = ["catalog", "serialize_result"]


def _wire_value(value: object) -> object:
    """One value as the wire encodes it: `Decimal` and `date` become text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _wire_row(row: dict[str, object]) -> dict[str, object]:
    """A `rows`/`totals` entry, each of its values converted for the wire."""
    return {key: _wire_value(value) for key, value in row.items()}


def _param_type(field: FieldInfo) -> tuple[str, list[object] | None]:
    """A parameter's catalog `type`, and its `choices` when it is a `Literal`.

    A `Literal` annotation (every status/disposition parameter today) is
    `"choice"`, with `choices` the literal's own values; a plain `int` is
    `"integer"`; anything else is `"text"`. Read from the annotation, never
    guessed from the default, so a `Literal[int, ...]` parameter (none
    exists today) would still be caught as `"choice"` rather than
    misread as `"integer"`.
    """
    origin = get_origin(field.annotation)
    if origin is Literal:
        return "choice", list(get_args(field.annotation))
    if field.annotation is int:
        return "integer", None
    return "text", None


def catalog() -> list[dict[str, object]]:
    """The catalog: every registered report, in registry order.

    Each entry carries its parameters' name, label (the pydantic field's own
    `title`), type, default and choices -- everything a parameter form (the
    console, or the CLI's `--help`) needs, without importing a report
    module's params class directly.
    """
    entries: list[dict[str, object]] = []
    for report in REPORTS.values():
        params: list[dict[str, object]] = []
        for name, field in report.params.model_fields.items():
            kind, choices = _param_type(field)
            default = None if field.default is PydanticUndefined else field.default
            params.append(
                {
                    "name": name,
                    "label": field.title or name,
                    "type": kind,
                    "default": _wire_value(default),
                    "choices": choices,
                }
            )
        entries.append(
            {
                "id": report.id,
                "group": report.group,
                "title": report.title,
                "purpose": report.purpose,
                "params": params,
            }
        )
    return entries


def serialize_result(
    report: Report[Any],
    params: BaseModel,
    result: ReportResult,
    run_at: datetime,
) -> dict[str, object]:
    """A report's result, exactly as the API sends it.

    `link_column` is always a column key (or `None` for a result with no
    columns): the result's own `link_column`, or its first column's key.
    """
    return {
        "id": report.id,
        "group": report.group,
        "title": report.title,
        "params": {
            name: _wire_value(value) for name, value in params.model_dump().items()
        },
        "run_at": run_at.isoformat(),
        "columns": [
            {"key": column.key, "label": column.label, "kind": column.kind}
            for column in result.columns
        ],
        "rows": [_wire_row(row) for row in result.rows],
        "totals": _wire_row(result.totals) if result.totals is not None else None,
        "drills": result.drills,
        "link_column": result.linked_key,
        "notes": result.notes,
    }
