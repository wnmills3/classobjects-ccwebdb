"""The editor's suggestion reads the screen: unsaved changes included.

`POST /api/inventory/{id}/suggested-description` takes what Save would send
and describes the item as if it were saved, writing nothing (owner,
2026-10-01: a suggestion that waited for Save got in the way). Each test
checks the on-screen wording against the wording after really saving the
same change, so the two can never describe one item differently.
"""

from __future__ import annotations

import pytest
from app.models import AppliesTo, ErrorType, InventoryItem, ItemFieldChange
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.builders import build_purchase_order

COIN = {
    "item_kind": "coin",
    "year_start": 1881,
    "country": "US",
    "denomination": "usd_coin_1_00",
    "series": "morgan_dollar",
    "grade": "MS64",
    "mint": "S",
    "grading_service": "PCGS",
}
NOTE = {
    "item_kind": "currency",
    "denomination": "usd_note_1",
    "grade": "N64",
    "series_year": 1957,
    "series_letter": "B",
    "note_type": "silver_certificate",
    "seal_color": "blue",
    "serial_number": "A31415926B",
}


def _create(
    client: TestClient, headers: dict[str, str], db: Session, fields: dict
) -> int:
    order = build_purchase_order(db, vendor_name="On Screen Test Vendor")
    created = client.post(
        "/api/inventory",
        json={**fields, "purchase_order_id": order.id, "source_title": "t"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return int(created.json()["id"])


def _on_screen(
    client: TestClient, headers: dict[str, str], item_id: int, body: dict
) -> str:
    resp = client.post(
        f"/api/inventory/{item_id}/suggested-description", json=body, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["description"])


def _saved(client: TestClient, headers: dict[str, str], item_id: int) -> str:
    resp = client.get(
        f"/api/inventory/{item_id}/suggested-description", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["description"])


def _error_code(db: Session, side: AppliesTo) -> str:
    return db.scalars(
        select(ErrorType.code)
        .where(ErrorType.applies_to.in_([side, AppliesTo.any]))
        .order_by(ErrorType.id)
    ).first() or pytest.fail(f"no {side.value} error type seeded")


@pytest.mark.parametrize(
    ("start", "changes"),
    [
        (COIN, {"grade": "MS65", "year_start": 1884, "year_end": 1884, "mint": "O"}),
        (COIN, {"piece_count": 3, "grading_service": None}),
        # An overmintmark typed into Variety (owner, 2026-10-02).
        (COIN, {"variety": "O/S"}),
        (NOTE, {"serial_number": "B27182818C", "series_letter": "A", "grade": "N66"}),
        (NOTE, {"seal_color": None}),
    ],
)
def test_unsaved_changes_read_as_the_same_changes_saved(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    start: dict,
    changes: dict,
) -> None:
    item_id = _create(client, admin_headers, db, start)

    on_screen = _on_screen(client, admin_headers, item_id, {"changes": changes})
    before_save = _saved(client, admin_headers, item_id)
    saved = client.patch(
        f"/api/inventory/{item_id}", json=changes, headers=admin_headers
    )
    assert saved.status_code == 200, saved.text

    assert on_screen == _saved(client, admin_headers, item_id)
    assert on_screen != before_save, "the change must show in the wording"


def test_the_defaults_a_save_fills_are_in_the_suggestion(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The owner's case: "Silver" alone, where the save gave "0.77344 ozt fine".

    A coin entered with no denomination has no composition to fill from;
    naming the denomination on screen is what a save fills the metal and
    weights from, and the suggestion has to see that fill too.
    """
    start = {k: v for k, v in COIN.items() if k not in {"denomination", "series"}}
    start["year_start"] = 1900
    start["mint"] = "O"
    item_id = _create(client, admin_headers, db, start)
    changes = {"denomination": "usd_coin_1_00", "series": "morgan_dollar"}

    on_screen = _on_screen(client, admin_headers, item_id, {"changes": changes})
    saved = client.patch(
        f"/api/inventory/{item_id}", json=changes, headers=admin_headers
    )
    assert saved.status_code == 200, saved.text

    assert "ozt fine" in on_screen
    assert on_screen == _saved(client, admin_headers, item_id)


def test_unsaved_errors_and_attributes_read_as_saved(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _create(client, admin_headers, db, NOTE)
    errors = [{"error_type": _error_code(db, AppliesTo.currency), "details": "x"}]

    on_screen = _on_screen(client, admin_headers, item_id, {"errors": errors})
    put = client.put(
        f"/api/inventory/{item_id}/errors",
        json={"errors": errors},
        headers=admin_headers,
    )
    assert put.status_code == 200, put.text
    assert on_screen == _saved(client, admin_headers, item_id)


def test_with_nothing_unsaved_it_reads_as_the_saved_record(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _create(client, admin_headers, db, COIN)
    assert _on_screen(client, admin_headers, item_id, {}) == _saved(
        client, admin_headers, item_id
    )


def test_a_suggestion_from_the_screen_writes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _create(client, admin_headers, db, COIN)
    item = db.get(InventoryItem, item_id)
    assert item is not None
    version, grade_id = item.version, item.grade_id
    changes_before = db.scalar(select(func.count()).select_from(ItemFieldChange))

    _on_screen(
        client,
        admin_headers,
        item_id,
        {"changes": {"grade": "MS67", "item_kind": "currency", "serial_number": "X1"}},
    )

    db.expire_all()
    item = db.get(InventoryItem, item_id)
    assert item is not None
    assert (item.version, item.grade_id) == (version, grade_id)
    assert item.currency_detail is None
    assert (
        db.scalar(select(func.count()).select_from(ItemFieldChange)) == changes_before
    )


def test_an_unknown_code_on_screen_is_named(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _create(client, admin_headers, db, COIN)
    resp = client.post(
        f"/api/inventory/{item_id}/suggested-description",
        json={"changes": {"grade": "NOT_A_GRADE"}},
        headers=admin_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "grade" in resp.json()["detail"]
