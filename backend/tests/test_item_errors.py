"""GET/PUT /inventory/{item_id}/errors -- replacing an item's whole error set.

An item may carry more than one mint or printing error -- miscut and
overprint are routinely found on the same bill -- which is exactly what a
single `error_type_id` foreign key could not express. These tests exercise
the join table end to end: two errors on one item, the unique constraint that
refuses the same one twice, and the replace semantics of the write endpoint.
"""

from __future__ import annotations

from app.models import ErrorType, InventoryItem, ItemError, User
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item


def _errors_of(db: Session, item: InventoryItem) -> set[str]:
    db.expire_all()
    return {
        row.error_type_id
        for row in db.query(ItemError)
        .filter(ItemError.inventory_item_id == item.id)
        .all()
    }


# ---------------------------------------------------------------------------
# GET /inventory/{item_id}/errors
# ---------------------------------------------------------------------------


def test_get_errors_is_empty_for_an_unremarkable_item(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    resp = client.get(f"/api/inventory/{item.id}/errors", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"inventory_item_id": item.id, "errors": []}


def test_get_errors_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    resp = client.get(f"/api/inventory/{item.id}/errors", headers=customer_headers)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /inventory/{item_id}/errors -- replacing the whole set
# ---------------------------------------------------------------------------


def test_two_errors_round_trip(
    client: TestClient,
    admin_headers: dict[str, str],
    admin_user: User,
    db: Session,
) -> None:
    """Miscut and overprint on the same bill: both survive, each with its own note."""
    item = make_item(db)

    resp = client.put(
        f"/api/inventory/{item.id}/errors",
        json={
            "errors": [
                {"error_type": "doubled_die", "details": "strong doubling on LIBERTY"},
                {"error_type": "off_center", "details": "struck 10% off center"},
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["inventory_item_id"] == item.id
    by_type = {e["error_type"]: e for e in body["errors"]}
    assert set(by_type) == {"doubled_die", "off_center"}
    assert by_type["doubled_die"]["details"] == "strong doubling on LIBERTY"
    assert by_type["off_center"]["details"] == "struck 10% off center"
    assert all(e["source"] == "manual" for e in body["errors"])
    assert all(e["noted_by_id"] == admin_user.id for e in body["errors"])
    assert all(e["noted_at"] for e in body["errors"])

    # And it is what is actually in the database, not just what the response
    # claims.
    stored = _errors_of(db, item)
    assert stored == {
        code_id(db, ErrorType, "doubled_die"),
        code_id(db, ErrorType, "off_center"),
    }

    # A second GET sees the same set.
    got = client.get(f"/api/inventory/{item.id}/errors", headers=admin_headers)
    assert {e["error_type"] for e in got.json()["errors"]} == {
        "doubled_die",
        "off_center",
    }


def test_put_replaces_rather_than_adds(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A second PUT with a different set discards the first, entirely."""
    item = make_item(db)
    client.put(
        f"/api/inventory/{item.id}/errors",
        json={"errors": [{"error_type": "doubled_die"}]},
        headers=admin_headers,
    )

    resp = client.put(
        f"/api/inventory/{item.id}/errors",
        json={"errors": [{"error_type": "off_center"}]},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert {e["error_type"] for e in resp.json()["errors"]} == {"off_center"}
    assert _errors_of(db, item) == {code_id(db, ErrorType, "off_center")}


def test_put_with_an_empty_list_clears_every_error(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    client.put(
        f"/api/inventory/{item.id}/errors",
        json={"errors": [{"error_type": "doubled_die"}]},
        headers=admin_headers,
    )

    resp = client.put(
        f"/api/inventory/{item.id}/errors",
        json={"errors": []},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["errors"] == []
    assert _errors_of(db, item) == set()


def test_an_unknown_error_type_is_422(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    resp = client.put(
        f"/api/inventory/{item.id}/errors",
        json={"errors": [{"error_type": "not_a_real_error"}]},
        headers=admin_headers,
    )
    assert resp.status_code == 422, resp.text


def test_the_same_error_type_twice_in_one_request_is_422(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The same error twice is one fact, not two -- caught before it reaches SQL."""
    item = make_item(db)
    resp = client.put(
        f"/api/inventory/{item.id}/errors",
        json={
            "errors": [
                {"error_type": "doubled_die", "details": "a"},
                {"error_type": "doubled_die", "details": "b"},
            ]
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422, resp.text


def test_set_errors_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    resp = client.put(
        f"/api/inventory/{item.id}/errors",
        json={"errors": []},
        headers=customer_headers,
    )
    assert resp.status_code == 403


def test_unknown_item_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    resp = client.put(
        "/api/inventory/999999/errors",
        json={"errors": []},
        headers=admin_headers,
    )
    assert resp.status_code == 404
