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
from app.sales_venues import store_venue_id
from app.schemas import OrderStatusUpdate
from fastapi import HTTPException
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.test_concurrency import RACE_TITLE, _code_id, _seed


def _present[T](instance: T | None) -> T:
    """A row this test just committed, with its existence stated not assumed.

    Every id handed to `Session.get` or `_load` below was committed a few
    lines earlier -- by `_seed`, by `_extra_listing`, or by the writer under
    test -- so a `None` here is a broken fixture rather than a case any of
    these tests means to handle. Failing on it by name beats letting it
    surface later as an attribute error on `None`.
    """
    assert instance is not None
    return instance


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
            sales_venue_id=store_venue_id(session),
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
        buyer = _present(session_a.get(User, buyer_id))
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
        order = _present(_load(session_a, order_id))
        stale_listing = order.items[0].listing
        assert stale_listing.quantity_available == 2

        with committed() as session_b:
            listing_b = _present(session_b.get(Listing, listing_id))
            listing_b.quantity_available = 0
            session_b.commit()

        admin = _present(session_a.get(User, admin_id))
        with pytest.raises(HTTPException) as excinfo:
            revise_order(
                session_a,
                order,
                customer=order.customer,
                lines=[Line(listing_id, 2)],
                notes=None,
                version=version,
                by=admin,
            )

    assert excinfo.value.status_code == 409
    assert f"Only 0 more of listing {listing_id}" in excinfo.value.detail

    with committed() as session_c:
        remaining = _present(session_c.get(Listing, listing_id)).quantity_available
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
        _present(s.get(User, admin_id)).role = UserRole.admin
        holder = _present(s.get(User, holder_id))
        order = place_order(
            s, customer_for_user(s, holder), [Line(listing_id, 1)], holder
        )
        s.commit()
        order_id, version = order.id, order.version

    barrier = threading.Barrier(2)

    def checkout() -> str | int:
        s = committed()
        try:
            user = _present(s.get(User, buyer_id))
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
            o = _present(s.get(SalesOrder, order_id))
            admin_user = _present(s.get(User, admin_id))
            customer = o.customer
            barrier.wait(timeout=10)
            revise_order(
                s,
                o,
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
        remaining = _present(s.get(Listing, listing_id)).quantity_available
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
        _present(s.get(User, admin_id)).role = UserRole.admin
        buyer = _present(s.get(User, buyer_id))
        order = place_order(
            s, customer_for_user(s, buyer), [Line(listing_id, 2)], buyer
        )
        s.commit()
        order_id, version = order.id, order.version

    with committed() as s:
        assert _present(s.get(Listing, listing_id)).quantity_available == 3

    barrier = threading.Barrier(2)

    def cancel() -> str | int:
        s = committed()
        try:
            admin = _present(s.get(User, admin_id))
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
            o = _present(s.get(SalesOrder, order_id))
            admin = _present(s.get(User, admin_id))
            customer = o.customer
            barrier.wait(timeout=10)
            revise_order(
                s,
                o,
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
        listing = _present(s.get(Listing, listing_id))
        final_order = _present(s.get(SalesOrder, order_id))
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


def test_a_concurrently_edited_item_no_longer_refuses_a_revision(
    committed: sessionmaker[Session],
) -> None:
    """A concurrent edit to the coin behind a line must not refuse the revision.

    **This test used to assert the opposite, and the change is the point.**
    Raising an order's quantity to take a listing's last unit writes to the
    item behind it -- `_after_stock_change` flips its disposition to `sold`.
    That write was never locked, only `Listing` was, so an item another
    session had edited and committed in the meantime failed the flush with
    `StaleDataError` and the caller got a 409 telling them to reload -- a
    *false* conflict: nothing about editing a coin's description says a
    revision of the order holding it must be thrown away.

    The lock-order fix closes it as a by-product. `_lock_listings` now takes
    its rows through `offering_writes.lock_for_sale`, which locks every item
    `_after_stock_change` will write -- `offered_items` for the lines'
    listings -- and re-reads them with `populate_existing`, so the version
    the disposition write uses is the one the lock granted rather than one
    read before the wait. The two writers are serialised instead of one of
    them losing.

    Fails for its stated reason if that re-read goes: remove
    `.execution_options(populate_existing=True)` from
    `offering_writes._lock_items` and the revision is refused 409 again.
    """
    listing_id, (buyer_id, admin_id) = _seed(committed, stock=2, buyers=2)

    with committed() as session_a:
        buyer = _present(session_a.get(User, buyer_id))
        order = place_order(
            session_a, customer_for_user(session_a, buyer), [Line(listing_id, 1)], buyer
        )
        session_a.commit()
        order_id, version = order.id, order.version

        listing = _present(session_a.get(Listing, listing_id))
        assert listing.quantity_available == 1
        item_id = listing.inventory_item_id
        # Keep a live reference in A's identity map, exactly as the
        # stale-read test above keeps `stale_listing`: without it, the item
        # would simply be queried fresh and B's commit would never be the
        # stale-version hazard this test is about.
        stale_item = _present(session_a.get(InventoryItem, item_id))
        assert stale_item.disposition.code == "listed"

        with committed() as session_b:
            item_b = _present(session_b.get(InventoryItem, item_id))
            item_b.description = "touched by session B"
            session_b.commit()

        order = _present(_load(session_a, order_id))
        admin = _present(session_a.get(User, admin_id))
        changed = revise_order(
            session_a,
            order,
            customer=order.customer,
            lines=[Line(listing_id, 2)],
            notes=None,
            version=version,
            by=admin,
        )
        assert changed is True
        session_a.commit()

    with committed() as session_c:
        remaining = _present(session_c.get(Listing, listing_id)).quantity_available
        order_c = _present(session_c.get(SalesOrder, order_id))
        item_lines = [i.quantity for i in order_c.items]
        item_c = _present(session_c.get(InventoryItem, item_id))
        item_description = item_c.description
        item_disposition = item_c.disposition.code
    assert remaining == 0
    assert item_lines == [2]
    # The revision applied *and* B's edit survived it: the lock re-read the
    # row rather than writing over it from a value read before the wait.
    # Asserting only the quantities would pass on a session that had
    # silently discarded B's description.
    assert item_description == "touched by session B"
    assert item_disposition == "sold"


def test_a_concurrently_edited_item_no_longer_refuses_a_cancellation(
    committed: sessionmaker[Session],
) -> None:
    """The same false conflict, reached through `return_stock`, is also gone.

    Two lines: the first listing's stock is fully sold, so cancelling flips
    its item's disposition from `sold` back to `listed`, via the write
    `_after_stock_change` queues for it. That write used to go unlocked, so
    an item another session had edited meanwhile raised `StaleDataError`
    inside `update_order_status` -- caught there and turned into a 409, but
    a 409 refusing a cancellation over an edit to a coin's description.

    `return_stock` takes its rows through `_lock_listings`, and so through
    `offering_writes.lock_for_sale`, which locks and re-reads every item
    either listing offers before anything is written. The cancellation now
    goes through and returns both lines' stock.

    The `except StaleDataError` clauses in `order_writes.revise_order` and
    `routers.orders.update_order_status` are left in place deliberately, and
    are now defence in depth rather than a live path: every version-tracked
    row these two functions write -- `sales_order`, `listing`,
    `inventory_item`, `sales_lot` -- is locked and re-read first. Removing an
    error handler from a money path on the strength of that argument is the
    owner's call, not a by-product of this fix.

    Fails for its stated reason if the item lock's re-read goes, exactly as
    the test above does.
    """
    listing_id, (buyer_id, admin_id) = _seed(committed, stock=1, buyers=2)
    other_listing_id = _extra_listing(committed, stock=5)
    with committed() as s:
        _present(s.get(User, admin_id)).role = UserRole.admin
        s.commit()

    with committed() as session_a:
        buyer = _present(session_a.get(User, buyer_id))
        order = place_order(
            session_a,
            customer_for_user(session_a, buyer),
            [Line(listing_id, 1), Line(other_listing_id, 2)],
            buyer,
        )
        session_a.commit()
        order_id = order.id

        listing = _present(session_a.get(Listing, listing_id))
        assert listing.quantity_available == 0
        item_id = listing.inventory_item_id
        # As above: a live reference in A's identity map, so B's commit
        # leaves this session holding a version that is genuinely behind.
        stale_item = _present(session_a.get(InventoryItem, item_id))
        assert stale_item.disposition.code == "sold"

        with committed() as session_b:
            item_b = _present(session_b.get(InventoryItem, item_id))
            item_b.description = "touched by session B"
            session_b.commit()

        admin = _present(session_a.get(User, admin_id))
        payload = OrderStatusUpdate(status="cancelled")
        update_order_status(order_id, payload, session_a, admin)

    with committed() as session_c:
        sold_out = _present(session_c.get(Listing, listing_id))
        other = _present(session_c.get(Listing, other_listing_id))
        final_status = _status_code(
            session_c, _present(session_c.get(SalesOrder, order_id))
        )
        item_c = _present(session_c.get(InventoryItem, item_id))
        item_description = item_c.description
        item_disposition = item_c.disposition.code
    assert final_status == "cancelled"
    # Both lines' stock is back, not just the one whose item was contended.
    assert sold_out.quantity_available == 1
    assert other.quantity_available == 5
    assert item_description == "touched by session B"
    assert item_disposition == "listed"
