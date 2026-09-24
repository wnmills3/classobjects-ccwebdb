"""Merge one vocabulary value into another, and remove it.

Cleaning up a vocabulary usually means two values that say the same thing:
"Legal Tender Note" and "United States Note". Retiring the old one only
stops it being offered; merging moves everything that holds it to the value
kept, and deletes it:

- **Items move.** Every record that describes an item -- the item, its coin
  or note detail, attributes, errors, certificates and image roles -- is
  changed to the kept value, and each item's version moves, so a form
  opened before the merge cannot put the old value back. An item that
  already has the kept value in a link table keeps that link; the old one is
  dropped.
- **Anything else refuses.** A value used by another vocabulary or a facts
  table (a denomination's currency, a series' note class, a note issue, an
  order's status) is left alone and the merge is refused, naming the uses:
  those rows have rules of their own that a blind remap could break.
- **The old names stay findable.** The old label, code and aliases become
  aliases of the kept value, so a rating or a search that uses the old word
  still finds it.
- **It stays merged.** `reference_merge` records it; a seed load skips the
  old code and reads a seed row naming it as naming the kept one.

A value the application looks up by its code cannot be merged away, for the
reason it cannot be retired (`routers.reference.retirable`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Column, FromClause, delete, func, select, update
from sqlalchemy.orm import Session

from . import aliases, sale_state
from .models import (
    Base,
    InventoryItem,
    ReferenceAlias,
    ReferenceMerge,
    ReferenceMixin,
    Series,
    SeriesAlias,
)

__all__ = ["MergeError", "MergePlan", "NoSuchValue", "merge", "plan"]

#: Tables that describe an item, and so move with a merge. Each maps to the
#: column naming the item, so the item's version can be moved too.
RECORD_TABLES: dict[str, str] = {
    "inventory_item": "id",
    "coin_detail": "inventory_item_id",
    "currency_detail": "inventory_item_id",
    "item_attribute_link": "inventory_item_id",
    "item_error": "inventory_item_id",
    "item_certification": "inventory_item_id",
    "item_image": "inventory_item_id",
}

#: Record tables where an item holds a value at most once: a merge that
#: would give an item the kept value twice drops the old row instead.
_ONCE_PER_ITEM = frozenset({"item_attribute_link", "item_error"})

#: Tables that belong to the value itself and go with it.
_OWNED = frozenset({"series_alias"})


class MergeError(ValueError):
    """A merge that cannot be made, with the reason."""


class NoSuchValue(MergeError):
    """One of the two values does not exist."""


@dataclass
class MergePlan:
    """What a merge moves, before or after it is made."""

    table: str
    code: str
    into: str
    #: Rows moved, by "table.column".
    moved: dict[str, int] = field(default_factory=dict)
    #: Distinct items those rows describe.
    items: int = 0
    #: Rows dropped because the item already had the kept value.
    dropped: int = 0
    #: Names the kept value gains.
    aliases: list[str] = field(default_factory=list)
    #: Item codes among `items` that are for sale, at most ten of them.
    for_sale: list[str] = field(default_factory=list)
    #: How many are for sale in total, however many are named above.
    for_sale_count: int = 0


def _references(table: FromClause) -> list[Column]:
    """Every column, anywhere, holding a key of this table."""
    return [
        fk.parent
        for other in Base.metadata.sorted_tables
        for fk in other.foreign_keys
        if fk.column.table is table
    ]


def _row(db: Session, model: type[ReferenceMixin], code: str) -> ReferenceMixin:
    row = db.scalar(select(model).where(model.code == code))
    if row is None:
        raise NoSuchValue(f"{model.__tablename__} has no value {code!r}.")
    return row


def _new_names(
    db: Session, model: type[ReferenceMixin], source: ReferenceMixin
) -> list[str]:
    names = [source.label, source.code]
    names += aliases.aliases_by_row(db, model).get(source.id, [])
    return list(dict.fromkeys(" ".join(n.split()) for n in names if n.strip()))


def plan(
    db: Session, model: type[ReferenceMixin], code: str, into: str
) -> tuple[MergePlan, ReferenceMixin, ReferenceMixin]:
    """What merging `code` into `into` would do; refuses what it cannot."""
    from .routers.reference import retirable

    table = model.__tablename__
    if code == into:
        raise MergeError("A value cannot be merged into itself.")
    source = _row(db, model, code)
    target = _row(db, model, into)
    if not retirable(table, code):
        raise MergeError(
            f"{source.label} cannot be merged away: the application looks it up "
            "by its code."
        )
    if not target.is_active:
        raise MergeError(f"{target.label} is retired; restore it first.")

    result = MergePlan(table, code, into)
    others: list[str] = []
    items: set[int] = set()
    for column in _references(model.__table__):
        owner = column.table.name
        if owner in _OWNED:
            continue
        count = db.scalar(
            select(func.count()).select_from(column.table).where(column == source.id)
        )
        if not count:
            continue
        if owner not in RECORD_TABLES:
            others.append(f"{owner}.{column.name} ({count})")
            continue
        result.moved[f"{owner}.{column.name}"] = count
        item_column = column.table.c[RECORD_TABLES[owner]]
        items.update(db.scalars(select(item_column).where(column == source.id)))
        if owner in _ONCE_PER_ITEM:
            # Counted here as well as in `merge`, because the dry run calls
            # only this function. Without it the preview promised to move N
            # rows and always reported 0 dropped, then the real merge dropped
            # some -- the one number in the preview that could not be
            # believed. Same predicate as `merge`, which deletes them.
            has_target = select(item_column).where(column == target.id)
            result.dropped += (
                db.scalar(
                    select(func.count())
                    .select_from(column.table)
                    .where((column == source.id) & item_column.in_(has_target))
                )
                or 0
            )
    if others:
        raise MergeError(
            f"{source.label} is also used by {', '.join(others)}; "
            "change those first, or retire it instead."
        )
    result.items = len(items)
    # `items` is discarded below; the codes that are for sale are what the
    # console has to show before anyone confirms a merge.
    for_sale = sale_state.for_sale(db, items)
    if for_sale:
        codes = db.scalars(
            select(InventoryItem.item_code)
            .where(InventoryItem.id.in_(list(for_sale)))
            .order_by(InventoryItem.item_code)
        ).all()
        result.for_sale_count = len(codes)
        result.for_sale = list(codes[:10])
    result.aliases = [
        name
        for name in _new_names(db, model, source)
        if name.lower() not in {target.label.lower(), target.code.lower()}
    ]
    return result, source, target


def merge(
    db: Session,
    model: type[ReferenceMixin],
    code: str,
    into: str,
    *,
    user_id: int | None,
) -> MergePlan:
    """Move everything from `code` to `into`, delete `code`, remember it."""
    result, source, target = plan(db, model, code, into)
    table = model.__tablename__
    items: set[int] = set()

    for column in _references(model.__table__):
        owner = column.table.name
        if owner not in RECORD_TABLES:
            continue
        item_column = column.table.c[RECORD_TABLES[owner]]
        items.update(db.scalars(select(item_column).where(column == source.id)))
        if owner in _ONCE_PER_ITEM:
            # `plan` -- called at the top of this function -- has already
            # counted these into `result.dropped`, so only the deletion
            # happens here. Counting again would double it.
            has_target = select(item_column).where(column == target.id)
            duplicate = (column == source.id) & item_column.in_(has_target)
            db.execute(delete(column.table).where(duplicate))
        db.execute(
            update(column.table).where(column == source.id).values({column: target.id})
        )

    if items:
        # Moves each item's version, so a stale form is refused.
        db.execute(
            update(InventoryItem)
            .where(InventoryItem.id.in_(items))
            .values(version=InventoryItem.version + 1)
        )

    names = _new_names(db, model, source)
    if model is not Series:
        db.execute(
            delete(ReferenceAlias).where(
                ReferenceAlias.table_name == table, ReferenceAlias.row_id == source.id
            )
        )
    else:
        db.execute(delete(SeriesAlias).where(SeriesAlias.series_id == source.id))
    db.add(
        ReferenceMerge(
            table_name=table,
            code=source.code,
            label=source.label,
            merged_into=target.code,
            merged_by_id=user_id,
        )
    )
    db.delete(source)
    db.flush()

    result.aliases = []
    for name in names:
        try:
            aliases.add_alias(db, model, target.id, name)
        except aliases.AliasError:
            continue  # already one of its names, or another value's
        result.aliases.append(name)
    result.items = len(items)
    db.flush()
    return result
