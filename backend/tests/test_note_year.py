"""A note holds no year of its own: its series year is its year.

Owner, 2026-09-25: the item's years exist for coins and for lots of mixed
years (a tube of Morgans). Storing a note's series year a second time only
let the two disagree, so a note's years stay empty, a year sent for one is
refused by name, and what searches a note by year reads its series year.
"""

from __future__ import annotations

from app.models import InventoryItem, PurchaseOrder
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_purchase_order


def _order(db: Session) -> PurchaseOrder:
    return build_purchase_order(db, vendor_name="Note Year Vendor")


def _create(
    client: TestClient, headers: dict[str, str], order: PurchaseOrder, **fields: object
) -> dict[str, object]:
    body = {"purchase_order_id": order.id, "source_title": "t", **fields}
    response = client.post("/api/inventory", json=body, headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, object] = response.json()
    return created


def _years(db: Session, item_id: object) -> tuple[int | None, int | None]:
    db.expire_all()
    item = db.get(InventoryItem, item_id)
    assert item is not None
    return item.year_start, item.year_end


def test_a_note_entered_holds_its_series_year_and_no_other(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order(db)
    note = _create(client, admin_headers, order, item_kind="currency", series_year=1934)
    assert (note["year_start"], note["year_end"], note["series_year"]) == (
        None,
        None,
        1934,
    )
    # CC-007663's case: a Year typed for a note is refused, by name.
    refused = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "source_title": "t",
            "item_kind": "currency",
            "series_year": 1934,
            "year_start": 1935,
        },
        headers=admin_headers,
    )
    assert refused.status_code == 422
    assert "year_start" in refused.text


def test_a_coin_keeps_its_own_year(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = _create(client, admin_headers, _order(db), item_kind="coin", year_start=1881)
    assert (coin["year_start"], coin["year_end"]) == (1881, 1881)


def test_a_notes_year_cannot_be_set_and_its_series_year_leaves_it_empty(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = _create(
        client, admin_headers, _order(db), item_kind="currency", series_year=1952
    )
    changed = client.patch(
        f"/api/inventory/{note['id']}",
        json={"series_year": 2017},
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    assert _years(db, note["id"]) == (None, None)

    refused = client.patch(
        f"/api/inventory/{note['id']}",
        json={"year_start": 2017},
        headers=admin_headers,
    )
    assert refused.status_code == 422
    assert "series_year instead" in refused.text


def test_a_coin_turned_note_gives_up_its_year(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = _create(client, admin_headers, _order(db), item_kind="coin", year_start=1935)
    turned = client.patch(
        f"/api/inventory/{coin['id']}",
        json={"item_kind": "currency"},
        headers=admin_headers,
    )
    assert turned.status_code == 200, turned.text
    assert _years(db, coin["id"]) == (None, None)


def test_a_bulk_year_is_refused_for_notes(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order(db)
    note = _create(client, admin_headers, order, item_kind="currency", series_year=1950)
    refused = client.post(
        "/api/inventory/bulk",
        json={"ids": [note["id"]], "changes": {"year_start": 1963}},
        headers=admin_headers,
    )
    assert refused.status_code == 422
    assert str(note["item_code"]) in refused.text
    moved = client.post(
        "/api/inventory/bulk",
        json={"ids": [note["id"]], "changes": {"series_year": 1963}},
        headers=admin_headers,
    )
    assert moved.status_code == 200, moved.text
    assert _years(db, note["id"]) == (None, None)


def test_the_currency_view_finds_a_note_by_its_series_year(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order(db)
    note = _create(client, admin_headers, order, item_kind="currency", series_year=1899)
    found = client.get(
        "/api/inventory/currency/search",
        params={"year_min": 1899, "year_max": 1899},
        headers=admin_headers,
    )
    assert found.status_code == 200, found.text
    codes = [row["item_code"] for row in found.json()["rows"]]
    assert note["item_code"] in codes
    no_year = client.get(
        "/api/inventory/currency/search",
        params={"issue": "no_year", "item_code": note["item_code"]},
        headers=admin_headers,
    )
    assert no_year.status_code == 200, no_year.text
    assert no_year.json()["rows"] == []
