"""Correcting and pruning the owner-kept lists (`list-maintenance-design.md`).

Friedberg numbers used here are synthetic -- the 9900s, past any real
number -- per `CLAUDE.md`.
"""

from __future__ import annotations

from typing import Any

from app.models import (
    CurrencyDetail,
    Denomination,
    FriedbergNumber,
    ItemKind,
    NoteType,
    PurchaseOrder,
    Seller,
    StorageLocation,
    StorageLocationKind,
    Vendor,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, build_purchase_order, code_id

Headers = dict[str, str]


def _fr(db: Session, fr_number: str, **fields: object) -> FriedbergNumber:
    row = FriedbergNumber(fr_number=fr_number, **fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _note_using(db: Session, row: FriedbergNumber) -> int:
    item = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id, friedberg_id=row.id, friedberg_status="proposed"
        )
    )
    db.commit()
    return item.id


def _by_id(body: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {row["id"]: row for row in body}


# --------------------------------------------------------------------------
# Friedberg numbers
# --------------------------------------------------------------------------


def test_the_catalog_lists_every_number_with_how_many_items_use_it(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    used = _fr(db, "9920")
    unused = _fr(db, "9921", description="a star note")
    _note_using(db, used)
    _note_using(db, used)

    everything = client.get("/api/friedberg/catalog", headers=admin_headers)
    assert everything.status_code == 200, everything.text
    rows = _by_id(everything.json())
    assert rows[used.id]["item_count"] == 2
    assert rows[unused.id]["item_count"] == 0

    by_text = client.get(
        "/api/friedberg/catalog", params={"q": "star"}, headers=admin_headers
    ).json()
    assert [row["id"] for row in by_text] == [unused.id]


def test_a_mistyped_number_is_corrected_and_its_items_keep_it(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    row = _fr(db, "9922-")
    item_id = _note_using(db, row)

    fixed = client.patch(
        f"/api/friedberg/{row.id}",
        json={"fr_number": "9922-L"},
        headers=admin_headers,
    )

    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["fr_number"] == "9922-L"
    detail = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    assert detail["friedberg_number"] == "9922-L"


def test_a_number_cannot_be_renamed_onto_another(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    taken = _fr(db, "9923")
    row = _fr(db, "9924")

    refused = client.patch(
        f"/api/friedberg/{row.id}",
        json={"fr_number": "9923"},
        headers=admin_headers,
    )

    assert refused.status_code == 409
    assert f"row {taken.id}" in refused.json()["detail"]


def test_a_confirmation_is_given_and_undone(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    row = _fr(db, "9925")

    confirmed = client.patch(
        f"/api/friedberg/{row.id}", json={"verified": True}, headers=admin_headers
    ).json()
    undone = client.patch(
        f"/api/friedberg/{row.id}", json={"verified": False}, headers=admin_headers
    ).json()

    assert confirmed["verified"] is True
    assert undone["verified"] is False
    db.refresh(row)
    assert row.verified_at is None
    assert row.verified_by_id is None


def test_only_an_unused_number_is_deleted(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    used = _fr(db, "9926")
    unused = _fr(db, "9927")
    _note_using(db, used)

    refused = client.delete(f"/api/friedberg/{used.id}", headers=admin_headers)
    gone = client.delete(f"/api/friedberg/{unused.id}", headers=admin_headers)

    assert refused.status_code == 409
    assert "1 item" in refused.json()["detail"]
    assert gone.status_code == 204
    assert db.get(FriedbergNumber, unused.id) is None


def test_recording_a_combination_already_on_file_names_that_row(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    """A slip's combination is named, with the row, not as a database error.

    The owner's case: `3007-` recorded by a slip, then `3007-L` refused with
    a raw database error that never said which row held the combination.
    """
    identity = {
        "denomination_id": code_id(db, Denomination, "usd_note_1"),
        "note_type_id": code_id(db, NoteType, "frn"),
        "series_year": 2021,
        "district_letter": "L",
    }
    slip = _fr(db, "9928-", **identity)

    refused = client.post(
        "/api/friedberg",
        json={
            "fr_number": "9928-L",
            "denomination": "usd_note_1",
            "note_type": "frn",
            "series_year": 2021,
            "district_letter": "L",
        },
        headers=admin_headers,
    )

    assert refused.status_code == 409
    body = refused.json()
    assert "9928-" in body["detail"]
    assert "duplicate key" not in body["detail"]
    assert body["existing"] == {"id": slip.id, "fr_number": "9928-"}


# --------------------------------------------------------------------------
# Sellers
# --------------------------------------------------------------------------


def _seller(db: Session, name: str) -> Seller:
    seller = Seller(name=name)
    db.add(seller)
    db.commit()
    db.refresh(seller)
    return seller


def test_sellers_are_listed_with_their_purchases_and_only_the_unused_deleted(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    busy = _seller(db, "busy_seller")
    idle = _seller(db, "idle_seller")
    build_purchase_order(db, vendor_name="ebay.com", seller_id=busy.id)

    rows = _by_id(client.get("/api/sellers", headers=admin_headers).json())
    assert rows[busy.id]["order_count"] == 1
    assert rows[idle.id]["order_count"] == 0

    refused = client.delete(f"/api/sellers/{busy.id}", headers=admin_headers)
    gone = client.delete(f"/api/sellers/{idle.id}", headers=admin_headers)
    assert refused.status_code == 409
    assert "1 purchase" in refused.json()["detail"]
    assert gone.status_code == 204


# --------------------------------------------------------------------------
# Vendors
# --------------------------------------------------------------------------


def test_a_vendor_is_renamed_relinked_and_rekinded(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    vendor = Vendor(name="old.example")
    db.add(vendor)
    db.commit()

    changed = client.patch(
        f"/api/vendors/{vendor.id}",
        json={
            "name": "New Dealer",
            "url": "https://shop.example.com/x",
            "vendor_kind": "dealer",
        },
        headers=admin_headers,
    )

    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "New Dealer"
    assert changed.json()["vendor_kind"] == "dealer"
    db.refresh(vendor)
    assert vendor.host == "shop.example.com"


def test_a_vendor_cannot_take_another_vendors_name(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    db.add(Vendor(name="Taken Name"))
    other = Vendor(name="Other")
    db.add(other)
    db.commit()

    refused = client.patch(
        f"/api/vendors/{other.id}", json={"name": "taken name"}, headers=admin_headers
    )
    assert refused.status_code == 409


def test_vendors_are_listed_with_their_use_and_only_the_unused_deleted(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    order = build_purchase_order(db, vendor_name="busy.example")
    idle = Vendor(name="idle.example")
    db.add(idle)
    db.commit()

    rows = _by_id(client.get("/api/vendors", headers=admin_headers).json())
    assert rows[order.vendor_id]["order_count"] == 1
    assert rows[idle.id]["order_count"] == 0

    refused = client.delete(f"/api/vendors/{order.vendor_id}", headers=admin_headers)
    gone = client.delete(f"/api/vendors/{idle.id}", headers=admin_headers)
    assert refused.status_code == 409
    assert gone.status_code == 204
    assert db.get(PurchaseOrder, order.id) is not None


# --------------------------------------------------------------------------
# Storage locations
# --------------------------------------------------------------------------


def _location(db: Session, identifier: str) -> StorageLocation:
    location = StorageLocation(
        storage_location_kind_id=code_id(db, StorageLocationKind, "safe_deposit_box"),
        institution="Test Bank",
        identifier=identifier,
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


def test_locations_are_listed_in_full_with_the_items_kept_there(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    kept = _location(db, "101")
    empty = _location(db, "102")
    build_bare_item(db, storage_location_id=kept.id)

    rows = _by_id(client.get("/api/storage-locations", headers=admin_headers).json())

    assert rows[kept.id]["item_count"] == 1
    assert rows[kept.id]["institution"] == "Test Bank"
    assert rows[kept.id]["identifier"] == "101"
    assert rows[empty.id]["item_count"] == 0


def test_a_location_is_corrected_but_not_onto_another(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    _location(db, "201")
    location = _location(db, "20l")

    fixed = client.patch(
        f"/api/storage-locations/{location.id}",
        json={"identifier": "202", "notes": "top shelf"},
        headers=admin_headers,
    )
    clash = client.patch(
        f"/api/storage-locations/{location.id}",
        json={"identifier": "201"},
        headers=admin_headers,
    )

    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["label"] == "Test Bank 202"
    assert fixed.json()["notes"] == "top shelf"
    assert clash.status_code == 409


def test_only_an_unused_location_is_deleted(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    kept = _location(db, "301")
    empty = _location(db, "302")
    build_bare_item(db, storage_location_id=kept.id)

    refused = client.delete(f"/api/storage-locations/{kept.id}", headers=admin_headers)
    gone = client.delete(f"/api/storage-locations/{empty.id}", headers=admin_headers)

    assert refused.status_code == 409
    assert gone.status_code == 204


# --------------------------------------------------------------------------
# The form of a number, where one is saved or confirmed (`app.fr_format`)
# --------------------------------------------------------------------------


def test_a_recorded_number_is_cleaned(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    made = client.post(
        "/api/friedberg", json={"fr_number": "  Fr. 9907-l "}, headers=admin_headers
    )
    assert made.status_code == 201, made.text
    assert made.json()["fr_number"] == "9907-L"


def test_a_number_ending_in_a_hyphen_is_refused_when_recorded_or_corrected(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    row = _fr(db, "9908-L")

    recorded = client.post(
        "/api/friedberg", json={"fr_number": "9909-"}, headers=admin_headers
    )
    corrected = client.patch(
        f"/api/friedberg/{row.id}", json={"fr_number": "9908-"}, headers=admin_headers
    )

    assert recorded.status_code == 422
    assert "hyphen" in recorded.text
    assert corrected.status_code == 422
    assert "hyphen" in corrected.text


def test_a_malformed_number_is_corrected_before_it_is_confirmed(
    db: Session, client: TestClient, admin_headers: Headers
) -> None:
    """Confirming is what makes a number trusted; a slip must not become one."""
    slip = _fr(db, "9910-")
    item_id = _note_using(db, slip)

    by_attach = client.post(
        f"/api/inventory/{item_id}/friedberg",
        json={"friedberg_id": slip.id, "status": "confirmed"},
        headers=admin_headers,
    )
    by_patch = client.patch(
        f"/api/friedberg/{slip.id}", json={"verified": True}, headers=admin_headers
    )
    proposed = client.post(
        f"/api/inventory/{item_id}/friedberg",
        json={"friedberg_id": slip.id, "status": "proposed"},
        headers=admin_headers,
    )

    assert by_attach.status_code == 422
    assert "9910-" in by_attach.json()["detail"]
    assert by_patch.status_code == 422
    assert proposed.status_code == 200, proposed.text
