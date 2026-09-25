"""Purchases always have a number, and their details can be changed.

A purchase created with no order number of its own is given the next
generated one, `Order-0001` and up (owner, 2026-09-24): one entered with
none could not be found again. `PATCH /api/purchase-orders/{id}` changes
the number, date, web address or notes afterwards.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.models import InventoryItem, PurchaseOrder, Vendor
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

ORDERS = "/api/purchase-orders"


def _vendor(db: Session, name: str) -> int:
    vendor = Vendor(name=name)
    db.add(vendor)
    db.commit()
    return vendor.id


def _create(
    client: TestClient, headers: dict[str, str], vendor_id: int, **fields: object
) -> dict[str, object]:
    response = client.post(
        ORDERS, json={"vendor_id": vendor_id, **fields}, headers=headers
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def _generated_high(db: Session) -> int:
    numbers = db.scalars(select(PurchaseOrder.order_number)).all()
    return max(
        (
            int(n.removeprefix("Order-"))
            for n in numbers
            if n and n.startswith("Order-") and n.removeprefix("Order-").isdigit()
        ),
        default=0,
    )


def test_a_purchase_with_no_number_is_given_the_next_one(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Numbering Vendor")
    high = _generated_high(db)
    first = _create(client, admin_headers, vendor)
    # Across vendors: one sequence of numbers, not one per vendor.
    second = _create(
        client, admin_headers, _vendor(db, "Another Vendor"), order_number=" "
    )
    assert first["order_number"] == f"Order-{high + 1:04d}"
    assert second["order_number"] == f"Order-{high + 2:04d}"


def test_a_number_of_its_own_is_kept_and_not_counted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Own Number Vendor")
    high = _generated_high(db)
    own = _create(client, admin_headers, vendor, order_number="27-11409-83512")
    assert own["order_number"] == "27-11409-83512"
    # Only Order-NNNN counts: "Order-9x" or a vendor's own number does not.
    _create(client, admin_headers, vendor, order_number="Order-9x")
    assert _create(client, admin_headers, vendor)["order_number"] == (
        f"Order-{high + 1:04d}"
    )


def test_the_details_change_and_only_those_sent(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Edit Vendor")
    order = _create(
        client,
        admin_headers,
        vendor,
        order_number="A-1",
        notes="kept",
        source_url="https://example.com/a",
    )
    changed = client.patch(
        f"{ORDERS}/{order['id']}",
        json={"order_number": "A-2", "ordered_on": "2026-09-01"},
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["order_number"], body["ordered_on"]) == ("A-2", "2026-09-01")
    assert (body["notes"], body["source_text"]) == ("kept", "https://example.com/a")


def test_a_number_cleared_is_given_a_generated_one(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Cleared Vendor")
    order = _create(client, admin_headers, vendor, order_number="B-1")
    high = _generated_high(db)
    cleared = client.patch(
        f"{ORDERS}/{order['id']}", json={"order_number": ""}, headers=admin_headers
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["order_number"] == f"Order-{high + 1:04d}"


def test_a_duplicate_number_or_a_future_date_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Refusing Vendor")
    _create(client, admin_headers, vendor, order_number="C-1")
    other = _create(client, admin_headers, vendor, order_number="C-2")
    duplicate = client.patch(
        f"{ORDERS}/{other['id']}", json={"order_number": "C-1"}, headers=admin_headers
    )
    assert duplicate.status_code == 409
    assert "C-1 is already recorded" in duplicate.text
    # Its own number again is no conflict.
    same = client.patch(
        f"{ORDERS}/{other['id']}", json={"order_number": "C-2"}, headers=admin_headers
    )
    assert same.status_code == 200, same.text
    future = (date.today() + timedelta(days=5)).isoformat()
    late = client.patch(
        f"{ORDERS}/{other['id']}", json={"ordered_on": future}, headers=admin_headers
    )
    assert late.status_code == 422
    missing = client.patch(
        f"{ORDERS}/999999", json={"notes": "x"}, headers=admin_headers
    )
    assert missing.status_code == 404


def test_an_item_names_its_purchase(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Item Link Vendor")
    order = _create(client, admin_headers, vendor, order_number="D-1")
    created = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order["id"],
            "item_kind": "coin",
            "source_title": "t",
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert (item["purchase_order_id"], item["order_number"], item["vendor"]) == (
        order["id"],
        "D-1",
        "Item Link Vendor",
    )
    assert db.get(InventoryItem, item["id"]) is not None
