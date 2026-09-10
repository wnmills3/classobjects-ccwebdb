"""Recording what actually arrived.

Receiving does not create inventory -- the import did that at the moment of
purchase. It is a transition on a row that already exists, which is why every
test here starts from an item that is already `ordered`.
"""

from __future__ import annotations

from datetime import date

from app.models import InventoryItem, ItemStatus, ItemStatusHistory
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_schema import make_item


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
    client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "missing"},
        headers=admin_headers,
    )
    res = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received"},
        headers=admin_headers,
    )
    assert res.status_code == 200


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
