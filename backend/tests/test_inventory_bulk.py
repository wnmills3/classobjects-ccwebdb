"""Setting one field across many items at once.

All-or-nothing in one transaction. A partial bulk edit across 50 coins leaves
a state nobody can describe, and "which of the 50 applied?" is not a question
the UI should ever have to answer.
"""

from __future__ import annotations

import pytest
from app import offering_writes
from app.models import Denomination, ItemKind, Listing, Metal
from app.routers import inventory
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def test_a_field_is_set_across_every_selected_item(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    items = [build_bare_item(db, year_start=None) for _ in range(3)]

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items], "changes": {"year_start": 1964}},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 3
    for item in items:
        db.refresh(item)
        assert item.year_start == 1964


def test_an_unselected_item_is_untouched(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    chosen = build_bare_item(db, year_start=1878)
    other = build_bare_item(db, year_start=1921)

    client.post(
        "/api/inventory/bulk",
        json={"ids": [chosen.id], "changes": {"year_start": 1964}},
        headers=admin_headers,
    )

    db.refresh(other)
    assert other.year_start == 1921


def test_one_bad_id_changes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """All-or-nothing. A half-applied bulk edit is unreportable."""
    items = [build_bare_item(db, year_start=1878) for _ in range(3)]

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items] + [999999], "changes": {"year_start": 1964}},
        headers=admin_headers,
    )

    assert response.status_code == 404
    for item in items:
        db.refresh(item)
        assert item.year_start == 1878, "a rejected bulk edit must apply nothing"


def test_one_bad_code_changes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    items = [build_bare_item(db, year_start=1878) for _ in range(3)]

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items], "changes": {"grade": "NOT_A_GRADE"}},
        headers=admin_headers,
    )

    assert response.status_code == 422
    for item in items:
        db.refresh(item)
        assert item.year_start == 1878


def test_bulk_nulling_a_required_classifier_is_refused_naming_the_field(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A bulk edit must not 500 where a single edit would 422.

    The same guard as PATCH -- see
    test_nulling_a_required_classifier_is_refused_naming_the_field in
    test_inventory_edit.py.
    """
    items = [build_bare_item(db) for _ in range(2)]

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items], "changes": {"disposition": None}},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "disposition" in response.json()["detail"]


def test_a_bulk_edit_cannot_give_a_note_a_coin_denomination(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """All or nothing: the coin in the same selection keeps its own denomination."""
    coin = build_bare_item(db)
    note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [coin.id, note.id], "changes": {"denomination": "usd_coin_0_25"}},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "denomination" in response.json()["detail"]
    db.refresh(coin)
    assert coin.denomination_id is None


def test_a_bulk_kind_only_edit_that_would_strand_a_denomination_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A bare item_kind edit is checked against the resulting state too.

    `plain_note` would succeed alone -- it carries no denomination -- but the
    batch is all-or-nothing, and `note` in the same selection already
    carries a note denomination that `item_kind: coin` would strand.
    """
    plain_note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))
    note = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        denomination_id=code_id(db, Denomination, "usd_note_1"),
    )

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [plain_note.id, note.id], "changes": {"item_kind": "coin"}},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "denomination" in response.json()["detail"]
    db.refresh(plain_note)
    assert plain_note.item_kind_id == code_id(db, ItemKind, "currency")


def test_a_bulk_kind_only_edit_that_would_strand_a_metal_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Same shape as the denomination case, for the other coin-only field."""
    plain_coin = build_bare_item(db)
    coin = build_bare_item(db, metal_id=code_id(db, Metal, "silver"))

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [plain_coin.id, coin.id], "changes": {"item_kind": "currency"}},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "metal" in response.json()["detail"]
    db.refresh(plain_coin)
    assert plain_coin.item_kind_id == code_id(db, ItemKind, "coin")


def test_a_bulk_combined_kind_and_denomination_edit_to_a_consistent_pair_succeeds(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A note becoming a coin, with a coin denomination in the same request."""
    note = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        denomination_id=code_id(db, Denomination, "usd_note_1"),
    )

    response = client.post(
        "/api/inventory/bulk",
        json={
            "ids": [note.id],
            "changes": {"item_kind": "coin", "denomination": "usd_coin_0_25"},
        },
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    db.refresh(note)
    assert note.item_kind_id == code_id(db, ItemKind, "coin")
    assert note.denomination_id == code_id(db, Denomination, "usd_coin_0_25")


def test_a_bulk_combined_kind_and_metal_edit_to_a_consistent_pair_succeeds(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A coin becoming a note, clearing its metal in the same request."""
    coin = build_bare_item(db, metal_id=code_id(db, Metal, "silver"))

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [coin.id], "changes": {"item_kind": "currency", "metal": None}},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    db.refresh(coin)
    assert coin.item_kind_id == code_id(db, ItemKind, "currency")
    assert coin.metal_id is None


def test_bulk_refuses_an_empty_selection(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Apply to nothing is far more likely a lost selection than an intent."""
    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [], "changes": {"year_start": 1964}},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_an_offer_holding_none_of_the_edited_items_names_every_change(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An offer the edit ends is noted with the edit's changes, not a 500.

    `offers_holding` found the offer through the edited items, so its pieces
    missing from them means a lot read differently; the note still names
    what the edit did.
    """
    monkeypatch.setattr(offering_writes, "offered_items", lambda _db, _listing: [])
    code = inventory._offer_standing_code(
        db, Listing(id=7), {1: "sold", 2: "held", 3: "sold"}
    )
    assert code == "held, sold"
