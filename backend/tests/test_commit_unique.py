"""`routers._tx.commit_unique`: a unique index's refusal read as the caller's 409."""

from __future__ import annotations

import pytest
from app.models import Vendor
from app.routers._tx import commit_unique
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

TAKEN = "A vendor named Apmex already exists"


def _vendors(db: Session) -> list[str]:
    """Every vendor's name, in name order."""
    return list(db.scalars(select(Vendor.name).order_by(Vendor.name)))


def test_a_row_the_index_accepts_is_committed(db: Session) -> None:
    db.add(Vendor(name="Apmex"))
    commit_unique(db, TAKEN)
    assert _vendors(db) == ["Apmex"]


def test_a_duplicate_the_index_refuses_is_a_409_in_the_caller_s_words(
    db: Session,
) -> None:
    db.add(Vendor(name="Apmex"))
    db.commit()

    db.add(Vendor(name="Apmex"))
    with pytest.raises(HTTPException) as refused:
        commit_unique(db, TAKEN)

    assert refused.value.status_code == 409
    assert refused.value.detail == TAKEN


def test_the_session_is_usable_again_after_a_refusal(db: Session) -> None:
    """Rolled back, not left in a failed transaction: the next statement runs."""
    db.add(Vendor(name="Apmex"))
    db.commit()
    db.add(Vendor(name="Apmex"))
    with pytest.raises(HTTPException):
        commit_unique(db, TAKEN)

    # Without the rollback this raises PendingRollbackError.
    assert db.scalar(select(func.count()).select_from(Vendor)) == 1
    db.add(Vendor(name="Bullion Exchange"))
    commit_unique(db, "unused")
    assert _vendors(db) == ["Apmex", "Bullion Exchange"]


def test_another_database_error_is_not_dressed_up_as_a_duplicate(db: Session) -> None:
    """Only a unique index's refusal is the 409: a value too long is its own error."""
    db.add(Vendor(name="x" * 5000))
    with pytest.raises(DataError):
        commit_unique(db, TAKEN)
    db.rollback()


def test_another_constraint_s_refusal_is_not_dressed_up_as_a_duplicate(
    db: Session,
) -> None:
    """A foreign key naming nothing is not "already exists".

    It is an integrity error like a duplicate, so only what the database
    says it refused tells the two apart.
    """
    db.add(Vendor(name="Apmex", vendor_kind_id=999_999_999))
    with pytest.raises(IntegrityError):
        commit_unique(db, TAKEN)

    # Rolled back all the same: the session is usable.
    assert _vendors(db) == []
