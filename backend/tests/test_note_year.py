"""A note has one year: its series year (owner, 2026-09-24).

The item's year follows the series year on every path that sets it --
entry, the editor and bulk edit -- so search, sorting and titles read the
year the note is catalogued by, and there is no second box to type it in.
"""

from __future__ import annotations

from app.models import InventoryItem, PurchaseOrder, Vendor
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _order(db: Session) -> PurchaseOrder:
    vendor = Vendor(name="Note Year Vendor")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id)
    db.add(order)
    db.commit()
    return order


def _create(
    client: TestClient, headers: dict[str, str], order: PurchaseOrder, **fields: object
) -> dict[str, object]:
    body = {"purchase_order_id": order.id, "source_title": "t", **fields}
    response = client.post("/api/inventory", json=body, headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, object] = response.json()
    return created


def test_a_note_entered_takes_its_series_year_as_its_year(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """CC-007663's case: 1935 typed as the Year of a Series 1934 note."""
    note = _create(
        client,
        admin_headers,
        _order(db),
        item_kind="currency",
        series_year=1934,
        year_start=1935,
    )
    assert (note["year_start"], note["year_end"], note["series_year"]) == (
        1934,
        1934,
        1934,
    )


def test_a_coin_keeps_its_own_year(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = _create(client, admin_headers, _order(db), item_kind="coin", year_start=1881)
    assert (coin["year_start"], coin["year_end"]) == (1881, 1881)


def test_editing_a_notes_series_year_moves_its_year(
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
    db.expire_all()
    item = db.get(InventoryItem, note["id"])
    assert item is not None
    assert (item.year_start, item.year_end) == (2017, 2017)

    cleared = client.patch(
        f"/api/inventory/{note['id']}",
        json={"series_year": None},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    db.expire_all()
    item = db.get(InventoryItem, note["id"])
    assert item is not None
    assert (item.year_start, item.year_end) == (None, None)


def test_a_bulk_series_year_moves_each_notes_year_and_no_coins(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order(db)
    notes = [
        _create(client, admin_headers, order, item_kind="currency", series_year=1950)
        for _ in range(2)
    ]
    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [n["id"] for n in notes], "changes": {"series_year": 1963}},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    db.expire_all()
    for note in notes:
        item = db.get(InventoryItem, note["id"])
        assert item is not None
        assert (item.year_start, item.year_end) == (1963, 1963)
