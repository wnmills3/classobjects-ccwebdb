"""Correcting a customer record: `PATCH /api/customers/{id}`."""

from __future__ import annotations

from app.models import Customer
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _customer(db: Session) -> Customer:
    customer = Customer(display_name="Pat Buyer", email="pat@example.com")
    db.add(customer)
    db.flush()
    return customer


def test_a_customer_s_contact_details_can_be_corrected(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    customer = _customer(db)
    resp = client.patch(
        f"/api/customers/{customer.id}",
        json={"display_name": "Pat Q. Buyer", "email": None},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "Pat Q. Buyer"
    # Email is optional, so clearing it is allowed.
    assert resp.json()["email"] is None


def test_clearing_the_required_name_is_a_422_naming_it(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The column is NOT NULL: an explicit null was a 500 (code review, 2026-09-23)."""
    customer = _customer(db)
    resp = client.patch(
        f"/api/customers/{customer.id}",
        json={"display_name": None},
        headers=admin_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "display_name" in resp.json()["detail"]
    db.refresh(customer)
    assert customer.display_name == "Pat Buyer"
