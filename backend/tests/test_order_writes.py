"""Orders placed and revised on a customer's behalf.

See docs/specs/order-on-behalf-design.md. The engine is `app.order_writes`;
these tests go through the HTTP API wherever a caller would.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import (
    Customer,
    Listing,
    SalesOrder,
    SalesOrderChange,
    SalesOrderChangeKind,
    SalesOrderStatus,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_orders import place


def test_an_order_records_its_placer_version_and_changes(
    db: Session, customer_user: User, admin_user: User
) -> None:
    customer = Customer(user_id=customer_user.id, display_name="Buyer")
    db.add(customer)
    db.flush()
    pending = db.scalar(
        select(SalesOrderStatus.id).where(SalesOrderStatus.code == "pending")
    )
    order = SalesOrder(
        customer_id=customer.id,
        sales_order_status_id=pending,
        placed_by_id=admin_user.id,
    )
    db.add(order)
    db.flush()
    db.add(
        SalesOrderChange(
            sales_order_id=order.id,
            changed_by_id=admin_user.id,
            change=SalesOrderChangeKind.placed,
            to_value=admin_user.email,
        )
    )
    db.commit()
    db.refresh(order)

    assert order.version == 1
    assert order.placed_by is not None and order.placed_by.email == admin_user.email
    assert [c.change for c in order.changes] == [SalesOrderChangeKind.placed]
    assert order.total_amount == Decimal("0.00")


def test_checkout_records_the_buyer_as_placer_and_writes_placed(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    body = place(client, customer_headers, listing.id, 2).json()

    assert body["version"] == 1
    assert body["placed_by_email"] == "customer@example.com"
    assert body["payment_adjustment_due"] is False
    changes = db.scalars(
        select(SalesOrderChange).where(SalesOrderChange.sales_order_id == body["id"])
    ).all()
    assert [(c.change, c.to_value) for c in changes] == [
        (SalesOrderChangeKind.placed, "customer@example.com")
    ]


def _new_customer(db: Session, name: str = "Walk-in Buyer") -> Customer:
    customer = Customer(display_name=name, email=None)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def test_an_admin_places_an_order_for_a_customer_without_an_account(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)

    response = client.post(
        f"/api/customers/{buyer.id}/orders",
        json={
            "items": [
                {"listing_id": listing.id, "quantity": 2, "unit_price": "150.00"}
            ],
            "notes": "phone order",
        },
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["customer_name"] == "Walk-in Buyer"
    assert body["placed_by_email"] == "admin@example.com"
    assert body["items"][0]["unit_price"] == "150.00"
    assert body["total_amount"] == "300.00"
    assert body["notes"] == "phone order"
    db.refresh(listing)
    assert listing.quantity_available == 3


def test_a_line_without_a_price_takes_the_listing_price(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)
    body = client.post(
        f"/api/customers/{buyer.id}/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=admin_headers,
    ).json()
    assert body["items"][0]["unit_price"] == "189.00"


def test_only_an_admin_may_place_an_order_for_someone(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)
    payload = {
        "items": [{"listing_id": listing.id, "quantity": 1, "unit_price": "0.01"}]
    }
    url = f"/api/customers/{buyer.id}/orders"

    assert client.post(url, json=payload).status_code == 401
    assert client.post(url, json=payload, headers=customer_headers).status_code == 403
    assert db.scalar(select(SalesOrder.id)) is None
    db.refresh(listing)
    assert listing.quantity_available == 5


def test_placing_for_an_unknown_customer_is_a_404(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/customers/999999/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_admin_order_payloads_are_validated(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)
    url = f"/api/customers/{buyer.id}/orders"
    line = {"listing_id": listing.id, "quantity": 1}
    for bad in (
        {"items": []},
        {"items": [{**line, "quantity": 0}]},
        {"items": [{**line, "unit_price": "-1.00"}]},
        {"items": [line, line]},
        {"items": [line], "customer_id": 1},
    ):
        assert client.post(url, json=bad, headers=admin_headers).status_code == 422, bad


def test_an_account_can_be_given_a_customer_record(
    client: TestClient, admin_headers: dict[str, str], customer_user: User, db: Session
) -> None:
    first = client.post(
        f"/api/users/{customer_user.id}/customer", headers=admin_headers
    )
    again = client.post(
        f"/api/users/{customer_user.id}/customer", headers=admin_headers
    )

    assert first.status_code == 200
    assert first.json()["user_id"] == customer_user.id
    assert again.json()["id"] == first.json()["id"]


def test_only_an_admin_may_create_a_customer_record_for_an_account(
    client: TestClient, customer_headers: dict[str, str], customer_user: User
) -> None:
    url = f"/api/users/{customer_user.id}/customer"
    assert client.post(url).status_code == 401
    assert client.post(url, headers=customer_headers).status_code == 403
