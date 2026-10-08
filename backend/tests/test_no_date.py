"""A piece with no date at all, as distinct from a year not recorded.

A gold bar carries no year; an empty year says only "not recorded", which
would put every bar in "No year recorded" beside coins whose date was never
typed. `no_date` says the piece has none.
"""

from __future__ import annotations

import pytest
from app.models import InventoryItem, ItemKind
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, build_purchase_order, code_id


def _bar(db: Session, **overrides: object) -> InventoryItem:
    """A bullion item with no year recorded, the overrides applied."""
    columns: dict[str, object] = {
        "item_kind_id": code_id(db, ItemKind, "bullion"),
        "year_start": None,
    }
    columns.update(overrides)
    return build_bare_item(db, **columns)


def _patch(
    client: TestClient, headers: dict[str, str], item_id: int, body: dict[str, object]
) -> Response:
    """The response to saving these fields on an item."""
    return client.patch(f"/api/inventory/{item_id}", json=body, headers=headers)


def test_marking_no_date_clears_the_years_and_is_shown(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _bar(db, year_start=2020, year_end=2020)

    resp = _patch(client, admin_headers, item.id, {"no_date": True})

    assert resp.status_code == 200, resp.text
    db.refresh(item)
    assert (item.no_date, item.year_start, item.year_end) == (True, None, None)
    detail = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert detail["no_date"] is True


def test_a_year_sent_later_dates_the_piece_again(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _bar(db, no_date=True)

    resp = _patch(client, admin_headers, item.id, {"year_start": 2021})

    assert resp.status_code == 200, resp.text
    db.refresh(item)
    assert (item.no_date, item.year_start) == (False, 2021)


def test_no_date_beside_a_year_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _bar(db)

    resp = _patch(client, admin_headers, item.id, {"no_date": True, "year_start": 2021})

    assert resp.status_code == 422, resp.text
    db.refresh(item)
    assert (item.no_date, item.year_start) == (False, None)


def test_a_note_cannot_be_marked_no_date(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = build_bare_item(
        db, item_kind_id=code_id(db, ItemKind, "currency"), year_start=None
    )
    resp = _patch(client, admin_headers, note.id, {"no_date": True})
    assert resp.status_code == 422, resp.text


def test_an_unrelated_edit_keeps_no_date(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _bar(db, no_date=True)
    resp = _patch(client, admin_headers, item.id, {"source_title": "5 g Valcambi bar"})
    assert resp.status_code == 200, resp.text
    db.refresh(item)
    assert item.no_date is True


def test_a_new_item_can_be_entered_with_no_date(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = build_purchase_order(db, vendor_name="No Date Test Vendor")
    body = {
        "purchase_order_id": order.id,
        "item_kind": "bullion",
        "source_title": "5 g Valcambi gold bar",
        "item_cost": "500.00",
        "shipping_cost": "0.00",
        "no_date": True,
    }
    created = client.post("/api/inventory", json=body, headers=admin_headers)
    assert created.status_code == 201, created.text
    item = db.get(InventoryItem, created.json()["id"])
    assert item is not None and item.no_date is True

    refused = client.post(
        "/api/inventory", json={**body, "year_start": 2020}, headers=admin_headers
    )
    assert refused.status_code == 422, refused.text


def test_no_year_recorded_does_not_list_a_piece_with_no_date(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    undated = _bar(db, no_date=True)
    unrecorded = _bar(db)

    rows = client.get(
        "/api/inventory/coins/search?issue=no_year", headers=admin_headers
    ).json()["rows"]
    ids = {row["id"] for row in rows}
    assert unrecorded.id in ids
    assert undated.id not in ids

    missing = client.get(
        "/api/inventory/coins/search?missing=year", headers=admin_headers
    ).json()["rows"]
    assert {row["id"] for row in missing} >= {unrecorded.id}
    assert undated.id not in {row["id"] for row in missing}


def test_bulk_edit_refuses_no_date_by_name(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _bar(db)
    resp = client.post(
        "/api/inventory/bulk",
        json={"ids": [item.id], "changes": {"no_date": True}},
        headers=admin_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "no_date" in resp.json()["detail"]


def test_bulk_setting_a_year_dates_a_piece_marked_no_date(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _bar(db, no_date=True)
    resp = client.post(
        "/api/inventory/bulk",
        json={"ids": [item.id], "changes": {"year_start": 2019}},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    db.refresh(item)
    assert (item.no_date, item.year_start) == (False, 2019)


def test_a_piece_turned_into_a_note_loses_no_date_in_bulk_as_in_a_single_edit(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A note never holds the flag: its year is its series year."""
    single = _bar(db, no_date=True)
    in_bulk = _bar(db, no_date=True)

    one = _patch(client, admin_headers, single.id, {"item_kind": "currency"})
    assert one.status_code == 200, one.text
    many = client.post(
        "/api/inventory/bulk",
        json={"ids": [in_bulk.id], "changes": {"item_kind": "currency"}},
        headers=admin_headers,
    )
    assert many.status_code == 200, many.text

    db.expire_all()
    for item in (single, in_bulk):
        assert item.item_kind.code == "currency"
        assert (item.no_date, item.year_start, item.year_end) == (False, None, None)


def test_the_database_refuses_no_date_with_a_year(db: Session) -> None:
    """The constraint holds even for a writer that bypasses the API."""
    with pytest.raises(IntegrityError):
        _bar(db, no_date=True, year_start=2020)
    db.rollback()
