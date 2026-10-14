"""Races that answered 500 answer as the check before them does.

Each handler checks before it writes, and a second request can commit
between the check and the write. These make the check miss -- as it would,
run before the other request committed -- and expect the answer the check
itself gives.
"""

from __future__ import annotations

import pytest
from app import aliases, buyers
from app.models import (
    CurrencyDetail,
    Customer,
    FriedbergNumber,
    GradeDesignation,
    ItemKind,
    ReferenceAlias,
    SalesVenue,
    Seller,
    StorageLocation,
    StorageLocationKind,
    Vendor,
)
from app.routers import acquisitions, friedberg, inventory
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from tests.builders import build_bare_item, build_purchase_order, code_id


def test_a_vendor_named_on_a_purchase_since_the_count_is_not_deleted(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vendor = build_purchase_order(db, vendor_name="Raced Vendor").vendor
    monkeypatch.setattr(acquisitions, "_vendor_use", lambda *_: 0)

    refused = client.delete(f"/api/vendors/{vendor.id}", headers=admin_headers)

    assert refused.status_code == 409, refused.text
    assert "Raced Vendor" in refused.json()["detail"]
    assert db.get(Vendor, vendor.id) is not None


def test_a_seller_named_on_a_purchase_since_the_count_is_not_deleted(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seller = Seller(name="raced_seller")
    db.add(seller)
    db.flush()
    build_purchase_order(db, vendor_name="Raced Market", seller_id=seller.id)
    monkeypatch.setattr(acquisitions, "_seller_orders", lambda *_: 0)

    refused = client.delete(f"/api/sellers/{seller.id}", headers=admin_headers)

    assert refused.status_code == 409, refused.text
    assert db.get(Seller, seller.id) is not None


def test_a_location_given_an_item_since_the_count_is_not_deleted(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    location = StorageLocation(
        storage_location_kind_id=code_id(db, StorageLocationKind, "safe"),
        institution="Raced Bank",
    )
    db.add(location)
    db.flush()
    build_bare_item(db, storage_location_id=location.id)
    db.commit()
    monkeypatch.setattr(acquisitions, "_location_use", lambda *_: 0)

    refused = client.delete(
        f"/api/storage-locations/{location.id}", headers=admin_headers
    )

    assert refused.status_code == 409, refused.text
    assert db.get(StorageLocation, location.id) is not None


def test_an_alias_added_twice_at_once_is_a_409(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second request looked before the first committed the same name."""
    row_id = code_id(db, GradeDesignation, "CAM")
    db.add(
        ReferenceAlias(table_name="grade_designation", row_id=row_id, alias="Frosty")
    )
    db.commit()
    monkeypatch.setattr(aliases, "_existing", lambda *_: None)

    refused = client.post(
        "/api/reference/grade_designation/CAM/aliases",
        json={"alias": "Frosty"},
        headers=admin_headers,
    )

    assert refused.status_code == 409, refused.text
    count = db.scalar(
        select(func.count())
        .select_from(ReferenceAlias)
        .where(ReferenceAlias.alias == "Frosty")
    )
    assert count == 1


def test_a_buyer_recorded_twice_at_once_is_one_customer(
    db: Session, ebay_venue: SalesVenue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loser of the insert reads the winner's row instead of failing."""
    winner = buyers.venue_buyer(db, ebay_venue, "RacedBuyer")
    db.commit()
    # This request's lookup ran before the winner committed: it found nothing.
    monkeypatch.setattr(db, "scalar", lambda *args, **kwargs: None)

    loser = buyers.venue_buyer(db, ebay_venue, "racedbuyer")

    monkeypatch.undo()
    assert loser.id == winner.id
    count = db.scalar(
        select(func.count())
        .select_from(Customer)
        .where(func.lower(Customer.venue_username) == "racedbuyer")
    )
    assert count == 1


def test_two_default_addresses_at_once_wait_for_each_other(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The customer's row is held while its default is stood down and replaced."""
    customer = Customer(display_name="Raced Address")
    db.add(customer)
    db.commit()
    sent: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_: object) -> None:
        """Keep each statement, in order."""
        sent.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", record)
    try:
        made = client.post(
            f"/api/customers/{customer.id}/addresses",
            json={
                "address_kind": "shipping",
                "line1": "1 Main St",
                "city": "Springfield",
                "country": "US",
                "is_default": True,
            },
            headers=admin_headers,
        )
    finally:
        event.remove(connection, "before_cursor_execute", record)

    assert made.status_code == 201, made.text
    holds = [
        i for i, s in enumerate(sent) if "FOR UPDATE" in s and "FROM customer" in s
    ]
    inserts = [i for i, s in enumerate(sent) if s.startswith("INSERT INTO address")]
    assert holds, sent
    assert inserts, sent
    assert holds[0] < inserts[0]


@pytest.mark.parametrize("route", ["preview", "suggested-description"])
def test_a_dry_run_that_meets_another_save_is_a_409(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    item = build_bare_item(db)
    db.commit()

    def landed(*_: object) -> None:
        """Another save committed on this item while the dry run wrote."""
        raise StaleDataError("UPDATE matched no rows")

    monkeypatch.setattr(inventory, "_dry_run_save", landed)

    refused = client.post(
        f"/api/inventory/{item.id}/{route}", json={}, headers=admin_headers
    )

    assert refused.status_code == 409, refused.text


def test_a_catalog_row_attached_since_the_count_is_not_deleted(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = FriedbergNumber(fr_number="9990")
    db.add(row)
    db.flush()
    note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))
    db.add(CurrencyDetail(inventory_item_id=note.id, friedberg_id=row.id))
    db.commit()
    monkeypatch.setattr(friedberg, "_items_using", lambda *_: 0)

    refused = client.delete(f"/api/friedberg/{row.id}", headers=admin_headers)

    assert refused.status_code == 409, refused.text
    assert "9990" in refused.json()["detail"]
    assert db.get(FriedbergNumber, row.id) is not None
