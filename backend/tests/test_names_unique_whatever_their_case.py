"""A vendor, a seller and a storage location are one row whatever their case.

The API asks first, with a SELECT, so two requests at once can both pass it;
these insert directly, as the second of two such requests would, and expect
the database to refuse what the API would have.
"""

from __future__ import annotations

import pytest
from app.models import Seller, StorageLocation, StorageLocationKind, Vendor
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.builders import code_id


def _refused(db: Session, row: object) -> None:
    """Insert `row` in a savepoint and expect the database to refuse it."""
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(row)
        db.flush()


def test_a_vendor_name_in_another_case_is_refused(db: Session) -> None:
    db.add(Vendor(name="ebay.com"))
    db.flush()
    _refused(db, Vendor(name="eBay.com"))


def test_a_seller_name_in_another_case_is_refused(db: Session) -> None:
    db.add(Seller(name="example_seller"))
    db.flush()
    _refused(db, Seller(name="Example_Seller"))


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("Home", None), ("home", None)),
        (("First National", "Box 12"), ("first national", "box 12")),
        ((None, None), (None, None)),
    ],
    ids=["no-box", "with-box", "kind-only"],
)
def test_a_storage_location_in_another_case_is_refused(
    db: Session,
    first: tuple[str | None, str | None],
    second: tuple[str | None, str | None],
) -> None:
    """Kind, institution and box: an absent part is empty, not a new identity."""
    kind_id = code_id(db, StorageLocationKind, "safe")
    db.add(
        StorageLocation(
            storage_location_kind_id=kind_id, institution=first[0], identifier=first[1]
        )
    )
    db.flush()
    _refused(
        db,
        StorageLocation(
            storage_location_kind_id=kind_id,
            institution=second[0],
            identifier=second[1],
        ),
    )


def test_two_vendors_added_at_once_in_two_cases_are_one(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second request's check ran before the first committed: a 409, one row."""
    db.add(Vendor(name="ebay.com"))
    db.commit()
    monkeypatch.setattr(db, "scalar", lambda *args, **kwargs: None)

    res = client.post("/api/vendors", json={"name": "eBay.com"}, headers=admin_headers)

    assert res.status_code == 409, res.text
    monkeypatch.undo()
    count = db.scalar(
        select(func.count())
        .select_from(Vendor)
        .where(func.lower(Vendor.name) == "ebay.com")
    )
    assert count == 1
