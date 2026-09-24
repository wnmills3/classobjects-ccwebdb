"""The New purchase panel's backend: vendors and purchase-order creation.

This is what lets an owner start a purchase from the console;
`test_acquisitions.py` reads `vendor` and `purchase_order` back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.models import PurchaseOrder, Vendor
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------
# Vendors
# --------------------------------------------------------------------------


def _vendor(db: Session, name: str = "Existing Vendor") -> Vendor:
    vendor = Vendor(name=name)
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return vendor


def test_vendors_are_listed_ordered_by_name(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _vendor(db, "Zed Coins")
    _vendor(db, "Acme Numismatics")
    res = client.get("/api/vendors", headers=admin_headers)
    assert res.status_code == 200
    names = [row["name"] for row in res.json()]
    assert names.index("Acme Numismatics") < names.index("Zed Coins")


def test_creating_a_vendor_returns_it_with_its_kind(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/vendors",
        json={
            "name": "Heritage Auctions",
            "url": "https://www.ha.com",
            "vendor_kind": "auction",
        },
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["name"] == "Heritage Auctions"
    assert body["url"] == "https://www.ha.com"
    assert body["vendor_kind"] == "auction"
    assert isinstance(body["id"], int)


def test_a_vendor_with_no_kind_defaults_to_unknown(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/vendors", json={"name": "Walk-in Show"}, headers=admin_headers
    )
    assert res.status_code == 201, res.text
    assert res.json()["vendor_kind"] == "unknown"


def test_a_duplicate_vendor_name_is_refused_case_insensitively(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _vendor(db, "ebay.com")
    res = client.post("/api/vendors", json={"name": "eBay.com"}, headers=admin_headers)
    assert res.status_code == 409
    assert "eBay.com" in res.text


def test_an_unknown_vendor_kind_is_a_422(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/vendors",
        json={"name": "Some Dealer", "vendor_kind": "not_a_kind"},
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_a_vendor_url_sets_its_host(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    res = client.post(
        "/api/vendors",
        json={"name": "Heritage Host", "url": "https://www.HA.com/coins"},
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    vendor = db.get(Vendor, res.json()["id"])
    assert vendor is not None
    assert vendor.host == "www.ha.com"


def test_a_vendor_with_no_url_has_no_host(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    res = client.post(
        "/api/vendors", json={"name": "No URL Vendor"}, headers=admin_headers
    )
    assert res.status_code == 201, res.text
    vendor = db.get(Vendor, res.json()["id"])
    assert vendor is not None
    assert vendor.host is None


def test_a_blank_vendor_name_is_a_422(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post("/api/vendors", json={"name": "   "}, headers=admin_headers)
    assert res.status_code == 422


def test_a_padded_vendor_name_is_stored_trimmed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    res = client.post(
        "/api/vendors", json={"name": "  Padded Vendor  "}, headers=admin_headers
    )
    assert res.status_code == 201, res.text
    assert res.json()["name"] == "Padded Vendor"
    vendor = db.get(Vendor, res.json()["id"])
    assert vendor is not None
    assert vendor.name == "Padded Vendor"


def test_a_non_http_vendor_url_is_a_422(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/vendors",
        json={"name": "Some Dealer", "url": "ftp://example.com"},
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_a_concurrent_duplicate_vendor_name_is_a_409_not_a_500(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two racing creations of the same name must not surface as a 500.

    The pre-check is a plain SELECT, not a lock: two concurrent posts can
    both see no duplicate and both proceed to INSERT. Simulated here by
    making the pre-check itself miss (as if it ran before the other request
    committed) while the conflicting row already exists -- the commit must
    then hit `uq_vendor_name` and come back as the same 409, not an
    unhandled `IntegrityError`.
    """
    _vendor(db, "ebay.com")
    monkeypatch.setattr(db, "scalar", lambda *args, **kwargs: None)

    res = client.post("/api/vendors", json={"name": "ebay.com"}, headers=admin_headers)

    assert res.status_code == 409
    assert "ebay.com" in res.text
    monkeypatch.undo()
    count = db.scalar(
        select(func.count()).select_from(Vendor).where(Vendor.name == "ebay.com")
    )
    assert count == 1


def test_a_customer_cannot_list_or_create_vendors(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    assert client.get("/api/vendors", headers=customer_headers).status_code == 403
    res = client.post(
        "/api/vendors", json={"name": "Should Not Exist"}, headers=customer_headers
    )
    assert res.status_code == 403
    assert db.scalar(select(Vendor).where(Vendor.name == "Should Not Exist")) is None


def test_a_signed_out_caller_cannot_list_or_create_vendors(client: TestClient) -> None:
    assert client.get("/api/vendors").status_code == 401
    assert client.post("/api/vendors", json={"name": "Nope"}).status_code == 401


# --------------------------------------------------------------------------
# Purchase orders
# --------------------------------------------------------------------------


def test_creating_a_purchase_order_returns_the_detail_shape(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Show Table Vendor")
    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "notes": "Bought at the coin show"},
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["vendor"] == "Show Table Vendor"
    assert body["order_number"] is None
    assert body["lines"] == []


def test_creating_a_purchase_order_with_an_order_number(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "eBay Vendor")
    res = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor.id,
            "order_number": "27-1234",
            "ordered_on": "2026-09-01",
            "source_url": "https://order.ebay.com/ord/show?orderId=1",
        },
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["order_number"] == "27-1234"
    assert body["ordered_on"] == "2026-09-01"
    assert body["source_url"] == "https://order.ebay.com/ord/show?orderId=1"


def test_an_unknown_vendor_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/purchase-orders", json={"vendor_id": 10_000_000}, headers=admin_headers
    )
    assert res.status_code == 404


def test_the_same_vendor_and_order_number_twice_is_a_409(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Repeat Vendor")
    order = PurchaseOrder(vendor_id=vendor.id, order_number="A-1")
    db.add(order)
    db.commit()

    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "order_number": "A-1"},
        headers=admin_headers,
    )
    assert res.status_code == 409
    assert "Repeat Vendor" in res.text
    assert "A-1" in res.text


def test_two_numberless_purchases_for_one_vendor_are_both_allowed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The partial unique index only guards a real order number.

    A blank order number is not an order number -- see the note on
    `PurchaseOrder`.
    """
    vendor = _vendor(db, "Numberless Vendor")
    first = client.post(
        "/api/purchase-orders", json={"vendor_id": vendor.id}, headers=admin_headers
    )
    second = client.post(
        "/api/purchase-orders", json={"vendor_id": vendor.id}, headers=admin_headers
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["id"] != second.json()["id"]


def test_a_blank_order_number_is_stored_as_null(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Blank Number Vendor")
    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "order_number": "   "},
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["order_number"] is None


def test_a_far_future_ordered_on_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Future Date Vendor")
    too_far = (datetime.now(UTC).date() + timedelta(days=5)).isoformat()
    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "ordered_on": too_far},
        headers=admin_headers,
    )
    assert res.status_code == 422
    assert too_far in res.text


def test_a_non_http_source_url_is_a_422(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Bad URL Vendor")
    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "source_url": "javascript:alert(1)"},
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_a_concurrent_duplicate_order_number_is_a_409_not_a_500(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two racing creations of the same vendor + order number: same fix as vendors.

    `uq_purchase_order_vendor_number` is the backstop when the pre-check
    itself misses a row committed just after it ran.
    """
    vendor = _vendor(db, "Race Vendor")
    order = PurchaseOrder(vendor_id=vendor.id, order_number="R-1")
    db.add(order)
    db.commit()
    monkeypatch.setattr(db, "scalar", lambda *args, **kwargs: None)

    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "order_number": "R-1"},
        headers=admin_headers,
    )

    assert res.status_code == 409
    assert "Race Vendor" in res.text
    assert "R-1" in res.text
    monkeypatch.undo()
    count = db.scalar(
        select(func.count())
        .select_from(PurchaseOrder)
        .where(
            PurchaseOrder.vendor_id == vendor.id,
            PurchaseOrder.order_number == "R-1",
        )
    )
    assert count == 1


def test_an_extra_field_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Extra Field Vendor")
    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "not_a_field": "x"},
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_a_customer_cannot_create_a_purchase_order(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    vendor = _vendor(db, "Customer Blocked Vendor")
    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id},
        headers=customer_headers,
    )
    assert res.status_code == 403
    assert (
        db.scalar(select(PurchaseOrder).where(PurchaseOrder.vendor_id == vendor.id))
        is None
    )


def test_a_signed_out_caller_cannot_create_a_purchase_order(
    client: TestClient, db: Session
) -> None:
    vendor = _vendor(db, "Signed Out Blocked Vendor")
    res = client.post("/api/purchase-orders", json={"vendor_id": vendor.id})
    assert res.status_code == 401
    assert (
        db.scalar(select(PurchaseOrder).where(PurchaseOrder.vendor_id == vendor.id))
        is None
    )
