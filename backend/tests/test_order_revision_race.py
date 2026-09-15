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

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from app.models import (
    Authenticity,
    Currency,
    Customer,
    Disposition,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    SalesOrder,
    SalesOrderChange,
    SalesOrderItem,
    StorageForm,
    User,
    UserRole,
    ValuationBasis,
)
from app.order_writes import Line, customer_for_user, place_order, revise_order
from app.routers.orders import _load, _status_code, update_order_status
from app.schemas import OrderStatusUpdate
from fastapi import HTTPException
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.test_concurrency import RACE_TITLE, _code_id, _seed


def _extra_listing(factory: sessionmaker[Session], *, stock: int) -> int:
    """A second RACE_TITLE listing, sharing `_seed`'s item shape without new users.

    Cleaned up by the `committed` fixture the same way `_seed`'s rows are:
    by `source_title`, not by any id this returns.
    """
    with factory() as session:
        item = InventoryItem(
            source_title=RACE_TITLE,
            item_kind_id=_code_id(session, ItemKind, "coin"),
            storage_form_id=_code_id(session, StorageForm, "single"),
            authenticity_id=_code_id(session, Authenticity, "unverified"),
            status_id=_code_id(session, ItemStatus, "received"),
            disposition_id=_code_id(session, Disposition, "listed"),
            valuation_basis_id=_code_id(session, ValuationBasis, "numismatic"),
        )
        session.add(item)
        session.flush()
        listing = Listing(
            inventory_item_id=item.id,
            price=Decimal("100.00"),
            currency_id=_code_id(session, Currency, "USD"),
            quantity_available=stock,
        )
        session.add(listing)
        session.commit()
        return listing.id


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


def test_an_edit_and_a_checkout_cannot_both_take_the_last_unit(
    committed: sessionmaker[Session],
) -> None:
    """A genuinely concurrent admin edit and shopper checkout, not a stale read.

    Two threads, each with its own committing session, released together by a
    barrier -- as in test_concurrency.py -- contend for the last unit of a
    listing an order already holds one of. Only one may take it; the loser
    gets a 409, not a lost update or an oversell. A TestClient-based version
    of this test would serialise both requests through one connection and
    pass even with the lock removed.
    """
    listing_id, (buyer_id, holder_id, admin_id) = _seed(committed, stock=2, buyers=3)
    with committed() as s:
        s.get(User, admin_id).role = UserRole.admin
        holder = s.get(User, holder_id)
        order = place_order(
            s, customer_for_user(s, holder), [Line(listing_id, 1)], holder
        )
        s.commit()
        order_id, version = order.id, order.version

    barrier = threading.Barrier(2)

    def checkout() -> str | int:
        s = committed()
        try:
            user = s.get(User, buyer_id)
            customer = customer_for_user(s, user)
            barrier.wait(timeout=10)
            place_order(s, customer, [Line(listing_id, 1)], user)
            s.commit()
            return "checkout"
        except HTTPException as exc:
            s.rollback()
            return exc.status_code
        finally:
            s.close()

    def edit() -> str | int:
        s = committed()
        try:
            o = s.get(SalesOrder, order_id)
            admin_user = s.get(User, admin_id)
            code = _status_code(s, o)
            customer = o.customer
            barrier.wait(timeout=10)
            revise_order(
                s,
                o,
                status_code=code,
                customer=customer,
                lines=[Line(listing_id, 2, Decimal("100.00"))],
                notes=None,
                version=version,
                by=admin_user,
            )
            s.commit()
            return "edit"
        except HTTPException as exc:
            s.rollback()
            return exc.status_code
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda f: f(), [checkout, edit]), key=str)

    with committed() as s:
        remaining = s.get(Listing, listing_id).quantity_available
    assert remaining == 0
    assert outcomes.count(409) == 1, outcomes
    assert len([o for o in outcomes if o in ("checkout", "edit")]) == 1, outcomes


def test_a_cancel_and_an_edit_on_the_same_order_cannot_deadlock_or_corrupt_stock(
    committed: sessionmaker[Session],
) -> None:
    """A PATCH cancellation and a PUT revision racing the same order.

    `update_order_status` (the PATCH path) and `revise_order` (the PUT path)
    both lock and re-read the `sales_order` row before touching listings --
    order first, listings second -- so two admins racing a cancel and an edit
    on the very same order must serialise on that lock rather than deadlock.

    `OrderStatusUpdate` carries no version, so a cancel is not refused merely
    because an edit changed the order since a caller last read it: whichever
    writer's lock lands second simply acts on the row's *current* state.
    Measured, not assumed -- both orderings are real and this test's setup
    reliably produces both across runs: if the edit's lock lands first, both
    writers succeed (the cancel returns whatever the edit just left on the
    order); if the cancel's lock lands first, the edit is refused with 409
    as no-longer-editable. What must hold either way, and what this test
    checks: no deadlock, and stock is exactly conserved -- `return_stock`
    always hands back precisely the units the order holds *at the moment it
    runs*, never a unit short or a unit conjured from nowhere. See the task
    report for the mutation-test evidence that this specific lock -- not the
    listing lock, which has ample stock here -- is what that conservation
    depends on.
    """
    listing_id, (buyer_id, admin_id) = _seed(committed, stock=5, buyers=2)
    with committed() as s:
        s.get(User, admin_id).role = UserRole.admin
        buyer = s.get(User, buyer_id)
        order = place_order(
            s, customer_for_user(s, buyer), [Line(listing_id, 2)], buyer
        )
        s.commit()
        order_id, version = order.id, order.version

    with committed() as s:
        assert s.get(Listing, listing_id).quantity_available == 3

    barrier = threading.Barrier(2)

    def cancel() -> str | int:
        s = committed()
        try:
            admin = s.get(User, admin_id)
            payload = OrderStatusUpdate(status="cancelled")
            barrier.wait(timeout=10)
            update_order_status(order_id, payload, s, admin)
            return "cancel"
        except HTTPException as exc:
            s.rollback()
            return exc.status_code
        finally:
            s.close()

    def edit() -> str | int:
        s = committed()
        try:
            o = s.get(SalesOrder, order_id)
            admin = s.get(User, admin_id)
            code = _status_code(s, o)
            customer = o.customer
            barrier.wait(timeout=10)
            revise_order(
                s,
                o,
                status_code=code,
                customer=customer,
                lines=[Line(listing_id, 3)],
                notes=None,
                version=version,
                by=admin,
            )
            s.commit()
            return "edit"
        except HTTPException as exc:
            s.rollback()
            return exc.status_code
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        # A result timeout, not just a barrier timeout: a deadlock would hang
        # inside the database call itself, past any barrier, and only a
        # timeout on the *result* turns that hang into a test failure instead
        # of an indefinitely stuck test run.
        outcomes = sorted(pool.map(lambda f: f(), [cancel, edit], timeout=30), key=str)

    # Cancel is never refused here: `OrderStatusUpdate` carries no version,
    # so the only thing that could refuse it -- the order already being
    # cancelled -- cannot happen with a single cancelling thread.
    assert outcomes.count("cancel") == 1, outcomes
    assert outcomes.count(409) <= 1, outcomes
    edit_won = "edit" in outcomes

    with committed() as s:
        listing = s.get(Listing, listing_id)
        final_order = s.get(SalesOrder, order_id)
        final_status = _status_code(s, final_order)
        final_items = [item.quantity for item in final_order.items]

    # Whichever writer's order-row lock landed first, cancelling always
    # returns every unit the order held *at that moment* -- 2 if cancel's
    # lock landed first (the edit never got to apply), 3 if the edit's did
    # (and applied cleanly first) -- so stock is exactly conserved either
    # way: 5 - 2 (the purchase) is restored in full, never a unit short or
    # a unit conjured from nowhere. That conservation is what a missing
    # order-row lock would break -- see the mutation test in the report.
    assert final_status == "cancelled", (outcomes, final_status)
    assert listing.quantity_available == 5, (outcomes, listing.quantity_available)
    if edit_won:
        assert outcomes.count(409) == 0, outcomes
        assert final_items == [3], outcomes
    else:
        assert outcomes.count(409) == 1, outcomes
        assert final_items == [2], outcomes


def test_a_stale_item_write_in_revise_order_is_refused_not_a_500(
    committed: sessionmaker[Session],
) -> None:
    """A concurrently edited item must not surface as an unhandled 500.

    Raising an order's quantity to take a listing's last unit writes to the
    item behind it -- `_after_stock_change` flips its disposition to `sold`.
    That write is never locked (only `Listing` is), so if the item was
    changed and committed by someone else in the meantime, the flush that
    applies it fails with `StaleDataError`. Fix round 1: this used to come
    back as an unhandled `PendingRollbackError` -- the `except` clause read
    `order.id`, but every instance in the session is expired once a flush
    has failed, and reading an attribute off one issues a SELECT the
    transaction (awaiting rollback) refuses. It must come back the same 409
    a stale version does instead.
    """
    listing_id, (buyer_id, admin_id) = _seed(committed, stock=2, buyers=2)

    with committed() as session_a:
        buyer = session_a.get(User, buyer_id)
        order = place_order(
            session_a, customer_for_user(session_a, buyer), [Line(listing_id, 1)], buyer
        )
        session_a.commit()
        order_id, version = order.id, order.version

        listing = session_a.get(Listing, listing_id)
        assert listing.quantity_available == 1
        item_id = listing.inventory_item_id
        # Keep a live reference in A's identity map, exactly as the
        # stale-read test above keeps `stale_listing`: without it,
        # `_after_stock_change`'s `db.get` would simply re-query and see
        # B's committed version, missing the race this test targets.
        stale_item = session_a.get(InventoryItem, item_id)
        assert stale_item.disposition.code == "listed"

        with committed() as session_b:
            item_b = session_b.get(InventoryItem, item_id)
            item_b.description = "touched by session B"
            session_b.commit()

        order = _load(session_a, order_id)
        assert order is not None
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
    assert "reload" in excinfo.value.detail.lower()

    with committed() as session_c:
        remaining = session_c.get(Listing, listing_id).quantity_available
        order_c = session_c.get(SalesOrder, order_id)
        item_lines = [i.quantity for i in order_c.items]
    assert remaining == 1
    assert item_lines == [1]


def test_a_stale_autoflush_mid_cancel_is_refused_not_a_500(
    committed: sessionmaker[Session],
) -> None:
    """A concurrently edited item, hit mid-`return_stock`, must not be a 500.

    Two lines: the first listing's stock is fully sold, so cancelling flips
    its item's disposition from `sold` back to `listed`. `return_stock`
    processes listings in id order, so that write happens while the loop is
    still working through it -- and the autoflush that surfaces the
    conflict fires while `return_stock` is locking and touching the
    *second* listing's item, well before `update_order_status` reaches its
    own `db.commit()`. Fix round 1: wrapping only `commit()` in a
    try/except missed this -- the failing flush is this earlier autoflush,
    not the final commit -- so it escaped as an unhandled 500.
    """
    listing_id, (buyer_id, admin_id) = _seed(committed, stock=1, buyers=2)
    other_listing_id = _extra_listing(committed, stock=5)
    with committed() as s:
        s.get(User, admin_id).role = UserRole.admin
        s.commit()

    with committed() as session_a:
        buyer = session_a.get(User, buyer_id)
        order = place_order(
            session_a,
            customer_for_user(session_a, buyer),
            [Line(listing_id, 1), Line(other_listing_id, 2)],
            buyer,
        )
        session_a.commit()
        order_id = order.id

        listing = session_a.get(Listing, listing_id)
        assert listing.quantity_available == 0
        item_id = listing.inventory_item_id
        # As above: a live reference in A's identity map, so the autoflush
        # inside `return_stock` finds this stale instance rather than a
        # freshly queried, already-current one.
        stale_item = session_a.get(InventoryItem, item_id)
        assert stale_item.disposition.code == "sold"

        with committed() as session_b:
            item_b = session_b.get(InventoryItem, item_id)
            item_b.description = "touched by session B"
            session_b.commit()

        admin = session_a.get(User, admin_id)
        payload = OrderStatusUpdate(status="cancelled")
        with pytest.raises(HTTPException) as excinfo:
            update_order_status(order_id, payload, session_a, admin)

    assert excinfo.value.status_code == 409
    assert "reload" in excinfo.value.detail.lower()

    with committed() as session_c:
        sold_out = session_c.get(Listing, listing_id)
        other = session_c.get(Listing, other_listing_id)
        final_status = _status_code(session_c, session_c.get(SalesOrder, order_id))
    assert sold_out.quantity_available == 0
    assert other.quantity_available == 3
    assert final_status == "pending"
