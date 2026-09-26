"""Other names for classifier rows: reading, matching and editing them.

docs/specs/item-attributes-design.md, section 1. The standard term is the
label; what people actually write -- Ultra Cameo, Legal Tender, No God, Mercury --
is an alias. Two tables hold them: `series_alias`, which predates the general
one and keeps its own shape, and `reference_alias` for every other
vocabulary. This module hides that split from its callers.

**A removed alias stays removed.** Seed loads only add aliases, so deleting a
shipped one would bring it back on the next load. A seeded alias is retired
(`is_active` false) instead; one an administrator added is deleted outright.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import cast

from sqlalchemy import ColumnElement, SQLColumnExpression, delete, func, or_, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from .models import (
    ProvenanceSource,
    ReferenceAlias,
    ReferenceMixin,
    Series,
    SeriesAlias,
)

__all__ = [
    "AliasError",
    "add_alias",
    "aliases_by_row",
    "delete_all",
    "ids_named",
    "normalise",
    "remove_alias",
    "resolve",
    "word_pattern",
]

#: The longest alias either table stores.
MAX_ALIAS = 64


class AliasError(ValueError):
    """An alias that cannot be added or removed, with the reason."""


def normalise(text: str) -> str:
    """A name with its runs of whitespace collapsed and its ends trimmed.

    Case is kept: every comparison of names here is case-insensitive
    (`func.lower` against `str.lower`), so case never makes a different name
    either, and the alias is stored as it was written.
    """
    return " ".join(text.split())


@dataclass(frozen=True)
class _AliasTable:
    """Where one vocabulary's aliases live: the table and its columns.

    The one place the `series_alias` / `reference_alias` split is decided;
    every function below reads the columns from here.
    """

    model: type[SeriesAlias] | type[ReferenceAlias]
    row_id: InstrumentedAttribute[int]
    alias: InstrumentedAttribute[str]
    is_active: InstrumentedAttribute[bool]
    #: `reference_alias.table_name == <vocabulary>`; none for `series_alias`.
    scope: tuple[ColumnElement[bool], ...]
    table_name: str | None

    def new(self, row_id: int, alias: str) -> SeriesAlias | ReferenceAlias:
        """An alias an administrator added, not yet in the session."""
        if self.table_name is None:
            return SeriesAlias(
                series_id=row_id, alias=alias, source=ProvenanceSource.manual
            )
        return ReferenceAlias(
            table_name=self.table_name,
            row_id=row_id,
            alias=alias,
            source=ProvenanceSource.manual,
        )


def _alias_table(model: type[ReferenceMixin]) -> _AliasTable:
    """The alias table for `model`'s vocabulary."""
    if model is Series:
        return _AliasTable(
            SeriesAlias,
            SeriesAlias.series_id,
            SeriesAlias.alias,
            SeriesAlias.is_active,
            (),
            None,
        )
    table = model.__tablename__
    return _AliasTable(
        ReferenceAlias,
        ReferenceAlias.row_id,
        ReferenceAlias.alias,
        ReferenceAlias.is_active,
        (ReferenceAlias.table_name == table,),
        table,
    )


def word_pattern(name: str) -> re.Pattern[str]:
    """A name as a whole word or phrase anywhere in free text, ignoring case.

    How a label or alias is looked for in a description or a rating: "Mercury"
    matches "a Mercury dime" but not "Mercurys". The name is escaped, so its
    punctuation is literal.
    """
    return re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)


def aliases_by_row(
    db: Session, model: type[ReferenceMixin], *, include_retired: bool = False
) -> dict[int, list[str]]:
    """Every row's aliases, in alphabetical order, keyed by row id."""
    held = _alias_table(model)
    stmt = select(held.row_id, held.alias).where(*held.scope)
    if not include_retired:
        stmt = stmt.where(held.is_active.is_(True))
    found: dict[int, list[str]] = defaultdict(list)
    for row_id, alias in db.execute(stmt).all():
        found[row_id].append(alias)
    return {row_id: sorted(names, key=str.lower) for row_id, names in found.items()}


#: Search text shorter than this must be a whole name, not part of one.
SUBSTRING_FROM = 3


def ids_named(db: Session, model: type[ReferenceMixin], query: str | None) -> list[int]:
    """Rows whose label, code or alias contains the search text.

    Text shorter than SUBSTRING_FROM must match a name whole, ignoring case:
    "PR" is the Proof strike and "D" the Denver mint, but as substrings "s"
    would name the strike aliased "MS", and with it most of the collection.
    """
    text = normalise(query or "")
    if not text:
        return []
    whole = len(text) < SUBSTRING_FROM

    def named(column: SQLColumnExpression[str]) -> ColumnElement[bool]:
        if whole:
            return func.lower(column) == text.lower()
        return column.ilike(f"%{text}%")

    by_name = select(model.id).where(or_(named(model.label), named(model.code)))
    held = _alias_table(model)
    by_alias = select(held.row_id).where(
        *held.scope, held.is_active.is_(True), named(held.alias)
    )
    return sorted(set(db.scalars(by_name.union(by_alias))))


@dataclass(frozen=True)
class Resolved:
    """A row found for a word, and how: by ``code``, ``label`` or ``alias``."""

    row_id: int
    by: str


def resolve(db: Session, model: type[ReferenceMixin], word: str) -> Resolved | None:
    """The row a word names, exactly: its code, then its label, then an alias.

    An exact code first; after that, case and extra spaces are ignored. An
    alias shared by two rows names neither -- a guess between them would be
    silent -- so it resolves to None.
    """
    text = normalise(word or "")
    if not text:
        return None
    found = db.scalar(select(model.id).where(model.code == text))
    if found is not None:
        return Resolved(found, "code")
    for column, by in ((model.code, "code"), (model.label, "label")):
        matched = db.scalars(
            select(model.id).where(func.lower(column) == text.lower())
        ).all()
        if len(matched) == 1:
            return Resolved(matched[0], by)
    held = _alias_table(model)
    stmt = select(held.row_id).where(
        *held.scope,
        held.is_active.is_(True),
        func.lower(held.alias) == text.lower(),
    )
    ids = set(db.scalars(stmt))
    if len(ids) == 1:
        return Resolved(ids.pop(), "alias")
    return None


def _existing(
    db: Session, model: type[ReferenceMixin], row_id: int, alias: str
) -> SeriesAlias | ReferenceAlias | None:
    held = _alias_table(model)
    # `select` of a union of two entities types its row as their common base;
    # the entity is `held.model`, one of the two named here.
    return cast(
        "SeriesAlias | ReferenceAlias | None",
        db.scalar(
            select(held.model).where(
                *held.scope,
                held.row_id == row_id,
                func.lower(held.alias) == alias.lower(),
            )
        ),
    )


def add_alias(
    db: Session, model: type[ReferenceMixin], row_id: int, alias: str
) -> SeriesAlias | ReferenceAlias:
    """Give a row another name; bring back a retired one rather than copy it.

    Two rows may share an alias -- "Cartwheel" is any large silver dollar,
    "National Currency" two note classes. Search finds both; `resolve` finds
    neither rather than guess.

    Refused when the name is empty, too long, or already a row's own label
    or code: `resolve` tries those first, so as another row's alias it could
    never be reached, and as this row's it would add nothing. Also when
    `row_id` names no row, and when the text is already an alias of a
    *different* row -- five `AliasError` reasons in all, which matters
    because `reference_merge.merge` catches `AliasError` broadly and would
    swallow any of them.
    """
    text = normalise(alias)
    if not text:
        raise AliasError("An alias needs some text.")
    if len(text) > MAX_ALIAS:
        raise AliasError(f"An alias is at most {MAX_ALIAS} characters.")
    row = db.get(model, row_id)
    if row is None:
        raise AliasError("No such value.")
    if text.lower() in {row.label.lower(), row.code.lower()}:
        raise AliasError(f"{text!r} is already this value's own name.")
    named = db.scalar(
        select(model.label).where(
            or_(
                func.lower(model.label) == text.lower(),
                func.lower(model.code) == text.lower(),
            )
        )
    )
    if named is not None:
        raise AliasError(f"{text!r} is already the name of {named}.")

    existing = _existing(db, model, row_id, text)
    if existing is not None:
        if existing.is_active:
            raise AliasError(f"{text!r} is already an alias of {row.label}.")
        existing.is_active = True
        db.flush()
        return existing
    created = _alias_table(model).new(row_id, text)
    db.add(created)
    db.flush()
    return created


def delete_all(db: Session, model: type[ReferenceMixin], row_id: int) -> None:
    """Delete every alias of one row, retired ones included.

    For a row that is itself going: `reference_merge.merge` deletes the
    merged-away value, and its aliases would otherwise name nothing.
    """
    held = _alias_table(model)
    db.execute(delete(held.model).where(*held.scope, held.row_id == row_id))


def remove_alias(
    db: Session, model: type[ReferenceMixin], row_id: int, alias: str
) -> None:
    """Take a name away: retire a shipped one, delete one added here."""
    existing = _existing(db, model, row_id, normalise(alias))
    if existing is None or not existing.is_active:
        raise AliasError(f"{alias!r} is not an alias of this value.")
    if existing.source == ProvenanceSource.manual:
        db.delete(existing)
    else:
        existing.is_active = False
    db.flush()
