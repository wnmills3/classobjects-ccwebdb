"""The transaction boundary the writing routers share.

Every write endpoint commits once, at the end, and a failure anywhere in the
write rolls the whole request back, so a refusal leaves nothing half-written
and the session clean for whatever uses it next. In production
`database.get_db` closes the session after the request anyway; the `client`
fixture in `tests/conftest.py` shares one session across every request in a
test, so there the rollback is what keeps the next request's reads sound.

A `StaleDataError` -- a version column that moved between this request's
read and its UPDATE -- is the caller's 409 with the caller's own wording.
Every other exception is re-raised unchanged after the rollback, so a caller
that maps a domain refusal to a status does it around the block, and the
status is never decided here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError


@contextmanager
def committing(db: Session, stale_detail: str) -> Iterator[None]:
    """Run the block's writes and commit them, or roll everything back.

    `StaleDataError` from the block or the commit becomes a 409 with
    `stale_detail`; any other exception is rolled back and re-raised as is.
    """
    try:
        yield
        db.commit()
    except StaleDataError as stale:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=stale_detail
        ) from stale
    except Exception:
        db.rollback()
        raise


def commit(db: Session, stale_detail: str) -> None:
    """Commit what the caller has already written, as `committing` would."""
    with committing(db, stale_detail):
        pass
