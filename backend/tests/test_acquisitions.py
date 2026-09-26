"""Reading the acquisition side: purchase orders and storage locations."""

from __future__ import annotations

from app.models import ItemKind, ItemStatus, PurchaseOrder
from app.models.base import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, build_purchase_order, code_id


def _order_with_lines(db: Session, outstanding: int, done: int) -> PurchaseOrder:
    order = build_purchase_order(
        db,
        vendor_name=f"Vendor {outstanding}{done}",
        order_number="27-1234",
        commit=False,
    )
    ordered_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()
    received_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "received")
    ).one()
    for _ in range(outstanding):
        item = build_bare_item(db)
        item.purchase_order_id = order.id
        item.status_id = ordered_id
    for _ in range(done):
        item = build_bare_item(db)
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


def test_a_missing_line_counts_as_outstanding(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """`missing` means paid for, not cancelled, never arrived -- not `outstanding=0`.

    An order whose only receivable line is `missing` must still be offered by
    `GET /api/purchase-orders`, or the late arrival has no route back in
    through the order path at all.
    """
    order = build_purchase_order(
        db, vendor_name="Missing-only Vendor", order_number="27-4321", commit=False
    )
    missing_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "missing")
    ).one()
    item = build_bare_item(db, status_id=missing_id)
    item.purchase_order_id = order.id
    db.commit()

    res = client.get("/api/purchase-orders", headers=admin_headers)
    assert res.status_code == 200
    row = next(r for r in res.json() if r["id"] == order.id)
    assert row["outstanding"] == 1
    assert row["total"] == 1


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


def test_a_line_carries_its_title_and_kind(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The Purchases page's items table needs a title and a kind per line.

    Both come straight off the item -- no per-line query -- so a line for a
    banknote reads `item_kind: "currency"` and shows its own title rather
    than falling back to `description`.
    """
    order = build_purchase_order(
        db, vendor_name="Titled Vendor", order_number="TL-1", commit=False
    )
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        source_title="Series 1928 $1 Silver Certificate",
    )
    item.purchase_order_id = order.id
    db.commit()

    res = client.get(f"/api/purchase-orders/{order.id}", headers=admin_headers)
    assert res.status_code == 200
    line = res.json()["lines"][0]
    assert line["source_title"] == "Series 1928 $1 Silver Certificate"
    assert line["item_kind"] == "currency"


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


def test_a_deleted_item_does_not_count_or_appear(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A row that should never have existed must not inflate outstanding."""
    order = _order_with_lines(db, outstanding=1, done=1)
    ordered_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()
    deleted_item = build_bare_item(db, status_id=ordered_id)
    deleted_item.purchase_order_id = order.id
    deleted_item.deleted_at = utcnow()
    db.commit()

    res = client.get("/api/purchase-orders", headers=admin_headers)
    assert res.status_code == 200
    row = next(r for r in res.json() if r["id"] == order.id)
    assert row["outstanding"] == 1
    assert row["total"] == 2

    detail = client.get(
        f"/api/purchase-orders/{order.id}", headers=admin_headers
    ).json()
    assert len(detail["lines"]) == 2
    assert deleted_item.id not in [line["id"] for line in detail["lines"]]


def test_a_split_parent_is_hidden_but_its_children_are_not(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A split lot's parent is superseded by its children, not alongside them."""
    order = build_purchase_order(
        db, vendor_name="Split Vendor", order_number="27-9999", commit=False
    )
    ordered_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()

    parent = build_bare_item(db, status_id=ordered_id)
    child_a = build_bare_item(db, status_id=ordered_id, parent_item_id=parent.id)
    child_b = build_bare_item(db, status_id=ordered_id, parent_item_id=parent.id)
    for item in (parent, child_a, child_b):
        item.purchase_order_id = order.id
    parent.split_at = utcnow()
    db.commit()

    res = client.get("/api/purchase-orders", headers=admin_headers)
    assert res.status_code == 200
    row = next(r for r in res.json() if r["id"] == order.id)
    assert row["outstanding"] == 2
    assert row["total"] == 2

    detail = client.get(
        f"/api/purchase-orders/{order.id}", headers=admin_headers
    ).json()
    line_ids = {line["id"] for line in detail["lines"]}
    assert len(detail["lines"]) == 2
    assert parent.id not in line_ids
    assert {child_a.id, child_b.id} <= line_ids


def _order_with_source_url(db: Session, source_url: str | None) -> PurchaseOrder:
    return build_purchase_order(
        db,
        vendor_name="Source URL Vendor",
        order_number="27-5555",
        source_url=source_url,
    )


def test_a_web_address_source_url_is_returned_unchanged(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order_with_source_url(db, "https://order.ebay.com/ord/show?orderId=1")
    detail = client.get(
        f"/api/purchase-orders/{order.id}", headers=admin_headers
    ).json()
    assert detail["source_url"] == "https://order.ebay.com/ord/show?orderId=1"


def test_non_web_address_source_url_is_withheld(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Source text that is not a web address is not offered as a link.

    "Gift" is the literal value stored for some rows -- not a URL at all.
    """
    order = _order_with_source_url(db, "Gift")
    detail = client.get(
        f"/api/purchase-orders/{order.id}", headers=admin_headers
    ).json()
    assert detail["source_url"] is None


def test_a_javascript_url_is_withheld(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The http(s) guard is what stops stored text becoming a clickable link.

    Without it, a `javascript:` value in the stored source text would
    become an executable link on the receiving page.
    """
    order = _order_with_source_url(db, "javascript:alert(1)")
    detail = client.get(
        f"/api/purchase-orders/{order.id}", headers=admin_headers
    ).json()
    assert detail["source_url"] is None


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
