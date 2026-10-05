"""What a vocabulary's own columns are, and reading them from a request.

Every vocabulary has a code, a label and a position. Some carry more -- a
denomination its currency, face value and side; a mint its mark -- and a
value cannot be added without them. This module describes those columns so a
form can ask for each one (`fields_of`), turns what the form sent back into
column values (`column_values`), and gives a value a code when the caller
sent none (`derived_code`).

A column that refers to another vocabulary crosses the API as that value's
code under the column's name less `_id` -- `currency: "USD"` -- since ids are
per-installation.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from sqlalchemy import (
    Boolean,
    Column,
    Enum,
    Integer,
    Numeric,
    String,
    Table,
    select,
)
from sqlalchemy.orm import Session

from .models import REFERENCE_MODELS, ReferenceMixin
from .schemas import ReferenceFieldOut

__all__ = ["FieldError", "column_values", "derived_code", "fields_of", "table_of"]

#: Columns every vocabulary shares; they are not a table's own.
_COMMON = frozenset({"id", "code", "label", "sort_order", "is_active", "source"})

_BY_TABLE: dict[str, type[ReferenceMixin]] = {
    model.__tablename__: model for model in REFERENCE_MODELS
}

#: A text column that takes one of a few words, where the column's type does
#: not say so. A word outside the list would hide the value from every picker.
_CHOICES: dict[tuple[str, str], tuple[str, ...]] = {
    ("series", "applies_to"): ("coin", "currency"),
}


#: Dropped from a label before it becomes a code: straight, grave and curly.
_APOSTROPHES = {ord(mark): None for mark in ("'", "`", chr(0x2019))}


class FieldError(ValueError):
    """What was sent for a vocabulary's column cannot be stored in it."""


def table_of(model: type[ReferenceMixin]) -> Table:
    """A vocabulary's table: the mapper declares it as any selectable."""
    return cast("Table", model.__table__)


def _own_columns(model: type[ReferenceMixin]) -> list[Column[Any]]:
    """The columns a person fills in: not the shared ones, not computed ones."""
    return [
        column
        for column in table_of(model).columns
        if column.name not in _COMMON and column.computed is None
    ]


def _target(column: Column[Any]) -> type[ReferenceMixin] | None:
    """The vocabulary a column refers to, or None when it refers to none."""
    for key in column.foreign_keys:
        return _BY_TABLE.get(key.column.table.name)
    return None


def _name(column: Column[Any]) -> str:
    """The name a column crosses the API under."""
    if _target(column) is not None and column.name.endswith("_id"):
        return column.name[: -len("_id")]
    return column.name


def _required(column: Column[Any]) -> bool:
    return (
        not column.nullable and column.default is None and column.server_default is None
    )


def _choices(table: str, column: Column[Any]) -> list[str]:
    if isinstance(column.type, Enum):
        return list(column.type.enums)
    return list(_CHOICES.get((table, column.name), ()))


def _kind(table: str, column: Column[Any]) -> str:
    if _target(column) is not None:
        return "reference"
    if _choices(table, column):
        return "choice"
    if isinstance(column.type, Boolean):
        return "boolean"
    if isinstance(column.type, Integer):
        return "integer"
    if isinstance(column.type, Numeric):
        return "decimal"
    return "text"


def fields_of(model: type[ReferenceMixin]) -> list[ReferenceFieldOut]:
    """A vocabulary's own columns, as a form asks for them."""
    table = model.__tablename__
    fields = []
    for column in _own_columns(model):
        target = _target(column)
        fields.append(
            ReferenceFieldOut(
                name=_name(column),
                label=_name(column).replace("_", " ").capitalize(),
                kind=_kind(table, column),
                required=_required(column),
                choices=_choices(table, column),
                table=target.__tablename__ if target is not None else None,
                max_length=(
                    column.type.length if isinstance(column.type, String) else None
                ),
            )
        )
    return fields


def _read(
    db: Session, table: str, column: Column[Any], name: str, value: object
) -> object:
    """One column's value from what was sent for it, or a FieldError."""
    target = _target(column)
    if target is not None and name != column.name:
        found = db.scalar(select(target.id).where(target.code == value))
        if found is None:
            raise FieldError(f"{name}: {target.__tablename__} has no value {value!r}")
        return found
    choices = _choices(table, column)
    if choices:
        if value not in choices:
            raise FieldError(f"{name}: {value!r} is not one of {', '.join(choices)}")
        return value
    try:
        if isinstance(column.type, Boolean):
            if not isinstance(value, bool):
                raise FieldError(f"{name}: {value!r} is not true or false")
            return value
        if isinstance(column.type, Integer):
            # A bool is an int to Python; a fraction would be cut short.
            if isinstance(value, bool) or (
                isinstance(value, float) and not value.is_integer()
            ):
                raise FieldError(f"{name}: {value!r} is not a whole number")
            return int(str(value).strip())
        if isinstance(column.type, Numeric):
            number = Decimal(str(value).strip())
            if not number.is_finite():
                raise FieldError(f"{name}: {value!r} is not a number")
            return number
    except (InvalidOperation, ValueError) as exc:
        if isinstance(exc, FieldError):
            raise
        raise FieldError(f"{name}: {value!r} is not a number") from exc
    text = str(value).strip()
    length = getattr(column.type, "length", None)
    if length is not None and len(text) > length:
        raise FieldError(f"{name}: at most {length} characters")
    return text


def column_values(
    db: Session, model: type[ReferenceMixin], extra: dict[str, object]
) -> dict[str, object]:
    """The column values `extra` names, keyed by column.

    Raises `FieldError` for a name the vocabulary has no column for, a value
    its column cannot hold, or a required column left out. A blank is the
    same as leaving the column out.
    """
    table = model.__tablename__
    by_name: dict[str, Column[Any]] = {}
    for column in _own_columns(model):
        by_name[column.name] = column
        by_name[_name(column)] = column

    unknown = sorted(set(extra) - set(by_name))
    if unknown:
        offered = sorted({_name(column) for column in by_name.values()})
        raise FieldError(
            f"{table} has no column(s) {unknown}. Available: {offered or 'none'}"
        )

    values: dict[str, object] = {}
    for name, value in extra.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        column = by_name[name]
        values[column.name] = _read(db, table, column, name, value)

    missing = sorted(
        _name(column)
        for column in _own_columns(model)
        if _required(column) and column.name not in values
    )
    if missing:
        raise FieldError(f"{table} needs {', '.join(missing)}")
    return values


def _slug(text: str) -> str:
    """`Mismatched Serial` -> `mismatched_serial`, as the pickers derive it."""
    bare = text.strip().lower().translate(_APOSTROPHES)
    return re.sub(r"[^a-z0-9]+", "_", bare).strip("_")


def derived_code(
    db: Session, model: type[ReferenceMixin], label: str, values: dict[str, object]
) -> str:
    """The code a value takes when none was sent.

    A denomination's says what it is, as the shipped ones do --
    `usd_coin_0_05`, `usd_note_100` -- so the same face value always has the
    same code. Any other value is named for its label.
    """
    if model.__tablename__ == "denomination":
        currency = _BY_TABLE["currency"]
        unit = db.scalar(
            select(currency.code).where(currency.id == values["currency_id"])
        )
        face = Decimal(str(values["face_value"]))
        kind = str(getattr(values["kind"], "value", values["kind"]))
        # A coin's face value is written to the cent, a note's as it is read.
        amount = f"{face:.2f}" if kind == "coin" else f"{face.normalize():f}"
        return _slug(f"{unit}_{kind}_{amount}")
    return _slug(label)
