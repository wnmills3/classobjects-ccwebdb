"""Face and back plates, and where a note was printed (`app.plates`).

The owner, 2026-09-25: a 2017-A $1 is Fr. 3005-A from Washington and 3006-A
from Fort Worth, and the face plate says which -- FW before it is Fort Worth.
The two numbers here are made up (FR-TEST-...): the catalogue's arrangement is
never shipped (CLAUDE.md, *Reference data*).
"""

from __future__ import annotations

import pytest
from app import plates
from app.models import PurchaseOrder
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_purchase_order


@pytest.mark.parametrize(
    ("typed", "stored", "facility"),
    [
        ("E82", "E82", "dc"),
        (" e82 ", "E82", "dc"),
        ("153", "153", "dc"),
        ("FW E82", "FW E82", "fw"),
        ("fwe82", "FW E82", "fw"),
        ("FW 1234", "FW 1234", "fw"),
    ],
)
def test_a_face_plate_is_stored_one_way_and_names_its_press(
    typed: str, stored: str, facility: str
) -> None:
    assert plates.face_plate(typed) == stored
    assert plates.facility_of(stored) == facility


@pytest.mark.parametrize("typed", ["E", "EE82", "82E", "FW", "E 8 2 3 4 5 6", "#12"])
def test_a_face_plate_of_another_shape_is_refused(typed: str) -> None:
    with pytest.raises(ValueError, match="face plate"):
        plates.face_plate(typed)


def test_a_back_plate_is_digits() -> None:
    assert plates.back_plate(" 1234 ") == "1234"
    assert plates.back_plate("") is None
    with pytest.raises(ValueError, match="digits only"):
        plates.back_plate("A12")


def _order(db: Session) -> PurchaseOrder:
    return build_purchase_order(db, vendor_name="Plates Vendor")


def _note(
    client: TestClient, headers: dict[str, str], db: Session, **fields: object
) -> dict[str, object]:
    body = {
        "purchase_order_id": _order(db).id,
        "source_title": "t",
        "item_kind": "currency",
        "series_year": 2017,
        "series_letter": "A",
        **fields,
    }
    response = client.post("/api/inventory", json=body, headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, object] = response.json()
    return created


def test_a_fort_worth_face_plate_records_where_it_was_printed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = _note(
        client, admin_headers, db, face_plate_number="fw e82", back_plate_number="1234"
    )
    assert (
        note["face_plate_number"],
        note["back_plate_number"],
        note["printing_facility"],
    ) == ("FW E82", "1234", "fw")

    # Changed to a Washington plate, the location follows it.
    moved = client.patch(
        f"/api/inventory/{note['id']}",
        json={"face_plate_number": "E82"},
        headers=admin_headers,
    )
    assert moved.status_code == 200, moved.text
    detail = client.get(f"/api/inventory/{note['id']}", headers=admin_headers).json()
    assert (detail["face_plate_number"], detail["printing_facility"]) == ("E82", "dc")


def test_a_location_the_face_plate_contradicts_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order(db)
    refused = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "source_title": "t",
            "item_kind": "currency",
            "face_plate_number": "FW E82",
            "printing_facility": "dc",
        },
        headers=admin_headers,
    )
    assert refused.status_code == 422
    assert "Fort Worth" in refused.text


def test_a_location_alone_is_kept_and_a_bad_plate_is_named(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = _note(client, admin_headers, db, printing_facility="fw")
    assert note["printing_facility"] == "fw"
    bad = client.patch(
        f"/api/inventory/{note['id']}",
        json={"back_plate_number": "B12"},
        headers=admin_headers,
    )
    assert bad.status_code == 422
    assert "back plate" in bad.text


def test_plates_are_a_notes_and_not_a_coins(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    refused = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": _order(db).id,
            "source_title": "t",
            "item_kind": "coin",
            "face_plate_number": "E82",
        },
        headers=admin_headers,
    )
    assert refused.status_code == 422
    assert "face_plate_number" in refused.text


def test_the_catalogue_tells_the_two_printings_apart(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    common = {
        "denomination": "usd_note_1",
        "note_type": "frn",
        "series_year": 2017,
        "series_letter": "A",
    }
    for number, facility in (("FR-TEST-DC", "dc"), ("FR-TEST-FW", "fw")):
        created = client.post(
            "/api/friedberg",
            json={"fr_number": number, "printing_facility": facility, **common},
            headers=admin_headers,
        )
        # One identity each: the printing location is part of it.
        assert created.status_code == 201, created.text
    found = client.get(
        "/api/friedberg",
        params={**common, "printing_facility": "fw"},
        headers=admin_headers,
    )
    assert found.status_code == 200, found.text
    assert [row["fr_number"] for row in found.json()] == ["FR-TEST-FW"]
    assert found.json()[0]["printing_facility"] == "fw"
