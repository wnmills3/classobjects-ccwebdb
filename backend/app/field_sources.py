"""Which fields of an item hold a derived default, and which are a person's.

The rule (docs/specs/classifier-defaults-design.md, option B):

- A pass that fills a field from known facts records it here.
- A pass may later refresh a field recorded here, and never touches one
  that is not.
- A person saving a field removes its record: from then on it is theirs.
- A person emptying a field records it as `held`: it stays empty, and no pass
  fills it, until someone sets it again.

Field names are columns (`note_type_id`, `fineness`), as `item_field_review`
uses, whether the column lives on the item or on its currency detail.
"""

from __future__ import annotations

from collections.abc import Iterable

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
SERIES_BACKFILL = "series_backfill"
SUGGESTION = "suggestion"
RATING = "rating"

#: Not a rule: a person emptied the field, and it is to stay empty.
HELD = "held"


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


def hold(db: Session, item_ids: Iterable[int], fields: Iterable[str]) -> None:
    """A person has emptied these fields: keep them empty."""
    names = list(fields)
    for item_id in item_ids:
        record_derived(db, item_id, names, HELD)


def derived_fields(db: Session, item_id: int) -> dict[str, str]:
    """Field name to the rule that filled it, for one item."""
    return dict(
        db.execute(
            select(ItemFieldSource.field_name, ItemFieldSource.derived_by).where(
                ItemFieldSource.inventory_item_id == item_id,
                ItemFieldSource.derived_by != HELD,
            )
        )
        .tuples()
        .all()
    )


def sources_by_item(
    db: Session, item_ids: Iterable[int] | None = None
) -> dict[int, dict[str, str]]:
    """Every item's recorded sources (field to rule), or only the given items'.

    Scoped when a single save asks, so saving one item does not read the
    provenance of the whole collection.

    Includes `HELD` rows, which are not a rule at all -- they record that a
    person emptied the field. `derived_fields` filters them out and this does
    not, so the two readers of this table mean different things by a row;
    `classifier_defaults.classify` separates them by hand after calling this.
    """
    query = select(
        ItemFieldSource.inventory_item_id,
        ItemFieldSource.field_name,
        ItemFieldSource.derived_by,
    )
    if item_ids is not None:
        query = query.where(ItemFieldSource.inventory_item_id.in_(list(item_ids)))
    found: dict[int, dict[str, str]] = {}
    for item_id, field, rule in db.execute(query).tuples():
        found.setdefault(item_id, {})[field] = rule
    return found
