"""Other names for classifier rows: reading, matching and editing them.

docs/specs/item-attributes-design.md, section 1. The standard term is the
label; what people actually write -- UCAM, Legal Tender, No God, Mercury --
is an alias. Two tables hold them: `series_alias`, which predates the general
one and keeps its own shape, and `reference_alias` for every other
vocabulary. This module hides that split from its callers.

**A removed alias stays removed.** Seed loads only add aliases, so deleting a
shipped one would bring it back on the next load. A seeded alias is retired
(`is_active` false) instead; one an administrator added is deleted outright.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import ColumnElement, SQLColumnExpression, func, or_, select
from sqlalchemy.orm import Session

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
    "ids_named",
    "remove_alias",
    "resolve",
]

#: The longest alias either table stores.
MAX_ALIAS = 64


class AliasError(ValueError):
    """An alias that cannot be added or removed, with the reason."""


def _normalise(text: str) -> str:
    """Case and runs of spaces do not make a different name."""
    return " ".join(text.split())


def aliases_by_row(
    db: Session, model: type[ReferenceMixin], *, include_retired: bool = False
) -> dict[int, list[str]]:
    """Every row's aliases, in alphabetical order, keyed by row id."""
    if model is Series:
        stmt = select(SeriesAlias.series_id, SeriesAlias.alias)
        if not include_retired:
            stmt = stmt.where(SeriesAlias.is_active.is_(True))
    else:
        stmt = select(ReferenceAlias.row_id, ReferenceAlias.alias).where(
            ReferenceAlias.table_name == model.__tablename__
        )
        if not include_retired:
            stmt = stmt.where(ReferenceAlias.is_active.is_(True))
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
    text = _normalise(query or "")
    if not text:
        return []
    whole = len(text) < SUBSTRING_FROM

    def named(column: SQLColumnExpression[str]) -> ColumnElement[bool]:
        if whole:
            return func.lower(column) == text.lower()
        return column.ilike(f"%{text}%")

    by_name = select(model.id).where(or_(named(model.label), named(model.code)))
    if model is Series:
        by_alias = select(SeriesAlias.series_id).where(
            SeriesAlias.is_active.is_(True), named(SeriesAlias.alias)
        )
    else:
        by_alias = select(ReferenceAlias.row_id).where(
            ReferenceAlias.table_name == model.__tablename__,
            ReferenceAlias.is_active.is_(True),
            named(ReferenceAlias.alias),
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
    text = _normalise(word or "")
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
    if model is Series:
        stmt = select(SeriesAlias.series_id).where(
            SeriesAlias.is_active.is_(True),
            func.lower(SeriesAlias.alias) == text.lower(),
        )
    else:
        stmt = select(ReferenceAlias.row_id).where(
            ReferenceAlias.table_name == model.__tablename__,
            ReferenceAlias.is_active.is_(True),
            func.lower(ReferenceAlias.alias) == text.lower(),
        )
    ids = set(db.scalars(stmt))
    if len(ids) == 1:
        return Resolved(ids.pop(), "alias")
    return None


def _existing(
    db: Session, model: type[ReferenceMixin], row_id: int, alias: str
) -> SeriesAlias | ReferenceAlias | None:
    if model is Series:
        return db.scalar(
            select(SeriesAlias).where(
                SeriesAlias.series_id == row_id,
                func.lower(SeriesAlias.alias) == alias.lower(),
            )
        )
    return db.scalar(
        select(ReferenceAlias).where(
            ReferenceAlias.table_name == model.__tablename__,
            ReferenceAlias.row_id == row_id,
            func.lower(ReferenceAlias.alias) == alias.lower(),
        )
    )


def add_alias(
    db: Session, model: type[ReferenceMixin], row_id: int, alias: str
) -> SeriesAlias | ReferenceAlias:
    """Give a row another name; bring back a retired one rather than copy it.

    Two rows may share an alias -- "Cartwheel" is any large silver dollar,
    "National Currency" two note classes. Search finds both; `resolve`, and
    so the importer, finds neither rather than guess.

    Refused when the name is empty, too long, or already a row's own label
    or code: `resolve` tries those first, so as another row's alias it could
    never be reached, and as this row's it would add nothing. Also when
    `row_id` names no row, and when the text is already an alias of a
    *different* row -- five `AliasError` reasons in all, which matters
    because `reference_merge.merge` catches `AliasError` broadly and would
    swallow any of them.
    """
    text = _normalise(alias)
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
    created: SeriesAlias | ReferenceAlias
    if model is Series:
        created = SeriesAlias(
            series_id=row_id, alias=text, source=ProvenanceSource.manual
        )
    else:
        created = ReferenceAlias(
            table_name=model.__tablename__,
            row_id=row_id,
            alias=text,
            source=ProvenanceSource.manual,
        )
    db.add(created)
    db.flush()
    return created


def remove_alias(
    db: Session, model: type[ReferenceMixin], row_id: int, alias: str
) -> None:
    """Take a name away: retire a shipped one, delete one added here."""
    existing = _existing(db, model, row_id, _normalise(alias))
    if existing is None or not existing.is_active:
        raise AliasError(f"{alias!r} is not an alias of this value.")
    if existing.source == ProvenanceSource.manual:
        db.delete(existing)
    else:
        existing.is_active = False
    db.flush()
