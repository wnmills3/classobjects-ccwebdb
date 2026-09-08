"""Recording that a person has confirmed a field by looking at the object.

Per field rather than per item because the unit of work is the field:
attributing fifty Morgans means confirming grade on all fifty, then year on
all fifty, and a half-done coin is the normal state.
"""

from __future__ import annotations

import pytest
from app.models import ItemFieldReview
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
