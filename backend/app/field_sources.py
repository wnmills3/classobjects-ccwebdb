"""Which fields of an item hold a derived default, and which are a person's.

The rule (docs/specs/classifier-defaults-design.md, option B):

- A pass that fills a field from known facts records it here.
- A pass may later refresh a field recorded here, and never touches one
  that is not.
- A person saving a field removes its record: from then on it is theirs.

Field names are columns (`note_type_id`, `fineness`), as `item_field_review`
uses, whether the column lives on the item or on its currency detail.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import ItemFieldSource, utcnow

#: Rules that fill fields, as recorded in `derived_by`.
NOTE_ISSUE = "note_issue"
SERIAL_DISTRICT = "serial_district"
COMPOSITION = "composition"
SERIES_MATCH = "series_match"
SERIES_CLASSIFY = "series_classify"
SUGGESTION = "suggestion"


def record_derived(
    db: Session, item_id: int, fields: Iterable[str], derived_by: str
) -> None:
    """Mark fields of one item as holding a default filled by `derived_by`."""
    rows = [
        {
            "inventory_item_id": item_id,
            "field_name": field,
            "derived_by": derived_by,
            "derived_at": utcnow(),
        }
        for field in fields
    ]
    if not rows:
        return
    statement = insert(ItemFieldSource).values(rows)
    db.execute(
        statement.on_conflict_do_update(
            constraint="uq_item_field_source",
            set_={
                "derived_by": statement.excluded.derived_by,
                "derived_at": statement.excluded.derived_at,
            },
        )
    )


def forget(db: Session, item_ids: Iterable[int], fields: Iterable[str]) -> None:
    """A person has set these fields: they are no longer derived."""
    ids, names = list(item_ids), list(fields)
    if not ids or not names:
        return
    db.execute(
        delete(ItemFieldSource).where(
            ItemFieldSource.inventory_item_id.in_(ids),
            ItemFieldSource.field_name.in_(names),
        )
    )


def derived_fields(db: Session, item_id: int) -> dict[str, str]:
    """Field name to the rule that filled it, for one item."""
    return dict(
        db.execute(
            select(ItemFieldSource.field_name, ItemFieldSource.derived_by).where(
                ItemFieldSource.inventory_item_id == item_id
            )
        )
        .tuples()
        .all()
    )


def derived_by_item(db: Session) -> dict[int, set[str]]:
    """Every item's derived fields, for a pass deciding what it may refresh."""
    found: dict[int, set[str]] = {}
    for item_id, field in db.execute(
        select(ItemFieldSource.inventory_item_id, ItemFieldSource.field_name)
    ).tuples():
        found.setdefault(item_id, set()).add(field)
    return found


def columns_set(data: Mapping[str, object], columns: Mapping[str, str]) -> list[str]:
    """The columns a request sets, from its field names.

    `columns` maps an API field name to its column (`grade` -> `grade_id`);
    a name it does not list is its own column.
    """
    return [columns.get(field, field) for field in data]
