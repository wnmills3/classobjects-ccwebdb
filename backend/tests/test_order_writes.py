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
