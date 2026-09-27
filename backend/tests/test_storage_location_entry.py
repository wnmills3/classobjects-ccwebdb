"""A storage location chosen when an item is entered, and changed in the editor.

Optional, and changeable over time: every move goes through
`lifecycle_writes.set_location`, so each is kept in the item's location
history, as Receiving's are. See docs/specs/entry-panels-design.md.
"""

from __future__ import annotations

from app.models import (
    InventoryItem,
    ItemFieldChange,
    LocationHistory,
    StorageLocation,
    StorageLocationKind,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.builders import build_purchase_order
from tests.conftest import build_listing


def _location(db: Session, kind: str = "safe") -> StorageLocation:
    kind_id = db.scalar(
        select(StorageLocationKind.id).where(StorageLocationKind.code == kind)
    )
    assert kind_id is not None
    location = StorageLocation(storage_location_kind_id=kind_id)
    db.add(location)
    db.commit()
    return location


def _moves(db: Session, item_id: int) -> list[int | None]:
    return list(
        db.scalars(
            select(LocationHistory.storage_location_id)
            .where(LocationHistory.inventory_item_id == item_id)
            .order_by(LocationHistory.id)
        )
    )


def _new_item(
    client: TestClient, headers: dict[str, str], db: Session, **fields: object
) -> dict[str, object]:
    order = build_purchase_order(db, vendor_name="Location Vendor")
    res = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "coin",
            "source_title": "A dime",
            **fields,
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body: dict[str, object] = res.json()
    return body


def test_an_item_is_entered_where_it_is_kept(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    safe = _location(db)
    body = _new_item(client, admin_headers, db, storage_location_id=safe.id)

    assert body["storage_location_id"] == safe.id
    assert _moves(db, int(str(body["id"]))) == [safe.id]


def test_an_item_entered_with_none_has_no_move(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    body = _new_item(client, admin_headers, db)

    assert body["storage_location_id"] is None
    assert _moves(db, int(str(body["id"]))) == []


def test_an_unknown_location_refuses_the_entry(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = build_purchase_order(db, vendor_name="Bad Location Vendor")
    before = db.scalar(select(func.count()).select_from(InventoryItem))
    res = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "coin",
            "source_title": "A dime",
            "storage_location_id": 999999,
        },
        headers=admin_headers,
    )

    assert res.status_code == 422
    assert "storage_location_id" in res.json()["detail"]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(InventoryItem)) == before


def test_the_editor_moves_an_item_and_keeps_each_move(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    safe = _location(db)
    home = _location(db, "home")
    item_id = int(
        str(_new_item(client, admin_headers, db, storage_location_id=safe.id)["id"])
    )

    moved = client.patch(
        f"/api/inventory/{item_id}",
        json={"storage_location_id": home.id},
        headers=admin_headers,
    )
    assert moved.status_code == 200, moved.text
    cleared = client.patch(
        f"/api/inventory/{item_id}",
        json={"storage_location_id": None},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text

    detail = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    assert detail["storage_location_id"] is None
    assert _moves(db, item_id) == [safe.id, home.id, None]
    # Kept once, as a move -- not a second time as a field change.
    logged = db.scalar(
        select(func.count()).where(
            ItemFieldChange.inventory_item_id == item_id,
            ItemFieldChange.field_name == "storage_location_id",
        )
    )
    assert logged == 0


def test_moving_to_where_it_already_is_records_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    safe = _location(db)
    item_id = int(
        str(_new_item(client, admin_headers, db, storage_location_id=safe.id)["id"])
    )

    res = client.patch(
        f"/api/inventory/{item_id}",
        json={"storage_location_id": safe.id},
        headers=admin_headers,
    )

    assert res.status_code == 200, res.text
    assert _moves(db, item_id) == [safe.id]


def test_an_unknown_location_refuses_the_edit(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = int(str(_new_item(client, admin_headers, db)["id"]))
    res = client.patch(
        f"/api/inventory/{item_id}",
        json={"storage_location_id": 999999},
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_moving_an_item_on_offer_needs_no_acknowledgement(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    # Where it is kept never shows to a buyer.
    listing = build_listing(db)
    safe = _location(db)
    res = client.patch(
        f"/api/inventory/{listing.inventory_item_id}",
        json={"storage_location_id": safe.id},
        headers=admin_headers,
    )
    assert res.status_code == 200, res.text
