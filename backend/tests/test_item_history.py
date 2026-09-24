"""An item's history: field edits, status moves and location moves, merged.

`GET /api/inventory/{id}/history` reads the three logs (`app.item_history`)
and answers newest first, with classifier codes shown by their labels.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.lifecycle_writes import record_initial_status, set_location, set_status
from app.models import (
    Country,
    ItemAttribute,
    ItemFieldChange,
    ItemStatus,
    ItemStatusHistory,
    LocationHistory,
    StorageLocation,
    StorageLocationKind,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item


def _history(
    client: TestClient, headers: dict[str, str], item_id: int
) -> list[dict[str, Any]]:
    response = client.get(f"/api/inventory/{item_id}/history", headers=headers)
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def _label(db: Session, model: type[Country] | type[ItemAttribute], code: str) -> str:
    return db.execute(select(model.label).where(model.code == code)).scalar_one()


def _location(db: Session, institution: str, identifier: str) -> StorageLocation:
    location = StorageLocation(
        storage_location_kind_id=code_id(db, StorageLocationKind, "safe_deposit_box"),
        institution=institution,
        identifier=identifier,
    )
    db.add(location)
    db.flush()
    return location


def test_the_three_logs_are_merged_newest_first(
    client: TestClient, admin_headers: dict[str, str], admin_user: User, db: Session
) -> None:
    item = make_item(db, status_id=code_id(db, ItemStatus, "ordered"))
    record_initial_status(db, item, user_id=admin_user.id, note="imported")
    set_status(
        db,
        item,
        code_id(db, ItemStatus, "received"),
        user_id=admin_user.id,
        arrived_on=date(2026, 9, 18),
    )
    set_location(db, item, _location(db, "First Bank", "Box 12").id)
    set_location(db, item, _location(db, "Second Bank", "Box 3").id)
    db.flush()
    # Explicit, a minute apart, and in the past: the clock may give rows
    # written together one timestamp, and the edit below must be newest.
    start = datetime.now(UTC) - timedelta(hours=1)
    rows = [
        *db.scalars(
            select(ItemStatusHistory)
            .where(ItemStatusHistory.inventory_item_id == item.id)
            .order_by(ItemStatusHistory.id)
        ),
        *db.scalars(
            select(LocationHistory)
            .where(LocationHistory.inventory_item_id == item.id)
            .order_by(LocationHistory.id)
        ),
    ]
    for n, row in enumerate(rows):
        when = start + timedelta(minutes=n)
        if isinstance(row, ItemStatusHistory):
            row.changed_at = when
        else:
            row.moved_at = when
    db.commit()
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"description": "cleaned", "country": "MX"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    events = _history(client, admin_headers, item.id)
    assert [(e["kind"], e["field"]) for e in events] == [
        ("field", "country"),
        ("field", "description"),
        ("location", "location"),
        ("location", "location"),
        ("status", "status"),
        ("status", "status"),
    ]
    country, description, moved, placed, received, opened = events
    # A classifier is shown by its label, not the code the log holds.
    assert (country["old_value"], country["new_value"]) == (
        _label(db, Country, "US"),
        _label(db, Country, "MX"),
    )
    assert description["new_value"] == "cleaned"
    assert description["by"] == "Test Admin"
    # A move says where the item came from: the previous move's destination.
    assert (moved["old_value"], moved["new_value"]) == (
        "First Bank Box 12",
        "Second Bank Box 3",
    )
    assert (placed["old_value"], placed["new_value"]) == (None, "First Bank Box 12")
    assert placed["by"] is None
    assert (received["old_value"], received["new_value"]) == ("Ordered", "Received")
    assert received["arrived_on"] == "2026-09-18"
    assert (opened["old_value"], opened["new_value"], opened["note"]) == (
        None,
        "Ordered",
        "imported",
    )


def test_attributes_are_shown_by_label_and_a_vanished_code_as_logged(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    at = datetime.now(UTC)
    db.add_all(
        [
            ItemFieldChange(
                inventory_item_id=item.id,
                field_name="attributes",
                old_value=[],
                new_value=["first_strike"],
                changed_at=at,
            ),
            # A code merged away since it was logged: shown, not dropped.
            ItemFieldChange(
                inventory_item_id=item.id,
                field_name="denomination",
                old_value="no_such_denomination",
                new_value=None,
                changed_at=at - timedelta(minutes=1),
            ),
        ]
    )
    db.commit()

    attributes, denomination = _history(client, admin_headers, item.id)
    assert attributes["new_value"] == [_label(db, ItemAttribute, "first_strike")]
    assert attributes["by"] is None
    assert denomination["old_value"] == "no_such_denomination"


def test_another_item_s_history_is_not_shown(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    mine, other = make_item(db), make_item(db)
    client.patch(
        f"/api/inventory/{other.id}", json={"description": "x"}, headers=admin_headers
    )
    assert _history(client, admin_headers, mine.id) == []


def test_history_is_admin_only_and_404s_for_a_missing_item(
    client: TestClient,
    admin_headers: dict[str, str],
    customer_headers: dict[str, str],
    db: Session,
) -> None:
    item = make_item(db)
    url = f"/api/inventory/{item.id}/history"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=customer_headers).status_code == 403
    missing = client.get("/api/inventory/999999999/history", headers=admin_headers)
    assert missing.status_code == 404
