"""Setting one field across many items at once.

All-or-nothing in one transaction. A partial bulk edit across 50 coins leaves
a state nobody can describe, and "which of the 50 applied?" is not a question
the UI should ever have to answer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app import offering_writes
from app.models import (
    Denomination,
    ItemFieldChange,
    ItemKind,
    Listing,
    ListingFormat,
    LocationHistory,
    Metal,
    SalesVenue,
    StorageLocation,
    StorageLocationKind,
    User,
)
from app.routers import inventory
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id
from tests.conftest import build_item


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
    """The good field sent beside the bad code is not applied either."""
    items = [build_bare_item(db, year_start=1878) for _ in range(3)]

    response = client.post(
        "/api/inventory/bulk",
        json={
            "ids": [i.id for i in items],
            "changes": {"year_start": 1964, "grade": "NOT_A_GRADE"},
        },
        headers=admin_headers,
    )

    assert response.status_code == 422
    db.expire_all()
    for item in items:
        assert item.year_start == 1878, "a rejected bulk edit must apply nothing"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mint", "S"),
        ("variety", "VAM-3"),
        ("cert_numbers", ["9900001"]),
        ("base", {"year_start": 1878}),
    ],
)
def test_a_field_a_bulk_edit_cannot_set_is_refused_by_name(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    field: str,
    value: object,
) -> None:
    """Refused, not dropped: a 200 would report a change that was never made.

    A coin's mint and variety, an item's certificates and the merge's `base`
    are a single edit's; nothing in a bulk edit writes them.
    """
    item = build_bare_item(db, year_start=1878)

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [item.id], "changes": {"year_start": 1964, field: value}},
        headers=admin_headers,
    )

    assert response.status_code == 422, response.text
    assert field in response.json()["detail"]
    db.expire_all()
    assert item.year_start == 1878, "nothing sent with the refused field is applied"


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


# --------------------------------------------------------------------------
# Where the items are kept
# --------------------------------------------------------------------------


def _box(db: Session, identifier: str) -> StorageLocation:
    """A safe-deposit box of this number, committed."""
    box = StorageLocation(
        storage_location_kind_id=code_id(db, StorageLocationKind, "safe_deposit_box"),
        institution="First National",
        identifier=identifier,
    )
    db.add(box)
    db.commit()
    return box


def _moves(
    db: Session, item_id: int
) -> list[tuple[int | None, int | None, str | None]]:
    """An item's location history: where to, who moved it, the note; oldest first."""
    rows = db.execute(
        select(
            LocationHistory.storage_location_id,
            LocationHistory.moved_by_id,
            LocationHistory.note,
        )
        .where(LocationHistory.inventory_item_id == item_id)
        .order_by(LocationHistory.id)
    ).all()
    return [tuple(row) for row in rows]


def test_a_location_is_set_across_every_selected_item_and_each_move_recorded(
    client: TestClient, admin_headers: dict[str, str], db: Session, admin_user: User
) -> None:
    box = _box(db, "804")
    chosen = [build_bare_item(db) for _ in range(3)]
    other = build_bare_item(db)

    response = client.post(
        "/api/inventory/bulk",
        json={
            "ids": [item.id for item in chosen],
            "changes": {"storage_location_id": box.id},
        },
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"updated": 3}
    db.expire_all()
    assert [item.storage_location_id for item in chosen] == [box.id] * 3
    assert other.storage_location_id is None
    # One move each, under the person who made it; none for the unselected.
    for item in chosen:
        assert _moves(db, item.id) == [(box.id, admin_user.id, "edited in the console")]
    assert _moves(db, other.id) == []


def test_an_item_already_there_is_not_moved_again(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    box = _box(db, "804")
    there = build_bare_item(db, storage_location_id=box.id)
    elsewhere = build_bare_item(db)

    client.post(
        "/api/inventory/bulk",
        json={
            "ids": [there.id, elsewhere.id],
            "changes": {"storage_location_id": box.id},
        },
        headers=admin_headers,
    )

    assert _moves(db, there.id) == []
    assert len(_moves(db, elsewhere.id)) == 1


def test_a_null_location_clears_where_each_item_is_kept(
    client: TestClient, admin_headers: dict[str, str], db: Session, admin_user: User
) -> None:
    box = _box(db, "804")
    items = [build_bare_item(db, storage_location_id=box.id) for _ in range(2)]

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items], "changes": {"storage_location_id": None}},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert [item.storage_location_id for item in items] == [None, None]
    assert _moves(db, items[0].id) == [(None, admin_user.id, "edited in the console")]


def test_a_location_that_does_not_exist_moves_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    box = _box(db, "804")
    items = [build_bare_item(db, storage_location_id=box.id, year_start=1878)]

    response = client.post(
        "/api/inventory/bulk",
        json={
            "ids": [items[0].id],
            "changes": {"storage_location_id": box.id + 999, "year_start": 1964},
        },
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == f"Unknown storage_location_id: {box.id + 999}"
    db.expire_all()
    # All or nothing: the year sent with it is not set either.
    assert (items[0].storage_location_id, items[0].year_start) == (box.id, 1878)
    assert _moves(db, items[0].id) == []


def test_a_move_is_kept_in_the_location_history_and_not_in_the_field_log(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    box = _box(db, "804")
    item = build_bare_item(db, year_start=1878)

    client.post(
        "/api/inventory/bulk",
        json={
            "ids": [item.id],
            "changes": {"storage_location_id": box.id, "year_start": 1964},
        },
        headers=admin_headers,
    )

    logged = db.scalars(
        select(ItemFieldChange.field_name).where(
            ItemFieldChange.inventory_item_id == item.id
        )
    ).all()
    assert "storage_location_id" not in logged
    assert "year_start" in logged
    assert len(_moves(db, item.id)) == 1


def test_moving_an_item_that_is_for_sale_asks_for_no_acknowledgement(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Where a coin is kept is not shown to a buyer: the sale warning is not for it."""
    box = _box(db, "804")
    item = build_item(db)
    venue = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).one()
    offering_writes.offer(
        db,
        item=item,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("50.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()
    # It is for sale: any other change is refused until acknowledged.
    refused = client.post(
        "/api/inventory/bulk",
        json={"ids": [item.id], "changes": {"year_start": 1964}},
        headers=admin_headers,
    )
    assert refused.status_code == 409, refused.text

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [item.id], "changes": {"storage_location_id": box.id}},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert len(_moves(db, item.id)) == 1
