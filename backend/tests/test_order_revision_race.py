"""Contention around revising an order: a stale read must not go unnoticed.

Real, committing sessions rather than ``TestClient``: a client call serialises
through one connection and would look correct even with the fix reverted --
see test_concurrency.py for the pattern this borrows.

Task 6 adds a genuinely concurrent (threaded) test to this file. This one is
deterministic -- two sessions taking turns, no threads -- and exists to pin
down exactly what a stale read must produce: a 409 naming what is actually
left, never a `StaleDataError` and never a silent oversell.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.models import (
    Customer,
    InventoryItem,
    Listing,
    SalesOrder,
    SalesOrderChange,
    SalesOrderItem,
    User,
)
from app.order_writes import Line, customer_for_user, place_order, revise_order
from app.routers.orders import _load, _status_code
from fastapi import HTTPException
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.test_concurrency import RACE_TITLE, _seed


@pytest.fixture
def committed(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Real, committing sessions; removes every row the race creates."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    with factory() as cleanup:
        cleanup.query(SalesOrderChange).delete()
        cleanup.query(SalesOrderItem).delete()
        cleanup.query(SalesOrder).delete()
        cleanup.query(Customer).delete()
        cleanup.query(Listing).delete()
        cleanup.query(InventoryItem).filter(
            InventoryItem.source_title == RACE_TITLE
        ).delete(synchronize_session=False)
        cleanup.query(User).filter(User.email.like("race%@example.com")).delete(
            synchronize_session=False
        )
        cleanup.commit()


def test_a_stale_read_is_refused_not_a_lost_update(
    committed: sessionmaker[Session],
) -> None:
    """A listing changed after it was read must not be trusted at the lock.

    Session A loads an order -- and, through it, a listing -- before session
    B sells out that listing's remaining stock and commits. When A then
    raises the order's quantity, the availability check must see B's
    committed state, not the value A happened to read earlier: a 409 naming
    what is actually left, not a `StaleDataError` surfacing from a write
    that should never have been attempted, and not a silent oversell.
    """
    listing_id, (buyer_id, admin_id) = _seed(committed, stock=3, buyers=2)

    with committed() as session_a:
        buyer = session_a.get(User, buyer_id)
        order = place_order(
            session_a,
            customer_for_user(session_a, buyer),
            [Line(listing_id, 1)],
            buyer,
        )
        session_a.commit()
        order_id, version = order.id, order.version

        # Load the order -- and, through it, the listing -- into A's
        # identity map before B changes the listing underneath it. Holding
        # `stale_listing` matters: without a live Python reference, nothing
        # keeps this instance around once `revise_order` reloads the order's
        # items below, and the identity map would happily hand back a freshly
        # queried (and so accidentally correct) object -- passing the test
        # for the wrong reason, whether or not the fix is present.
        order = _load(session_a, order_id)
        assert order is not None
        stale_listing = order.items[0].listing
        assert stale_listing.quantity_available == 2

        with committed() as session_b:
            listing_b = session_b.get(Listing, listing_id)
            listing_b.quantity_available = 0
            session_b.commit()

        admin = session_a.get(User, admin_id)
        with pytest.raises(HTTPException) as excinfo:
            revise_order(
                session_a,
                order,
                status_code=_status_code(session_a, order),
                customer=order.customer,
                lines=[Line(listing_id, 2)],
                notes=None,
                version=version,
                by=admin,
            )

    assert excinfo.value.status_code == 409
    assert f"Only 0 more of listing {listing_id}" in excinfo.value.detail

    with committed() as session_c:
        remaining = session_c.get(Listing, listing_id).quantity_available
    assert remaining == 0
