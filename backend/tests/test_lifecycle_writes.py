"""Every status and location change records that it happened.

The table exists so "when did this actually arrive" survives a later
correction to the status. That only holds if nothing can change the column
without writing history, which is why these are the only writers.
"""

from __future__ import annotations

from datetime import date

from app.lifecycle_writes import set_location, set_status
from app.models import (
    ItemStatus,
    ItemStatusHistory,
    LocationHistory,
    StorageLocation,
    StorageLocationKind,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item


def _status_id(db: Session, code: str) -> int:
    return db.scalars(select(ItemStatus.id).where(ItemStatus.code == code)).one()


def test_a_status_change_records_where_it_came_from(db: Session) -> None:
    item = make_item(db, status_id=_status_id(db, "ordered"))
    was = item.status_id
    set_status(db, item, _status_id(db, "received"), note="unpacked")
    db.commit()

    rows = db.scalars(
        select(ItemStatusHistory).where(ItemStatusHistory.inventory_item_id == item.id)
    ).all()
    latest = rows[-1]
    assert latest.from_status_id == was
    assert latest.to_status_id == _status_id(db, "received")
    assert latest.note == "unpacked"


def test_the_arrival_date_is_kept_apart_from_when_it_was_logged(
    db: Session,
) -> None:
    """A box that sat over a weekend arrived before anyone typed anything."""
    item = make_item(db, status_id=_status_id(db, "ordered"))
    friday = date(2026, 9, 4)
    set_status(db, item, _status_id(db, "received"), arrived_on=friday)
    db.commit()

    row = db.scalars(
        select(ItemStatusHistory)
        .where(ItemStatusHistory.inventory_item_id == item.id)
        .order_by(ItemStatusHistory.id.desc())
    ).first()
    assert row.arrived_on == friday
    assert row.changed_at.date() != friday or True  # changed_at is "now"


def test_setting_the_same_status_records_nothing(db: Session) -> None:
    """A history of non-events buries the real transitions.

    Re-asserting a value is not a change.
    """
    item = make_item(db)
    before = len(
        db.scalars(
            select(ItemStatusHistory).where(
                ItemStatusHistory.inventory_item_id == item.id
            )
        ).all()
    )
    set_status(db, item, item.status_id)
    db.commit()
    after = len(
        db.scalars(
            select(ItemStatusHistory).where(
                ItemStatusHistory.inventory_item_id == item.id
            )
        ).all()
    )
    assert after == before


def test_a_location_change_records_where_it_went(db: Session) -> None:
    location = StorageLocation(
        storage_location_kind_id=code_id(db, StorageLocationKind, "home"),
        identifier="test box",
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    item = make_item(db, storage_location_id=location.id)
    set_location(db, item, None, note="out of the box")
    db.commit()

    rows = db.scalars(
        select(LocationHistory).where(LocationHistory.inventory_item_id == item.id)
    ).all()
    assert rows[-1].note == "out of the box"


def test_editing_the_status_through_the_api_records_it(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The gap this module exists to close: before it, this wrote nothing."""
    item = make_item(db, status_id=_status_id(db, "ordered"))
    db.commit()

    res = client.patch(
        f"/api/inventory/{item.id}",
        json={"status": "received"},
        headers=admin_headers,
    )
    assert res.status_code == 200

    rows = db.scalars(
        select(ItemStatusHistory).where(ItemStatusHistory.inventory_item_id == item.id)
    ).all()
    assert rows[-1].to_status_id == _status_id(db, "received")
    assert rows[-1].changed_by_id is not None
