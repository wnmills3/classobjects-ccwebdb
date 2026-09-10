"""Every status and location change records that it happened.

The table exists so "when did this actually arrive" survives a later
correction to the status. That only holds if nothing can change the column
without writing history, which is why these are the only writers.
"""

from __future__ import annotations

from datetime import date

from app.lifecycle_writes import record_initial_status, set_location, set_status
from app.models import (
    InventoryItem,
    ItemStatus,
    ItemStatusHistory,
    LocationHistory,
    StorageLocation,
    StorageLocationKind,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_catalog import NEW_ITEM
from tests.test_schema import code_id, make_item
from tests.test_split import TUBE, do_split
from tests.test_split import lot as split_lot


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
    assert row.changed_at.date() == date.today()


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


def test_record_initial_status_writes_an_opening_row(db: Session) -> None:
    """The opening row has no `from`: there was no status before this one."""
    item = make_item(db, status_id=_status_id(db, "ordered"))
    db.flush()
    record_initial_status(db, item, note="set at import")
    db.commit()

    rows = db.scalars(
        select(ItemStatusHistory).where(ItemStatusHistory.inventory_item_id == item.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].from_status_id is None
    assert rows[0].to_status_id == item.status_id
    assert rows[0].note == "set at import"


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


def test_bulk_editing_the_status_through_the_api_records_it_per_item(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A bulk edit is the likelier path to "received" than a single PATCH.

    An owner marks a whole order received at once, not one coin at a time.
    """
    ordered = _status_id(db, "ordered")
    items = [make_item(db, status_id=ordered) for _ in range(2)]
    db.commit()

    res = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items], "changes": {"status": "received"}},
        headers=admin_headers,
    )
    assert res.status_code == 200
    assert res.json()["updated"] == 2

    for item in items:
        rows = db.scalars(
            select(ItemStatusHistory).where(
                ItemStatusHistory.inventory_item_id == item.id
            )
        ).all()
        assert rows[-1].from_status_id == ordered
        assert rows[-1].to_status_id == _status_id(db, "received")
        assert rows[-1].changed_by_id is not None


def test_no_item_lacks_history_across_every_creation_path_a_test_can_drive(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The invariant itself, not one endpoint's obedience to it.

    Four code paths build an `InventoryItem`: the importer, `splitting.py`,
    the catalogue API, and `seed.py`. A test process cannot drive the
    importer or the demo seed script without a great deal of unrelated
    setup, but it can drive the two live endpoints -- catalogue creation and
    splitting -- which is enough to check the invariant at the table level
    rather than re-asserting what a single code path does: after exercising
    both, no `inventory_item` row anywhere is missing its opening
    `item_status_history` row.
    """
    client.post("/api/catalog", json=NEW_ITEM, headers=admin_headers)

    # `split_lot` is test scaffolding built directly with `make_item`, which
    # deliberately bypasses these helpers so tests can set up arbitrary
    # starting states -- it is not one of the four creation paths under
    # test, so it is excluded from the check below rather than expected to
    # satisfy it.
    parent = split_lot(db)
    db.commit()
    split_res = do_split(client, admin_headers, parent.id, TUBE)
    assert split_res.status_code == 200

    orphans = db.scalars(
        select(InventoryItem.id)
        .outerjoin(
            ItemStatusHistory,
            ItemStatusHistory.inventory_item_id == InventoryItem.id,
        )
        .where(ItemStatusHistory.id.is_(None), InventoryItem.id != parent.id)
    ).all()
    assert orphans == []
