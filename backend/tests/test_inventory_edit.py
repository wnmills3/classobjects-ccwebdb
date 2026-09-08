"""Editing an item that is not for sale.

Which is all of them: the collection has 7,591 items and no listings, so
before this endpoint existed there was no way to correct any of them through
the API.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def test_an_unlisted_item_can_be_edited(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db, source_title="wrong", year_start=1878)

    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "1881-S Morgan Silver Dollar", "year_start": 1881},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_title"] == "1881-S Morgan Silver Dollar"
    assert body["year_start"] == 1881


def test_a_classifier_is_set_by_code(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Codes, never ids -- an id is meaningless to a client and unstable."""
    item = make_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"grade": "MS63"}, headers=admin_headers
    )

    assert response.status_code == 200
    db.refresh(item)
    assert item.grade_id is not None


def test_an_unknown_code_is_refused_naming_the_field(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A silently null column is how 185 junk grades got in once already."""
    item = make_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"grade": "NOT_A_GRADE"},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "grade" in response.json()["detail"]


def test_a_stale_version_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Two staff, one loaded form each; the second must not silently win."""
    item = make_item(db)
    stale = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()[
        "version"
    ]

    first = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "careful", "version": stale},
        headers=admin_headers,
    )
    assert first.status_code == 200

    second = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "clobbering", "version": stale},
        headers=admin_headers,
    )
    assert second.status_code == 409

    db.refresh(item)
    assert item.source_title == "careful"


def test_omitting_the_version_edits_unconditionally(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A script that means 'set this regardless' can say so."""
    item = make_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "no version sent"},
        headers=admin_headers,
    )
    assert response.status_code == 200


def test_an_omitted_field_is_left_alone(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """exclude_unset, so a partial form does not null everything it omits."""
    item = make_item(db, source_title="keep me", year_start=1921)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"year_start": 1922}, headers=admin_headers
    )

    assert response.status_code == 200
    db.refresh(item)
    assert item.source_title == "keep me"


def test_a_customer_cannot_edit_inventory(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    """Everything on this router exposes cost basis."""
    item = make_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "nope"},
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_money_survives_a_round_trip_as_a_string(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A Decimal that becomes a float has lost the guarantee it was for."""
    item = make_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"item_cost": "19.99"},
        headers=admin_headers,
    )
    assert response.json()["item_cost"] == "19.99"
    db.refresh(item)
    assert item.item_cost == Decimal("19.99")


def test_a_piece_reports_what_its_lot_claimed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """So it is always visible what is being overridden.

    And what is still only the seller's word about the lot.
    """
    from tests.test_split import TUBE, do_split, lot

    parent = lot(db, year_start=1881)
    piece_id = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"][0][
        "id"
    ]

    body = client.get(f"/api/inventory/{piece_id}", headers=admin_headers).json()

    assert body["parent_item_code"] == parent.item_code
    assert body["lot_claims"]["year_start"] == 1881


def test_an_item_with_no_parent_claims_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """7,591 of 7,591 have no parent. Absent is the normal case, not an error."""
    item = make_item(db)
    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()

    assert body["parent_item_code"] is None
    assert body["lot_claims"] == {}


def test_the_detail_carries_what_has_been_reviewed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """One round trip for the edit form, not two."""
    item = make_item(db)
    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )

    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert body["reviewed"] == ["grade_id"]


def test_a_piece_reports_the_lot_s_claim_even_when_it_still_agrees(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The agreeing case is the important one, not the boring one.

    Fifty Morgans split out of one lot all read grade BU because they
    inherited the seller's claim, not because anyone graded them. A form that
    showed the lot's value only where the piece already differs would stay
    silent on exactly the fields that need the warning.
    """
    from tests.test_split import TUBE, do_split, lot

    parent = lot(db, year_start=1881)
    piece_id = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"][0][
        "id"
    ]

    body = client.get(f"/api/inventory/{piece_id}", headers=admin_headers).json()

    assert body["year_start"] == 1881, "the piece inherited the lot's year"
    assert body["lot_claims"]["year_start"] == 1881, (
        "and the response must still say the lot is where that came from"
    )
    assert body["reviewed"] == [], "nobody has confirmed it"
