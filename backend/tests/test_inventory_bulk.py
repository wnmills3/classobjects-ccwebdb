"""Setting one field across many items at once.

All-or-nothing in one transaction. A partial bulk edit across 50 coins leaves
a state nobody can describe, and "which of the 50 applied?" is not a question
the UI should ever have to answer.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def test_a_field_is_set_across_every_selected_item(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    items = [make_item(db, year_start=None) for _ in range(3)]

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
    chosen = make_item(db, year_start=1878)
    other = make_item(db, year_start=1921)

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
    items = [make_item(db, year_start=1878) for _ in range(3)]

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
    items = [make_item(db, year_start=1878) for _ in range(3)]

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items], "changes": {"grade": "NOT_A_GRADE"}},
        headers=admin_headers,
    )

    assert response.status_code == 422
    for item in items:
        db.refresh(item)
        assert item.year_start == 1878


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
