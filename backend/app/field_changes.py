"""Who changed which field of an item, and when: the `item_field_change` log.

The sole writer of `item_field_change`. The item edit, bulk edit, the
errors endpoint and the eBay order pass (`app.ebay_orders`) call `record`
with the item's values before and after, and one row is written per field
whose value actually moved -- a field sent unchanged leaves no trace.
`latest` answers the item editor's question when it warns about a field
changed elsewhere: who, and when.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ErrorType, ItemError, ItemFieldChange, User, utcnow

__all__ = [
    "LatestChange",
    "error_set",
    "latest",
    "record",
    "same_value",
    "sorted_errors",
]


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


def sorted_errors(errors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """An error set in a fixed order, so two equal sets log as equal."""
    return sorted(
        ({"error_type": e["error_type"], "details": e.get("details")} for e in errors),
        key=lambda e: (e["error_type"], e["details"] or ""),
    )


def error_set(db: Session, item_id: int) -> list[dict[str, Any]]:
    """The item's recorded errors as the change log holds them: code and details.

    `errors` is logged as the whole set, before and after, because the
    errors endpoint replaces the whole set; the History shows each entry.
    """
    rows = db.execute(
        select(ErrorType.code, ItemError.details)
        .join(ErrorType, ErrorType.id == ItemError.error_type_id)
        .where(ItemError.inventory_item_id == item_id)
    ).tuples()
    return sorted_errors({"error_type": code, "details": d} for code, d in rows)


def record(
    db: Session,
    item_id: int,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    fields: Iterable[str],
    *,
    user_id: int | None,
    at: datetime | None = None,
    text_fields: Collection[str] = (),
) -> int:
    """Log each of `fields` whose value differs between `before` and `after`.

    Returns how many rows it logged. `at` stamps every row with one moment --
    a pass that changes many items at once passes its start -- and defaults
    to now. A field in `text_fields` is compared as text rather than by
    `same_value`: an identifier such as an order number `0123` is not `123`.
    """
    logged = 0
    for field in fields:
        old, new = before.get(field), after.get(field)
        if field in text_fields:
            if (old or None) == (new or None):
                continue
        elif same_value(old, new):
            continue
        logged += 1
        db.add(
            ItemFieldChange(
                inventory_item_id=item_id,
                field_name=field,
                old_value=old,
                new_value=new,
                changed_by_id=user_id,
                changed_at=utcnow() if at is None else at,
            )
        )
    return logged


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
