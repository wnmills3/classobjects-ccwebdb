"""Sellers: who sold a purchase on the marketplace its vendor names.

`GET/POST /api/sellers`, `PATCH /api/sellers/{id}`, and a purchase's
`seller_id`. See docs/specs/entry-panels-design.md.
"""

from __future__ import annotations

from app.models import PurchaseOrder, Seller, Vendor
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

STORE = "https://www.ebay.com/usr/coind0g"


def _seller(
    db: Session, name: str = "coind0g", store_url: str | None = STORE
) -> Seller:
    seller = Seller(name=name, store_url=store_url)
    db.add(seller)
    db.commit()
    return seller


def _order(db: Session, **fields: object) -> PurchaseOrder:
    vendor = Vendor(name=f"ebay.com {len(fields)}")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, order_number="S-1", **fields)
    db.add(order)
    db.commit()
    return order


def test_a_seller_is_added_with_a_name_and_store(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/sellers",
        json={"name": "  coind0g  ", "store_url": STORE},
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    assert res.json() == {
        "id": res.json()["id"],
        "name": "coind0g",
        "store_url": STORE,
        "order_count": 0,
    }


def test_a_seller_needs_no_store(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/sellers", json={"name": "Coin show table 12"}, headers=admin_headers
    )
    assert res.status_code == 201, res.text
    assert res.json()["store_url"] is None


def test_a_sellers_name_is_unique_whatever_its_case(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _seller(db)
    res = client.post("/api/sellers", json={"name": "COIND0G"}, headers=admin_headers)
    assert res.status_code == 409
    assert "COIND0G" in res.json()["detail"]


def test_a_store_that_is_not_a_web_address_is_a_422(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/sellers",
        json={"name": "x", "store_url": "javascript:alert(1)"},
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_sellers_are_listed_by_name(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _seller(db, "zeta")
    _seller(db, "Alpha", None)
    res = client.get("/api/sellers", headers=admin_headers)
    assert res.status_code == 200
    assert [s["name"] for s in res.json()] == ["Alpha", "zeta"]


def test_a_seller_is_renamed_and_its_store_changed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    seller = _seller(db)
    _seller(db, "taken", None)

    moved = client.patch(
        f"/api/sellers/{seller.id}",
        json={"store_url": "https://www.ebay.com/str/coind0gcoins"},
        headers=admin_headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["store_url"] == "https://www.ebay.com/str/coind0gcoins"
    assert moved.json()["name"] == "coind0g"

    clash = client.patch(
        f"/api/sellers/{seller.id}", json={"name": "Taken"}, headers=admin_headers
    )
    assert clash.status_code == 409

    cleared = client.patch(
        f"/api/sellers/{seller.id}", json={"store_url": ""}, headers=admin_headers
    )
    assert cleared.json()["store_url"] is None


def test_sellers_are_staff_only(client: TestClient) -> None:
    assert client.get("/api/sellers").status_code == 401


def test_a_purchase_names_its_seller(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    seller = _seller(db)
    vendor = Vendor(name="ebay.com")
    db.add(vendor)
    db.commit()

    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "order_number": "S-9", "seller_id": seller.id},
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["seller_id"], body["seller"], body["seller_url"]) == (
        seller.id,
        "coind0g",
        STORE,
    )


def test_an_unknown_seller_is_a_404(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = Vendor(name="ebay.com")
    db.add(vendor)
    db.commit()
    res = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor.id, "seller_id": 999999},
        headers=admin_headers,
    )
    assert res.status_code == 404


def test_a_purchases_seller_is_changed_and_cleared(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    seller = _seller(db)
    order = _order(db)

    named = client.patch(
        f"/api/purchase-orders/{order.id}",
        json={"seller_id": seller.id},
        headers=admin_headers,
    )
    assert named.status_code == 200, named.text
    assert named.json()["seller"] == "coind0g"

    cleared = client.patch(
        f"/api/purchase-orders/{order.id}",
        json={"seller_id": None},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["seller_id"], cleared.json()["seller"]) == (None, None)
    assert cleared.json()["order_number"] == "S-1"
