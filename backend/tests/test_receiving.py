"""Recording what actually arrived.

Receiving does not create inventory -- the import did that at the moment of
purchase. It is a transition on a row that already exists, which is why every
test here starts from an item that is already `ordered`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

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

from tests.test_schema import code_id, make_item


def _location(db: Session) -> StorageLocation:
    location = StorageLocation(
        storage_location_kind_id=code_id(db, StorageLocationKind, "home"),
        identifier="test box",
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


def _ordered(db: Session) -> InventoryItem:
    item = make_item(db)
    item.status_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()
    db.commit()
    return item


def test_receiving_records_the_arrival(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _ordered(db)
    res = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "received",
            "arrived_on": "2026-09-04",
            "note": "edge knock not in the listing photos",
        },
        headers=admin_headers,
    )
    assert res.status_code == 200

    db.expire_all()
    row = db.scalars(
        select(ItemStatusHistory)
        .where(ItemStatusHistory.inventory_item_id == item.id)
        .order_by(ItemStatusHistory.id.desc())
    ).first()
    assert row.arrived_on == date(2026, 9, 4)
    assert row.note == "edge knock not in the listing photos"
    assert row.changed_by_id is not None


def test_a_far_future_arrival_date_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """arrived_on records when the thing physically turned up.

    Two or more days past UTC's today is what an actual fat-fingered date
    looks like -- a single day ahead is a legitimate "today" for anyone in a
    timezone ahead of UTC, so the bound sits one day wider than UTC's own
    calendar date (see the endpoint's comment).
    """
    item = _ordered(db)
    # Matches the endpoint's own reference point (UTC), not local wall-clock
    # time -- the two can disagree by a day within a few hours of midnight
    # UTC, which would make this test flaky against a fixed local `today`.
    too_far = datetime.now(UTC).date() + timedelta(days=2)
    res = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "received",
            "arrived_on": too_far.isoformat(),
        },
        headers=admin_headers,
    )
    assert res.status_code == 422
    assert too_far.isoformat() in res.text


def test_todays_arrival_date_is_accepted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The boundary matters: today is a real arrival, not a future one."""
    item = _ordered(db)
    today = datetime.now(UTC).date()
    res = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "received",
            "arrived_on": today.isoformat(),
        },
        headers=admin_headers,
    )
    assert res.status_code == 200


def test_one_day_ahead_of_utc_is_accepted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A day ahead of UTC's today is a real local "today" somewhere.

    The backend only has UTC to compare against, but a caller's local
    calendar date can lead UTC's by up to a day (anywhere east of it, once
    UTC has not yet reached local midnight). Refusing that would reject a
    genuine same-day receipt for a large share of the world for several
    hours every evening, which is exactly the bug this bound was widened to
    fix.
    """
    item = _ordered(db)
    one_ahead = datetime.now(UTC).date() + timedelta(days=1)
    res = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "received",
            "arrived_on": one_ahead.isoformat(),
        },
        headers=admin_headers,
    )
    assert res.status_code == 200


def test_one_bad_id_writes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A partial receipt across twenty coins leaves a state nobody can describe.

    So every id is resolved before anything is written.
    """
    good = _ordered(db)
    res = client.post(
        "/api/inventory/receive",
        json={"item_ids": [good.id, 10_000_000], "outcome": "received"},
        headers=admin_headers,
    )
    assert res.status_code == 404
    assert "10000000" in res.text

    db.expire_all()
    assert (
        db.get(type(good), good.id).status_id
        != db.scalars(select(ItemStatus.id).where(ItemStatus.code == "received")).one()
    )


def test_receiving_twice_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Silently re-receiving overwrites a true arrival date with today's."""
    item = _ordered(db)
    first = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received", "arrived_on": "2026-09-04"},
        headers=admin_headers,
    )
    assert first.status_code == 200

    again = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received"},
        headers=admin_headers,
    )
    assert again.status_code == 409
    # The spec promises the 409 names the current status and the date it
    # arrived, not just that it was already received -- that's what tells a
    # double-submitted form apart from the wrong row.
    assert "received" in again.text
    assert "2026-09-04" in again.text

    db.expire_all()
    row = db.scalars(
        select(ItemStatusHistory)
        .where(ItemStatusHistory.inventory_item_id == item.id)
        .order_by(ItemStatusHistory.id.desc())
    ).first()
    assert row.arrived_on == date(2026, 9, 4)


def test_a_parcel_written_off_as_missing_can_still_turn_up(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """`missing` means paid for, not cancelled, never arrived.

    And things that never arrived sometimes arrive.
    """
    item = _ordered(db)
    missing_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "missing")
    ).one()
    received_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "received")
    ).one()

    first = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "missing"},
        headers=admin_headers,
    )
    assert first.status_code == 200

    db.expire_all()
    assert db.get(type(item), item.id).status_id == missing_id

    res = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received"},
        headers=admin_headers,
    )
    assert res.status_code == 200

    db.expire_all()
    assert db.get(type(item), item.id).status_id == received_id

    row = db.scalars(
        select(ItemStatusHistory)
        .where(ItemStatusHistory.inventory_item_id == item.id)
        .order_by(ItemStatusHistory.id.desc())
    ).first()
    assert row.from_status_id == missing_id
    assert row.to_status_id == received_id


def test_an_unknown_outcome_is_refused_listing_the_known_ones(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _ordered(db)
    res = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "delivered"},
        headers=admin_headers,
    )
    assert res.status_code == 422
    assert "received" in res.text


def test_receiving_with_a_location_writes_it_and_its_history(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Location and history are written together, and only for `received`.

    The other half of that guarantee is the next test.
    """
    item = _ordered(db)
    location = _location(db)
    res = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "received",
            "storage_location_id": location.id,
        },
        headers=admin_headers,
    )
    assert res.status_code == 200

    db.expire_all()
    assert db.get(type(item), item.id).storage_location_id == location.id
    rows = db.scalars(
        select(LocationHistory).where(LocationHistory.inventory_item_id == item.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].storage_location_id == location.id


def test_a_missing_outcome_with_a_location_writes_neither(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """`storage_location_id` is documented as "only meaningful when received".

    Sent alongside `missing` anyway, it must be ignored outright: not the
    item's column, not a history row -- an item marked missing has not been
    put anywhere.
    """
    item = _ordered(db)
    location = _location(db)
    res = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "missing",
            "storage_location_id": location.id,
        },
        headers=admin_headers,
    )
    assert res.status_code == 200

    db.expire_all()
    assert db.get(type(item), item.id).storage_location_id is None
    rows = db.scalars(
        select(LocationHistory).where(LocationHistory.inventory_item_id == item.id)
    ).all()
    assert rows == []


def test_a_customer_cannot_receive(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    item = _ordered(db)
    res = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received"},
        headers=customer_headers,
    )
    assert res.status_code == 403
