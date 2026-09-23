"""The item field change log: who changed which field, and when.

The owner's request (2026-09-23): a conflict in the item editor should say
who made the other change. `item_field_change` records each field an edit
actually changes; the item detail and the 409 name the latest change.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.models import ItemFieldChange, User
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def _changes(db: Session, item_id: int) -> list[ItemFieldChange]:
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
    item = make_item(db, source_title="Dime", description="as bought")
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
    item = make_item(db)
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
    first, second = make_item(db), make_item(db)
    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [first.id, second.id], "changes": {"description": "lot of two"}},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    for item in (first, second):
        assert [c.field_name for c in _changes(db, item.id)] == ["description"]


def test_a_conflict_says_who_made_the_other_change(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db, description="as bought")
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
