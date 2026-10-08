"""Customer records: who may read and correct them, and their addresses."""

from __future__ import annotations

from app.models import Address, AddressKind, Customer
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def _customer(db: Session) -> Customer:
    """A customer record to correct, flushed."""
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
    """The column is NOT NULL: an explicit null is a 422 naming it, not a 500."""
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


# --------------------------------------------------------------------------
# Access: a customer record is a name, an email, a phone and an address
# --------------------------------------------------------------------------

_HOME = {"line1": "1 Mint Street", "city": "Carson City", "country": "US"}


def test_only_a_manager_may_list_customers(
    db: Session, client: TestClient, customer_headers: dict[str, str]
) -> None:
    """Every customer's contact details in one response: managers only."""
    _customer(db)
    assert client.get("/api/customers").status_code == 401
    refused = client.get("/api/customers", headers=customer_headers)
    assert refused.status_code == 403
    # Refused outright, not answered with a list that happens to be short.
    assert "pat@example.com" not in refused.text


def test_a_manager_lists_customers_with_their_addresses(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    customer = _customer(db)
    db.add(
        Address(
            customer_id=customer.id,
            address_kind=AddressKind.shipping,
            line1="1 Mint Street",
            city="Carson City",
            is_default=True,
        )
    )
    db.flush()

    rows = client.get("/api/customers", headers=admin_headers).json()

    row = next(r for r in rows if r["id"] == customer.id)
    assert row["email"] == "pat@example.com"
    assert [a["line1"] for a in row["addresses"]] == ["1 Mint Street"]


def test_only_a_manager_may_correct_a_customer(
    db: Session, client: TestClient, customer_headers: dict[str, str]
) -> None:
    customer = _customer(db)
    url = f"/api/customers/{customer.id}"
    change = {"display_name": "Somebody Else"}

    assert client.patch(url, json=change).status_code == 401
    assert client.patch(url, json=change, headers=customer_headers).status_code == 403
    db.refresh(customer)
    assert customer.display_name == "Pat Buyer"


def test_only_a_manager_may_record_an_address(
    db: Session, client: TestClient, customer_headers: dict[str, str]
) -> None:
    customer = _customer(db)
    url = f"/api/customers/{customer.id}/addresses"

    assert client.post(url, json=_HOME).status_code == 401
    assert client.post(url, json=_HOME, headers=customer_headers).status_code == 403
    assert (
        db.scalars(select(Address).where(Address.customer_id == customer.id)).all()
        == []
    )


# --------------------------------------------------------------------------
# Addresses are superseded, never rewritten
# --------------------------------------------------------------------------


def _addresses(db: Session, customer: Customer) -> list[Address]:
    """The customer's address rows, oldest first, read afresh."""
    db.expire_all()
    return list(
        db.scalars(
            select(Address)
            .where(Address.customer_id == customer.id)
            .order_by(Address.id)
        ).all()
    )


def test_a_first_address_becomes_the_default(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    customer = _customer(db)

    resp = client.post(
        f"/api/customers/{customer.id}/addresses", json=_HOME, headers=admin_headers
    )

    assert resp.status_code == 201, resp.text
    assert [a["line1"] for a in resp.json()["addresses"]] == ["1 Mint Street"]
    (stored,) = _addresses(db, customer)
    assert stored.is_default is True
    assert stored.address_kind is AddressKind.shipping
    assert stored.valid_from is not None
    assert stored.valid_to is None
    assert stored.country_id is not None


def test_a_new_default_address_retires_the_old_one_and_keeps_it(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The row an earlier order was sent to stays as it was, closed.

    Two defaults of one kind would also break `uq_address_default`, so the
    incumbent has to be stood down before the new row goes in: a second
    address that came back as a 500 is the failure this pins.
    """
    customer = _customer(db)
    url = f"/api/customers/{customer.id}/addresses"
    assert client.post(url, json=_HOME, headers=admin_headers).status_code == 201

    moved = client.post(
        url,
        json={"line1": "9 Assay Road", "city": "Denver", "country": "US"},
        headers=admin_headers,
    )

    assert moved.status_code == 201, moved.text
    old, new = _addresses(db, customer)
    assert (old.line1, old.city) == ("1 Mint Street", "Carson City")
    assert old.is_default is False
    assert old.valid_to is not None
    assert (new.line1, new.is_default, new.valid_to) == ("9 Assay Road", True, None)


def test_a_new_default_leaves_the_other_kind_of_address_alone(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A billing address does not retire the shipping one: one default each."""
    customer = _customer(db)
    url = f"/api/customers/{customer.id}/addresses"
    assert client.post(url, json=_HOME, headers=admin_headers).status_code == 201

    billing = client.post(
        url,
        json={**_HOME, "line1": "PO Box 7", "address_kind": "billing"},
        headers=admin_headers,
    )

    assert billing.status_code == 201, billing.text
    shipping, bill = _addresses(db, customer)
    assert (shipping.is_default, shipping.valid_to) == (True, None)
    assert (bill.address_kind, bill.is_default) == (AddressKind.billing, True)


def test_an_address_that_is_not_the_default_retires_nothing(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    customer = _customer(db)
    url = f"/api/customers/{customer.id}/addresses"
    assert client.post(url, json=_HOME, headers=admin_headers).status_code == 201

    second = client.post(
        url,
        json={**_HOME, "line1": "9 Assay Road", "is_default": False},
        headers=admin_headers,
    )

    assert second.status_code == 201, second.text
    first, other = _addresses(db, customer)
    assert (first.is_default, first.valid_to) == (True, None)
    assert other.is_default is False


def test_an_address_in_an_unknown_country_is_a_422_and_writes_nothing(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    customer = _customer(db)

    resp = client.post(
        f"/api/customers/{customer.id}/addresses",
        json={**_HOME, "country": "ZZ-NOWHERE"},
        headers=admin_headers,
    )

    assert resp.status_code == 422, resp.text
    assert "ZZ-NOWHERE" in resp.json()["detail"]
    assert _addresses(db, customer) == []


def test_an_address_for_an_unknown_customer_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    resp = client.post(
        "/api/customers/999999/addresses", json=_HOME, headers=admin_headers
    )
    assert resp.status_code == 404
