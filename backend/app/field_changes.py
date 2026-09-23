"""Who changed which field of an item, and when: the `item_field_change` log.

The sole writer of `item_field_change`. The item edit and bulk edit call
`record` with the item's values before and after, and one row is written per
field whose value actually moved -- a field sent unchanged leaves no trace.
`latest` answers the item editor's question when it warns about a field
changed elsewhere: who, and when.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ItemFieldChange, User

__all__ = ["LatestChange", "latest", "record", "same_value"]


@dataclass(frozen=True)
class LatestChange:
    """A field's most recent change: who made it, and when."""

    by: str | None
    at: datetime


def same_value(a: object, b: object) -> bool:
    """Whether two field values say the same thing.

    Blank and null are the same; numbers compare as numbers, so a cost typed
    "84" is not a change from a stored "84.00"; lists (attribute codes)
    compare as sets. The edit's field-by-field merge and this log both use
    it, so they agree on what counts as a change.
    """
    if a in (None, "") and b in (None, ""):
        return True
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (ArithmeticError, ValueError):
        return a == b


def record(
    db: Session,
    item_id: int,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    fields: Iterable[str],
    *,
    user_id: int | None,
) -> None:
    """Log each of `fields` whose value differs between `before` and `after`."""
    for field in fields:
        old, new = before.get(field), after.get(field)
        if same_value(old, new):
            continue
        db.add(
            ItemFieldChange(
                inventory_item_id=item_id,
                field_name=field,
                old_value=old,
                new_value=new,
                changed_by_id=user_id,
            )
        )


def latest(db: Session, item_id: int) -> dict[str, LatestChange]:
    """Each field's most recent logged change on one item, keyed by field.

    One query: `DISTINCT ON (field_name)` over the item's rows, newest first.
    The person is named by full name, else email; a change whose user has
    since been removed names nobody.
    """
    rows = db.execute(
        select(
            ItemFieldChange.field_name,
            ItemFieldChange.changed_at,
            User.full_name,
            User.email,
        )
        .outerjoin(User, User.id == ItemFieldChange.changed_by_id)
        .where(ItemFieldChange.inventory_item_id == item_id)
        .distinct(ItemFieldChange.field_name)
        .order_by(
            ItemFieldChange.field_name,
            ItemFieldChange.changed_at.desc(),
            ItemFieldChange.id.desc(),
        )
    ).all()
    return {
        field: LatestChange(by=(full_name or email or None), at=at)
        for field, at, full_name, email in rows
    }
