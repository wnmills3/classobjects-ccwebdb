"""Two people editing one item: a save is merged field by field.

The owner's ruling (2026-09-23): changes made elsewhere should not stop a
save unless they touched a field this save changes. A save sends `base` --
the value each field it changes had when the edit began, as
`GET /inventory/{id}` returned it -- and is refused only for a field someone
else has changed since, naming it with both values.
"""

from __future__ import annotations

from typing import Any

from app.models import InventoryItem
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def _open(
    client: TestClient, headers: dict[str, str], item: InventoryItem
) -> dict[str, Any]:
    """What an editor has in front of it: the item as the editor loads it."""
    response = client.get(f"/api/inventory/{item.id}", headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _save(
    client: TestClient,
    headers: dict[str, str],
    opened: dict[str, Any],
    changes: dict[str, Any],
) -> Response:
    return client.patch(
        f"/api/inventory/{opened['id']}",
        json={
            **changes,
            "version": opened["version"],
            "base": {field: opened[field] for field in changes},
        },
        headers=headers,
    )


def test_a_change_to_another_field_does_not_stop_a_save(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db, source_title="1881-S Morgan", description="as bought")
    mine = _open(client, admin_headers, item)
    theirs = _open(client, admin_headers, item)

    first = _save(client, admin_headers, theirs, {"description": "cleaned, sadly"})
    assert first.status_code == 200, first.text

    # My form is now a version behind -- but I changed a different field.
    second = _save(
        client, admin_headers, mine, {"source_title": "1881-S Morgan Dollar"}
    )
    assert second.status_code == 200, second.text

    db.expire_all()
    stored = db.get(InventoryItem, item.id)
    assert stored is not None
    assert stored.source_title == "1881-S Morgan Dollar"
    assert stored.description == "cleaned, sadly"  # theirs kept, not overwritten


def test_a_change_to_the_same_field_is_refused_naming_both_values(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db, description="as bought")
    mine = _open(client, admin_headers, item)
    theirs = _open(client, admin_headers, item)
    assert (
        _save(client, admin_headers, theirs, {"description": "cleaned"}).status_code
        == 200
    )

    response = _save(client, admin_headers, mine, {"description": "original skin"})
    assert response.status_code == 409, response.text
    body = response.json()
    assert "description" in body["detail"]
    [conflict] = body["conflicts"]
    assert {key: conflict[key] for key in ("field", "was", "theirs", "yours")} == {
        "field": "description",
        "was": "as bought",
        "theirs": "cleaned",
        "yours": "original skin",
    }
    # Who made the other change comes from the change log
    # (test_field_change_log.py covers it).
    assert conflict["changed_by"] == "Test Admin"
    db.expire_all()
    stored = db.get(InventoryItem, item.id)
    assert stored is not None
    assert stored.description == "cleaned"


def test_the_same_change_made_twice_is_no_conflict(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db, description="as bought")
    mine = _open(client, admin_headers, item)
    theirs = _open(client, admin_headers, item)
    assert (
        _save(client, admin_headers, theirs, {"description": "cleaned"}).status_code
        == 200
    )
    response = _save(client, admin_headers, mine, {"description": "cleaned"})
    assert response.status_code == 200, response.text


def test_numbers_compare_as_numbers_not_text(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A cost typed "84" is not a change from a stored "84.00"."""
    item = make_item(db)
    mine = _open(client, admin_headers, item)
    base_cost = mine["item_cost"]
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={
            "item_cost": "99.00",
            "version": mine["version"],
            "base": {"item_cost": str(float(base_cost)).rstrip("0").rstrip(".")},
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text


def test_base_must_cover_every_field_sent(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A field with no base would be saved unchecked -- refused instead."""
    item = make_item(db)
    opened = _open(client, admin_headers, item)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={
            "description": "x",
            "source_title": "y",
            "version": opened["version"],
            "base": {"description": opened["description"]},
        },
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "source_title" in response.json()["detail"]


def test_without_a_base_a_stale_version_is_still_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Scripts and older callers keep the whole-item version check."""
    item = make_item(db)
    opened = _open(client, admin_headers, item)
    first = client.patch(
        f"/api/inventory/{item.id}",
        json={"description": "a", "version": opened["version"]},
        headers=admin_headers,
    )
    assert first.status_code == 200, first.text
    second = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "b", "version": opened["version"]},
        headers=admin_headers,
    )
    assert second.status_code == 409, second.text
