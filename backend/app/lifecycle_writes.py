"""The only writers of an item's status and location.

Both columns have a history table beside them, and both tables exist so a
later correction cannot erase what actually happened -- `lifecycle.py` says
so: "History is a table rather than overwritten columns, so that 'when did
this actually arrive' survives a later correction to the status."

That promise holds only if nothing assigns the column without writing the
row. A helper some callers use is a convention; a helper that is the only way
to reach the column is a property of the code. Assign `item.status_id` or
`item.storage_location_id` anywhere else and the history silently stops being
true.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from .models import InventoryItem, ItemStatusHistory, LocationHistory

__all__ = ["set_location", "set_status"]


def set_status(
    session: Session,
    item: InventoryItem,
    to_status_id: int,
    *,
    user_id: int | None = None,
    note: str | None = None,
    arrived_on: date | None = None,
) -> None:
    """Change an item's status and record that it changed.

    A no-op when the status already matches: re-asserting a value is not a
    transition, and a history full of non-events buries the real ones.

    ``arrived_on`` is the date the thing physically turned up, which is not
    the same as ``changed_at`` -- see the column's note.
    """
    if item.status_id == to_status_id:
        return

    session.add(
        ItemStatusHistory(
            inventory_item_id=item.id,
            from_status_id=item.status_id,
            to_status_id=to_status_id,
            changed_at=datetime.now(UTC),
            changed_by_id=user_id,
            note=note,
            arrived_on=arrived_on,
        )
    )
    item.status_id = to_status_id


def set_location(
    session: Session,
    item: InventoryItem,
    storage_location_id: int | None,
    *,
    user_id: int | None = None,
    note: str | None = None,
) -> None:
    """Move an item and record the move.

    Unlike a status, a location is legitimately nullable -- "not recorded" is
    a real answer -- so None is a value here, not an absence.
    """
    if item.storage_location_id == storage_location_id:
        return

    session.add(
        LocationHistory(
            inventory_item_id=item.id,
            storage_location_id=storage_location_id,
            moved_at=datetime.now(UTC),
            moved_by_id=user_id,
            note=note,
        )
    )
    item.storage_location_id = storage_location_id
