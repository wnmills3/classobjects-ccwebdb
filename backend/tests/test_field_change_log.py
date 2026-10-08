"""The item field change log: who changed which field, and when.

A conflict in the item editor says who made the other change.
`item_field_change` records each field an edit
actually changes; the item detail and the 409 name the latest change.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.models import (
    CoinDetail,
    CurrencyDetail,
    ItemFieldChange,
    ItemKind,
    Mint,
    StrikeType,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def _changes(db: Session, item_id: int) -> list[ItemFieldChange]:
    """The item's logged field changes, in field-name order."""
    return list(
        db.scalars(
            select(ItemFieldChange)
            .where(ItemFieldChange.inventory_item_id == item_id)
            .order_by(ItemFieldChange.field_name)
        )
    )


def _detail(
    client: TestClient, headers: dict[str, str], item_id: int
) -> dict[str, Any]:
    """The item as its detail route returns it."""
    body: dict[str, Any] = client.get(
        f"/api/inventory/{item_id}", headers=headers
    ).json()
    return body


@pytest.mark.parametrize("autoflush", [True, False])
def test_an_edit_logs_each_field_it_changes_and_who_changed_it(
    client: TestClient,
    admin_headers: dict[str, str],
    admin_user: User,
    db: Session,
    autoflush: bool,
) -> None:
    """Run both ways: production's session does not autoflush."""
    item = build_bare_item(db, source_title="Dime", description="as bought")
    db.autoflush = autoflush
    try:
        response = client.patch(
            f"/api/inventory/{item.id}",
            json={
                "description": "cleaned",
                "source_title": "Dime",  # sent, but unchanged: not logged
                "attributes": ["first_strike"],
            },
            headers=admin_headers,
        )
    finally:
        db.autoflush = True
    assert response.status_code == 200, response.text

    changes = _changes(db, item.id)
    assert [c.field_name for c in changes] == ["attributes", "description"]
    described = changes[1]
    assert (described.old_value, described.new_value) == ("as bought", "cleaned")
    assert described.changed_by_id == admin_user.id
    assert changes[0].new_value == ["first_strike"]


def test_the_item_detail_names_each_field_s_latest_change(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db)
    client.patch(
        f"/api/inventory/{item.id}", json={"description": "x"}, headers=admin_headers
    )
    last = _detail(client, admin_headers, item.id)["last_changes"]
    assert set(last) == {"description"}
    assert last["description"]["by"] == "Test Admin"
    assert last["description"]["at"]


def test_a_bulk_edit_logs_each_item(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    first, second = build_bare_item(db), build_bare_item(db)
    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [first.id, second.id], "changes": {"description": "lot of two"}},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    for item in (first, second):
        assert [c.field_name for c in _changes(db, item.id)] == ["description"]


def test_an_identifier_changed_by_a_leading_zero_is_logged(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """`0123` and `123` are different identifiers, so the change is a change."""
    item = build_bare_item(db, sellers_item_id="0123", source_title="007")
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"sellers_item_id": "123", "source_title": "7"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    changes = _changes(db, item.id)
    assert [(c.field_name, c.old_value, c.new_value) for c in changes] == [
        ("sellers_item_id", "0123", "123"),
        ("source_title", "007", "7"),
    ]


def test_a_cost_typed_without_its_cents_is_not_logged_as_a_change(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Money still compares as a number: 100 is the stored 100.00."""
    item = build_bare_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}", json={"item_cost": "100"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert _changes(db, item.id) == []


def _logged(db: Session, item_id: int) -> dict[str, tuple[Any, Any]]:
    """Each logged field of the item, with the value it moved from and to."""
    return {c.field_name: (c.old_value, c.new_value) for c in _changes(db, item_id)}


def test_a_kind_change_logs_the_year_and_coin_fields_it_empties(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Becoming a note drops the year, the mint and the variety: each is logged.

    None of the three is sent, and all three are gone afterwards; a history
    that named only the kind would not say what the change cost.
    """
    coin = build_bare_item(db, year_start=1935, year_end=1935)
    db.add(
        CoinDetail(
            inventory_item_id=coin.id,
            mint_id=code_id(db, Mint, "D"),
            variety="Doubled",
        )
    )
    db.commit()

    response = client.patch(
        f"/api/inventory/{coin.id}",
        json={"item_kind": "currency"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    logged = _logged(db, coin.id)
    assert logged["item_kind"] == ("coin", "currency")
    assert logged["year_start"] == (1935, None)
    assert logged["year_end"] == (1935, None)
    assert logged["mint"] == ("D", None)
    assert logged["variety"] == ("Doubled", None)


def test_a_compound_grade_logs_the_strike_type_it_sets(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """`PR65` sets the grade and the strike; the strike is not sent by name."""
    coin = build_bare_item(db, strike_type_id=code_id(db, StrikeType, "business"))

    response = client.patch(
        f"/api/inventory/{coin.id}", json={"grade": "PR65"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text

    logged = _logged(db, coin.id)
    assert logged["grade"] == (None, "65")
    assert logged["strike_type"] == ("business", "proof")


def test_a_year_logs_the_end_it_moves_and_the_no_date_it_clears(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    undated = build_bare_item(
        db, item_kind_id=code_id(db, ItemKind, "bullion"), year_start=None, no_date=True
    )

    response = client.patch(
        f"/api/inventory/{undated.id}", json={"year_start": 2021}, headers=admin_headers
    )
    assert response.status_code == 200, response.text

    assert _logged(db, undated.id) == {
        "year_start": (None, 2021),
        "year_end": (None, 2021),
        "no_date": (True, False),
    }


def test_a_face_plate_logs_the_printing_location_it_sets(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = build_bare_item(
        db, item_kind_id=code_id(db, ItemKind, "currency"), year_start=None
    )
    db.add(CurrencyDetail(inventory_item_id=note.id))
    db.commit()

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"face_plate_number": "FW E82"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    assert _logged(db, note.id) == {
        "face_plate_number": (None, "FW E82"),
        "printing_facility": (None, "fw"),
    }


def test_a_bulk_kind_change_logs_the_year_it_empties(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = build_bare_item(db, year_start=1935, year_end=1935)

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [coin.id], "changes": {"item_kind": "currency"}},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    logged = _logged(db, coin.id)
    assert logged["year_start"] == (1935, None)
    assert logged["year_end"] == (1935, None)


def test_a_conflict_says_who_made_the_other_change(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db, description="as bought")
    mine = _detail(client, admin_headers, item.id)
    theirs = _detail(client, admin_headers, item.id)
    first = client.patch(
        f"/api/inventory/{item.id}",
        json={"description": "cleaned", "base": {"description": theirs["description"]}},
        headers=admin_headers,
    )
    assert first.status_code == 200, first.text

    response = client.patch(
        f"/api/inventory/{item.id}",
        json={
            "description": "original skin",
            "base": {"description": mine["description"]},
        },
        headers=admin_headers,
    )
    assert response.status_code == 409, response.text
    [conflict] = response.json()["conflicts"]
    assert conflict["changed_by"] == "Test Admin"
    assert conflict["changed_at"]
