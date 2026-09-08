"""Recording that a person has confirmed a field by looking at the object.

Per field rather than per item because the unit of work is the field:
attributing fifty Morgans means confirming grade on all fifty, then year on
all fifty, and a half-done coin is the normal state.
"""

from __future__ import annotations

import pytest
from app.models import ItemFieldReview
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def test_a_field_is_reviewed_at_most_once(db: Session) -> None:
    """A second confirmation of the same field is the same fact, not a new one.

    Without the constraint the table accumulates duplicates and "is this
    reviewed?" becomes a count rather than an existence check.
    """
    item = make_item(db)
    db.add(ItemFieldReview(inventory_item_id=item.id, field_name="grade_id"))
    db.commit()

    db.add(ItemFieldReview(inventory_item_id=item.id, field_name="grade_id"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_two_fields_of_one_item_are_separate_records(db: Session) -> None:
    """Confirming the grade says nothing about the year."""
    item = make_item(db)
    db.add_all(
        [
            ItemFieldReview(inventory_item_id=item.id, field_name="grade_id"),
            ItemFieldReview(inventory_item_id=item.id, field_name="year_start"),
        ]
    )
    db.commit()

    names = {r.field_name for r in db.query(ItemFieldReview).all()}
    assert names == {"grade_id", "year_start"}


def test_deleting_an_item_takes_its_reviews_with_it(db: Session) -> None:
    """A review of a row that no longer exists is not a fact about anything."""
    item = make_item(db)
    db.add(ItemFieldReview(inventory_item_id=item.id, field_name="grade_id"))
    db.commit()

    db.delete(item)
    db.commit()
    assert db.query(ItemFieldReview).count() == 0


def test_confirming_a_field_records_who_and_when(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)

    response = client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["reviewed"] == ["grade_id"]

    row = db.query(ItemFieldReview).one()
    assert row.field_name == "grade_id"
    assert row.reviewed_by_id is not None
    assert row.reviewed_at is not None


def test_confirming_twice_is_not_an_error(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Looking again and agreeing is the same fact, not a failure."""
    item = make_item(db)
    for _ in range(2):
        response = client.post(
            f"/api/inventory/{item.id}/reviewed",
            json={"fields": ["grade_id"]},
            headers=admin_headers,
        )
        assert response.status_code == 200

    assert db.query(ItemFieldReview).count() == 1


def test_a_field_nobody_reviews_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A typo must not become a review record nobody can query for."""
    item = make_item(db)
    response = client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade"]},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "grade" in response.json()["detail"]


def test_unconfirming_removes_the_record(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Someone who realises they confirmed the wrong coin needs a way back."""
    item = make_item(db)
    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )

    response = client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": [], "replace": True},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["reviewed"] == []
    assert db.query(ItemFieldReview).count() == 0


def test_reading_back_what_has_been_reviewed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id", "year_start"]},
        headers=admin_headers,
    )

    body = client.get(
        f"/api/inventory/{item.id}/reviewed", headers=admin_headers
    ).json()
    assert sorted(body["reviewed"]) == ["grade_id", "year_start"]
