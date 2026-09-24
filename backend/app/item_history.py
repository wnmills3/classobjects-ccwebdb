"""One item's history, read from the three logs that record it.

`item_field_change` (edits made in the item editor and the bulk edit),
`item_status_history` (every acquisition-status move, receiving included)
and `location_history` (every physical move) each answer part of "what
happened to this item". `timeline` merges them into one list, newest first,
so the item editor can show it without knowing there are three.

Read-only. The writers stay where they are -- `app.field_changes.record`,
`app.lifecycle_writes` -- and nothing here writes a row.

Classifier values are logged as codes (`"usd_coin_1_00"`), which is what
the editor holds but not what anyone reads, so a code whose field names a
classifier is shown by its label. A code that no longer resolves -- the
value was since merged away -- is shown as the code, rather than dropped.

Not included: listing status moves (the Offers panel shows every offer and
its state) and the machine passes' own marks (`item_field_source`), which
record where a value came from, not a change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from .models import (
    ItemFieldChange,
    ItemStatus,
    ItemStatusHistory,
    LocationHistory,
    StorageLocation,
    User,
)
from .models.base import ReferenceMixin

__all__ = ["HistoryEvent", "location_label", "timeline"]

EventKind = Literal["field", "status", "location"]


@dataclass(frozen=True)
class HistoryEvent:
    """One thing that happened to an item: what, from what, to what, who, when."""

    kind: EventKind
    #: The field as the editor names it; `status` and `location` for the
    #: other two logs.
    field: str
    old_value: Any
    new_value: Any
    by: str | None
    at: datetime
    note: str | None = None
    #: When a parcel actually arrived, on the status move that received it.
    arrived_on: date | None = None


def location_label(location: StorageLocation) -> str:
    """A human-readable identity for a storage location.

    `institution` and `identifier` are the only free-text fields that tell
    one location apart from another of the same kind; a location with
    neither set falls back to naming its kind.
    """
    parts = [part for part in (location.institution, location.identifier) if part]
    return " ".join(parts) if parts else location.kind.label


def _person(full_name: str | None, email: str | None) -> str | None:
    """A user as the history names them: full name, else email."""
    return full_name or email or None


def _labels(
    db: Session,
    changes: list[ItemFieldChange],
    classifiers: Mapping[str, type[ReferenceMixin]],
) -> dict[tuple[str, str], str]:
    """Each logged classifier code's label, keyed by (field, code).

    One query per classifier table that actually appears in the changes.
    """
    wanted: dict[str, set[str]] = {}
    for change in changes:
        if change.field_name not in classifiers:
            continue
        for value in (change.old_value, change.new_value):
            values = value if isinstance(value, list) else [value]
            wanted.setdefault(change.field_name, set()).update(
                v for v in values if isinstance(v, str) and v
            )
    labels: dict[tuple[str, str], str] = {}
    for field, codes in wanted.items():
        if not codes:
            continue
        model = classifiers[field]
        for code, label in db.execute(
            select(model.code, model.label).where(model.code.in_(codes))
        ).tuples():
            labels[(field, code)] = label
    return labels


def _shown(value: object, field: str, labels: Mapping[tuple[str, str], str]) -> object:
    """A logged value with its classifier codes replaced by their labels."""
    if isinstance(value, list):
        return [_shown(v, field, labels) for v in value]
    if isinstance(value, str):
        return labels.get((field, value), value)
    return value


def _field_events(
    db: Session, item_id: int, classifiers: Mapping[str, type[ReferenceMixin]]
) -> list[HistoryEvent]:
    """The item's logged field edits."""
    rows = list(
        db.execute(
            select(ItemFieldChange, User.full_name, User.email)
            .outerjoin(User, User.id == ItemFieldChange.changed_by_id)
            .where(ItemFieldChange.inventory_item_id == item_id)
        ).tuples()
    )
    labels = _labels(db, [change for change, _, _ in rows], classifiers)
    return [
        HistoryEvent(
            kind="field",
            field=change.field_name,
            old_value=_shown(change.old_value, change.field_name, labels),
            new_value=_shown(change.new_value, change.field_name, labels),
            by=_person(full_name, email),
            at=change.changed_at,
        )
        for change, full_name, email in rows
    ]


def _status_events(db: Session, item_id: int) -> list[HistoryEvent]:
    """The item's status moves; the first has no `from`."""
    was = aliased(ItemStatus)
    now = aliased(ItemStatus)
    rows = db.execute(
        select(ItemStatusHistory, was.label, now.label, User.full_name, User.email)
        .outerjoin(was, was.id == ItemStatusHistory.from_status_id)
        .join(now, now.id == ItemStatusHistory.to_status_id)
        .outerjoin(User, User.id == ItemStatusHistory.changed_by_id)
        .where(ItemStatusHistory.inventory_item_id == item_id)
        .order_by(ItemStatusHistory.changed_at.desc(), ItemStatusHistory.id.desc())
    ).tuples()
    return [
        HistoryEvent(
            kind="status",
            field="status",
            old_value=old,
            new_value=new,
            by=_person(full_name, email),
            at=row.changed_at,
            note=row.note,
            arrived_on=row.arrived_on,
        )
        for row, old, new, full_name, email in rows
    ]


def _location_events(db: Session, item_id: int) -> list[HistoryEvent]:
    """The item's moves.

    A row records only where the item went, so where it came from is the
    previous row's destination, read in the order the moves happened.
    """
    rows = db.execute(
        select(LocationHistory, StorageLocation, User.full_name, User.email)
        .outerjoin(
            StorageLocation, StorageLocation.id == LocationHistory.storage_location_id
        )
        .outerjoin(User, User.id == LocationHistory.moved_by_id)
        .where(LocationHistory.inventory_item_id == item_id)
        .order_by(LocationHistory.moved_at, LocationHistory.id)
    ).tuples()
    events: list[HistoryEvent] = []
    previous: str | None = None
    for row, location, full_name, email in rows:
        here = location_label(location) if location is not None else None
        events.append(
            HistoryEvent(
                kind="location",
                field="location",
                old_value=previous,
                new_value=here,
                by=_person(full_name, email),
                at=row.moved_at,
                note=row.note,
            )
        )
        previous = here
    events.reverse()
    return events


#: Among events at the same instant, the order they are listed in: a
#: receipt writes its status and location in one transaction, and an edit's
#: fields all share a timestamp.
_KIND_ORDER: dict[EventKind, int] = {"status": 0, "location": 1, "field": 2}


def timeline(
    db: Session, item_id: int, classifiers: Mapping[str, type[ReferenceMixin]]
) -> list[HistoryEvent]:
    """Everything logged about one item, newest first.

    `classifiers` maps a field name to the table its codes come from, so a
    logged code can be shown by its label; a field not in it is shown as
    logged.
    """
    events = [
        *_field_events(db, item_id, classifiers),
        *_status_events(db, item_id),
        *_location_events(db, item_id),
    ]
    # Newest first. Within one instant: by log, then an edit's fields by
    # name; status and location rows keep their newest-row-first order
    # (both sorts are stable, `reverse=True` included).
    events.sort(
        key=lambda e: (_KIND_ORDER[e.kind], e.field if e.kind == "field" else "")
    )
    events.sort(key=lambda e: e.at, reverse=True)
    return events
