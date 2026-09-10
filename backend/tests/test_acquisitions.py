"""Reading the acquisition side, which until now was import-only."""

from __future__ import annotations

from app.models import ItemStatus, PurchaseOrder, Vendor
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def _order_with_lines(db: Session, outstanding: int, done: int) -> PurchaseOrder:
    vendor = Vendor(name=f"Vendor {outstanding}{done}")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, order_number="27-1234")
    db.add(order)
    db.flush()
    ordered_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()
    received_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "received")
    ).one()
    for _ in range(outstanding):
        item = make_item(db)
        item.purchase_order_id = order.id
        item.status_id = ordered_id
    for _ in range(done):
        item = make_item(db)
        item.purchase_order_id = order.id
        item.status_id = received_id
    db.commit()
    return order


def test_orders_report_how_much_is_outstanding(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order_with_lines(db, outstanding=3, done=2)
    res = client.get("/api/purchase-orders", headers=admin_headers)
    assert res.status_code == 200
    row = next(r for r in res.json() if r["id"] == order.id)
    assert row["outstanding"] == 3
    assert row["total"] == 5


def test_an_order_lists_its_lines_with_their_status(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order_with_lines(db, outstanding=2, done=1)
    res = client.get(f"/api/purchase-orders/{order.id}", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body["lines"]) == 3
    assert sorted(line["status"] for line in body["lines"]) == [
        "ordered",
        "ordered",
        "received",
    ]


def test_an_unknown_order_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.get("/api/purchase-orders/10000000", headers=admin_headers)
    assert res.status_code == 404


def test_a_customer_cannot_read_purchase_orders(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    """What the owner paid a vendor is not a customer's business."""
    _order_with_lines(db, outstanding=1, done=0)
    assert (
        client.get("/api/purchase-orders", headers=customer_headers).status_code == 403
    )


def test_a_customer_cannot_read_storage_locations(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    """A public listing must not leak the safe-deposit box holding an item.

    That would be a security failure, not a cosmetic one.
    """
    assert (
        client.get("/api/storage-locations", headers=customer_headers).status_code
        == 403
    )
