"""`POST /api/inventory/{id}/preview`: the item as Save would leave it.

The editor shows what the facts decide while they are typed, by the rules a
save applies, and nothing is written. See docs/specs/entry-panels-design.md.
"""

from __future__ import annotations

from app.models import CurrencyDetail, InventoryItem, ItemFieldChange
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.builders import build_purchase_order


def _new(client: TestClient, headers: dict[str, str], db: Session, kind: str) -> int:
    """The id of an item of this kind, entered through the API with nothing else."""
    order = build_purchase_order(db, vendor_name="Preview Vendor", order_number="P-1")
    made = client.post(
        "/api/inventory",
        json={"purchase_order_id": order.id, "item_kind": kind, "source_title": "t"},
        headers=headers,
    )
    assert made.status_code == 201, made.text
    return int(made.json()["id"])


def test_a_notes_facts_fill_its_class_seal_and_signatures(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _new(client, admin_headers, db, "currency")
    before = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    assert before["note_type"] is None

    shown = client.post(
        f"/api/inventory/{item_id}/preview",
        json={
            "changes": {
                "denomination": "usd_note_1",
                "series_year": 1957,
                "series_letter": "B",
            }
        },
        headers=admin_headers,
    )

    assert shown.status_code == 200, shown.text
    body = shown.json()
    # What was sent, and what follows from it.
    assert (body["denomination"], body["series_year"]) == ("usd_note_1", 1957)
    assert body["note_type"] == "silver_certificate"
    assert body["seal_color"] == "blue"
    assert body["signature_combination"]
    # Marked as a rule's, so the editor can say "suggested".
    assert "note_type_id" in body["derived"]


def test_a_coins_facts_fill_its_metal_and_series(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _new(client, admin_headers, db, "coin")

    body = client.post(
        f"/api/inventory/{item_id}/preview",
        json={
            "changes": {
                "denomination": "usd_coin_1_00",
                "country": "US",
                "year_start": 1881,
                "year_end": 1881,
            }
        },
        headers=admin_headers,
    ).json()

    assert body["metal"] == "silver"
    assert body["series"] == "morgan_dollar"


def test_a_preview_writes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _new(client, admin_headers, db, "currency")
    version = db.get_one(InventoryItem, item_id).version
    logged = db.scalar(select(func.count()).select_from(ItemFieldChange))

    for _ in range(2):
        shown = client.post(
            f"/api/inventory/{item_id}/preview",
            json={"changes": {"denomination": "usd_note_1", "series_year": 1957}},
            headers=admin_headers,
        )
        assert shown.status_code == 200, shown.text

    db.expire_all()
    item = db.get_one(InventoryItem, item_id)
    assert item.version == version
    assert item.denomination_id is None
    detail = db.scalar(
        select(CurrencyDetail).where(CurrencyDetail.inventory_item_id == item_id)
    )
    assert detail is not None
    assert detail.series_year is None
    assert detail.note_type_id is None
    assert db.scalar(select(func.count()).select_from(ItemFieldChange)) == logged
    # And the saved record is still what a read returns.
    read = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    assert (read["denomination"], read["note_type"]) == (None, None)


def test_a_change_save_would_refuse_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _new(client, admin_headers, db, "currency")

    bad = client.post(
        f"/api/inventory/{item_id}/preview",
        json={"changes": {"denomination": "no_such_denomination"}},
        headers=admin_headers,
    )

    assert bad.status_code == 422
    assert "denomination" in bad.text


def test_a_preview_is_staff_only(client: TestClient) -> None:
    assert client.post("/api/inventory/1/preview", json={}).status_code == 401
