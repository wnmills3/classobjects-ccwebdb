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
  dropped, and an error's details that differ are added to the kept error's.
  An attribute the item once had removed is not "already had": the old
  link, in force, replaces the removed one, so the item keeps the
  attribute it holds. Each field moved -- a classifier, the attributes, the errors --
  goes into the item's history under the person who merged
  (`app.field_changes`), and a photograph's role is changed by
  `app.image_links`, the one writer of `item_image`.
- **Anything else refuses.** A value used by another vocabulary or a facts
  table (a denomination's currency, a series' note class, a note issue, an
  order's status) is left alone and the merge is refused, naming the uses:
  those rows have rules of their own that a blind remap could break.
- **The old names stay findable.** The old label, code and aliases become
  aliases of the kept value, so a rating or a search that uses the old word
  still finds it. One that cannot be an alias -- longer than an alias may
  be, or another value's own label or code -- is reported in
  `names_not_kept`, by the preview and by the merge alike.
- **It stays merged.** `reference_merge` records it; a seed load skips the
  old code and reads a seed row naming it as naming the kept one.

A value the application looks up by its code cannot be merged away, for the
reason it cannot be retired (`references.retirable`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Column, FromClause, Select, delete, func, select, update
from sqlalchemy.orm import Session, aliased

from . import aliases, field_changes, image_links, sale_state, serial_patterns
from .models import (
    Base,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ItemError,
    ReferenceMerge,
    ReferenceMixin,
)
from .references import retirable

__all__ = ["MergeError", "MergePlan", "NoSuchTarget", "NoSuchValue", "merge", "plan"]

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

#: Record tables whose column is the item field of the same name less `_id`
#: -- `grade_designation`, `mint`, `seal_color` -- as the item history logs it.
_FIELD_TABLES = frozenset({"inventory_item", "coin_detail", "currency_detail"})


class MergeError(ValueError):
    """A merge that cannot be made, with the reason."""


class NoSuchValue(MergeError):
    """The value being merged away does not exist."""


class NoSuchTarget(MergeError):
    """The value to merge into does not exist."""


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
    #: Rows dropped because the item already had the kept value -- for an
    #: attribute, a link in force; a removed one is replaced, not counted.
    dropped: int = 0
    #: Names the kept value gains.
    aliases: list[str] = field(default_factory=list)
    #: Old names it cannot take, each with the reason.
    names_not_kept: list[str] = field(default_factory=list)
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
    """The vocabulary's value with this code, or `NoSuchValue`."""
    row = db.scalar(select(model).where(model.code == code))
    if row is None:
        raise NoSuchValue(f"{model.__tablename__} has no value {code!r}.")
    return row


def _new_names(
    db: Session, model: type[ReferenceMixin], source: ReferenceMixin
) -> list[str]:
    """Every name the merged value went by: label, code and aliases, once each.

    Once ignoring case, as every comparison of names is: the kept value takes
    the first spelling, and a second would be refused as the name it holds.
    """
    names = [source.label, source.code]
    names += aliases.aliases_by_row(db, model).get(source.id, [])
    once: dict[str, str] = {}
    for name in (aliases.normalise(n) for n in names if n.strip()):
        once.setdefault(name.lower(), name)
    return list(once.values())


def _target(db: Session, model: type[ReferenceMixin], into: str) -> ReferenceMixin:
    """The value to merge into, or `NoSuchTarget`: it is named in the body."""
    try:
        return _row(db, model, into)
    except NoSuchValue as exc:
        raise NoSuchTarget(str(exc)) from exc


def _holding_target(
    column: Column, item_column: Column, target: ReferenceMixin
) -> Select:
    """The items that already hold the kept value: for an attribute, in force.

    A removed attribute link is not the item holding it, so the merged
    value's link is not dropped for it; `_move_records` replaces it.
    """
    held = select(item_column).where(column == target.id)
    if column.table.name == "item_attribute_link":
        held = held.where(column.table.c.removed_at.is_(None))
    return held


def _held_twice(
    db: Session,
    column: Column,
    item_column: Column,
    source: ReferenceMixin,
    target: ReferenceMixin,
) -> int:
    """Rows of `column` on items that hold the target already: the merge drops them.

    Same predicate as `merge`, which deletes them.
    """
    has_target = _holding_target(column, item_column, target)
    return (
        db.scalar(
            select(func.count())
            .select_from(column.table)
            .where((column == source.id) & item_column.in_(has_target))
        )
        or 0
    )


def plan(
    db: Session, model: type[ReferenceMixin], code: str, into: str
) -> tuple[MergePlan, ReferenceMixin, ReferenceMixin]:
    """What merging `code` into `into` would do; refuses what it cannot."""
    table = model.__tablename__
    if code == into:
        raise MergeError("A value cannot be merged into itself.")
    source = _row(db, model, code)
    target = _target(db, model, into)
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
            # Counted here, and only here, because the dry run calls only this
            # function: the preview's dropped count is then the one the
            # merge makes true.
            result.dropped += _held_twice(db, column, item_column, source, target)
    if others:
        raise MergeError(
            f"{source.label} is also used by {', '.join(others)}; "
            "change those first, or retire it instead."
        )
    result.items = len(items)
    # Only the count of `items` is returned; the codes that are for sale are
    # what the console has to show before anyone confirms a merge.
    for_sale = sale_state.for_sale(db, items)
    if for_sale:
        codes = db.scalars(
            select(InventoryItem.item_code)
            .where(InventoryItem.id.in_(list(for_sale)))
            .order_by(InventoryItem.item_code)
        ).all()
        result.for_sale_count = len(codes)
        result.for_sale = list(codes[:10])
    result.aliases, result.names_not_kept = _names_kept(db, model, source, target)
    return result, source, target


def _names_kept(
    db: Session,
    model: type[ReferenceMixin],
    source: ReferenceMixin,
    target: ReferenceMixin,
) -> tuple[list[str], list[str]]:
    """The old names the kept value gains, and those it cannot take, with why.

    The same refusals `aliases.add_alias` makes once the merged value is
    gone, worked out beforehand so the preview promises only what the merge
    keeps. A name the kept value already goes by is in neither list.
    """
    already = {target.label.lower(), target.code.lower()}
    held = aliases.aliases_by_row(db, model).get(target.id, [])
    already |= {name.lower() for name in held}
    kept: list[str] = []
    lost: list[str] = []
    for name in _new_names(db, model, source):
        if name.lower() in already:
            continue
        if len(name) > aliases.MAX_ALIAS:
            lost.append(
                f"{name!r}: longer than the {aliases.MAX_ALIAS} characters an "
                "alias holds"
            )
            continue
        other = db.scalar(
            select(model.label).where(
                model.id != source.id,
                (func.lower(model.label) == name.lower())
                | (func.lower(model.code) == name.lower()),
            )
        )
        if other is not None:
            lost.append(f"{name!r}: already the name of {other}")
            continue
        kept.append(name)
    return kept, lost


def _items_holding(db: Session, model: type[ReferenceMixin], value_id: int) -> set[int]:
    """The items whose records hold this value, across every record table."""
    items: set[int] = set()
    for column in _references(model.__table__):
        owner = column.table.name
        if owner in RECORD_TABLES:
            item_column = column.table.c[RECORD_TABLES[owner]]
            items.update(db.scalars(select(item_column).where(column == value_id)))
    return items


def _lock_items(db: Session, item_ids: set[int]) -> None:
    """Take these items' rows FOR UPDATE, in id order, in one statement."""
    if item_ids:
        db.execute(
            select(InventoryItem.id)
            .where(InventoryItem.id.in_(sorted(item_ids)))
            .order_by(InventoryItem.id)
            .with_for_update()
        )


def _attribute_codes(db: Session, item_id: int) -> list[str]:
    """The item's attributes in force, as codes: the history's `attributes`."""
    return sorted(
        db.scalars(
            select(ItemAttribute.code)
            .join(
                ItemAttributeLink,
                ItemAttributeLink.item_attribute_id == ItemAttribute.id,
            )
            .where(
                ItemAttributeLink.inventory_item_id == item_id,
                ItemAttributeLink.removed_at.is_(None),
            )
        )
    )


def _history_before(
    db: Session, model: type[ReferenceMixin], source: ReferenceMixin
) -> dict[int, dict[str, Any]]:
    """Each item's fields the merge will move, by item, as they stand now.

    A classifier column is the field of its name less `_id`, holding the
    merged code; an attribute or an error moves the whole set, as the
    editor logs it.
    """
    before: dict[int, dict[str, Any]] = {}
    for column in _references(model.__table__):
        owner = column.table.name
        if owner not in RECORD_TABLES:
            continue
        item_column = column.table.c[RECORD_TABLES[owner]]
        holding = select(item_column).where(column == source.id)
        if owner in _FIELD_TABLES:
            name = column.name.removesuffix("_id")
            for item_id in db.scalars(holding):
                before.setdefault(item_id, {})[name] = source.code
        elif owner == "item_attribute_link":
            in_force = holding.where(column.table.c.removed_at.is_(None))
            for item_id in db.scalars(in_force):
                before.setdefault(item_id, {})["attributes"] = _attribute_codes(
                    db, item_id
                )
        elif owner == "item_error":
            for item_id in db.scalars(holding):
                before.setdefault(item_id, {})["errors"] = field_changes.error_set(
                    db, item_id
                )
    return before


def _log_history(
    db: Session,
    before: dict[int, dict[str, Any]],
    target: ReferenceMixin,
    user_id: int | None,
) -> None:
    """Log each moved field in its item's history, as an edit would."""
    for item_id, fields in before.items():
        after: dict[str, Any] = {}
        for name in fields:
            if name == "attributes":
                after[name] = _attribute_codes(db, item_id)
            elif name == "errors":
                after[name] = field_changes.error_set(db, item_id)
            else:
                after[name] = target.code
        field_changes.record(db, item_id, fields, after, list(fields), user_id=user_id)


def _release_serial_links(
    db: Session, source: ReferenceMixin, target: ReferenceMixin
) -> None:
    """Hand the serial pass's links to the merge unless the pass derives the target.

    `serial_patterns` takes back every link it marked as its own whose code
    the serial no longer earns. Moved to a code it never derives, its links
    would be taken back on its next run, so they stop being its own.
    """
    if target.code in serial_patterns.DESIGNATIONS:
        return
    db.execute(
        update(ItemAttributeLink)
        .where(
            ItemAttributeLink.item_attribute_id == source.id,
            ItemAttributeLink.derived_by == serial_patterns.RULE,
        )
        .values(derived_by=None)
    )


def _keep_error_details(
    db: Session, source: ReferenceMixin, target: ReferenceMixin
) -> None:
    """Add the merged error's details to the kept one's, on an item holding both.

    The merged row is dropped, and its details are a person's note that
    nothing else holds; the same words already there are not repeated.
    """
    merged = aliased(ItemError)
    rows = db.execute(
        select(ItemError.id, ItemError.details, merged.details)
        .join(merged, merged.inventory_item_id == ItemError.inventory_item_id)
        .where(ItemError.error_type_id == target.id, merged.error_type_id == source.id)
    ).all()
    for kept_id, kept, extra in rows:
        kept_text = (kept or "").strip()
        extra_text = (extra or "").strip()
        if not extra_text or extra_text == kept_text:
            continue
        details = f"{kept_text}; {extra_text}" if kept_text else extra_text
        db.execute(
            update(ItemError).where(ItemError.id == kept_id).values(details=details)
        )


def _move_records(
    db: Session,
    model: type[ReferenceMixin],
    source: ReferenceMixin,
    target: ReferenceMixin,
) -> None:
    """Point every record table's rows at the kept value instead of the merged one."""
    for column in _references(model.__table__):
        owner = column.table.name
        if owner not in RECORD_TABLES:
            continue
        if owner == "item_image":
            image_links.replace_role(db, source.id, target.id)
            continue
        item_column = column.table.c[RECORD_TABLES[owner]]
        if owner in _ONCE_PER_ITEM:
            # `plan` -- called at the top of `merge` -- has already counted
            # these into `result.dropped`, so only the deletion happens
            # here. Counting again would double it.
            if owner == "item_error":
                _keep_error_details(db, source, target)
            has_target = _holding_target(column, item_column, target)
            duplicate = (column == source.id) & item_column.in_(has_target)
            db.execute(delete(column.table).where(duplicate))
            if owner == "item_attribute_link":
                # What is left of the merged value's links is on items whose
                # kept-value link, if any, was removed: one row per item and
                # attribute, so the removed one goes and the other takes its
                # place.
                still_held = select(item_column).where(column == source.id)
                db.execute(
                    delete(column.table).where(
                        (column == target.id) & item_column.in_(still_held)
                    )
                )
        db.execute(
            update(column.table).where(column == source.id).values({column: target.id})
        )


def merge(
    db: Session,
    model: type[ReferenceMixin],
    code: str,
    into: str,
    *,
    user_id: int | None,
) -> MergePlan:
    """Move everything from `code` to `into`, delete `code`, remember it.

    The items it moves are locked first, in id order, before any record
    under them is written: every item edit holds or writes the item's row
    before its records (`routers.inventory._hold_item` where the records
    would otherwise go first), and a merge taking the records first could
    each hold what the other waits for.
    """
    result, source, target = plan(db, model, code, into)
    table = model.__tablename__
    items = _items_holding(db, model, source.id)
    _lock_items(db, items)
    # An item given this value between that read and the lock is moved by
    # the updates below all the same; it is locked too, so its version moves.
    late = _items_holding(db, model, source.id) - items
    _lock_items(db, late)
    items |= late

    before = _history_before(db, model, source)
    if model is ItemAttribute:
        _release_serial_links(db, source, target)
    _move_records(db, model, source, target)
    _log_history(db, before, target, user_id)

    if items:
        # Moves each item's version, so a stale form is refused.
        db.execute(
            update(InventoryItem)
            .where(InventoryItem.id.in_(items))
            .values(version=InventoryItem.version + 1)
        )

    names = _new_names(db, model, source)
    aliases.delete_all(db, model, source.id)
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
