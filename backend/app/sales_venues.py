"""The platforms items are sold through, and the web store among them.

The web store is a platform like any other so a listing always names where it
is offered. Exactly one exists; the migration creates it on a real database,
and `ensure_store_venue` creates it where the schema is built from the models
instead (the test database).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SalesVenue, SalesVenueKind

__all__ = ["STORE_CODE", "ensure_store_venue", "store_venue_id"]

#: The web store platform's code.
STORE_CODE = "store"


def store_venue_id(db: Session) -> int:
    """The web store platform's id; it must exist."""
    found = db.scalar(select(SalesVenue.id).where(SalesVenue.is_own_store.is_(True)))
    if found is None:
        raise RuntimeError(
            "No web store platform: run `alembic upgrade head` on this database"
        )
    return found


def ensure_store_venue(db: Session) -> int:
    """Create the web store platform if it is missing, and return its id."""
    found = db.scalar(select(SalesVenue.id).where(SalesVenue.is_own_store.is_(True)))
    if found is not None:
        return found
    kind_id = db.scalar(
        select(SalesVenueKind.id).where(SalesVenueKind.code == "own_store")
    )
    if kind_id is None:
        raise RuntimeError(
            "sales_venue_kind 'own_store' is not seeded: "
            "run `python -m app.seeding load`"
        )
    venue = SalesVenue(
        code=STORE_CODE,
        name="Web store",
        sales_venue_kind_id=kind_id,
        is_own_store=True,
    )
    db.add(venue)
    db.flush()
    return venue.id
