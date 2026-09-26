"""Races around the "an item is offered once" rule (selling design, phase 2).

Real, committing sessions, each with its own connection -- not ``TestClient``,
which funnels every request through Starlette's single portal and so could
never observe two requests racing at all. Follows
``test_order_revision_race.py``'s pattern: each thread gets its own
``Session``, and a ``threading.Barrier`` releases them together so the two
transactions genuinely overlap.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from app import lot_writes, offering_writes, order_writes
from app.lot_writes import LotRefused
from app.models import (
    Authenticity,
    ClaimState,
    Customer,
    Disposition,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    OfferClaim,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesOrder,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesVenue,
    SalesVenueKind,
    StorageForm,
    User,
    UserRole,
    ValuationBasis,
)
from app.offering_writes import OfferRefused
from app.routers.inventory import receive_items
from app.sales_venues import store_venue_id
from app.schemas import ReceiveRequest
from app.security import hash_password
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from tests.builders import code_id
from tests.conftest import (
    ClaimInvariantViolation,
    check_all_invariants,
)

RACE_TITLE = "RACE Offer Contested Item"

#: What `_seed_lot_listing_and_buyer` names the people it commits, and what
#: `_cleanup_race_rows` matches them on. A literal in one place, because the
#: cleanup filter and the seed have to agree or the cleanup silently grades
#: and deletes nothing.
RACE_BUYER = "RACE Buyer"
RACE_ADMIN_EMAIL = "race-admin@example.com"

Outcome = str


def _cleanup_race_rows(cleanup: Session) -> None:
    """Check all five suite invariants against these rows, then delete regardless.

    `check_all_invariants` is called here explicitly because every test in
    this file takes `committed`, not `db`, so `conftest.py`'s autouse
    `_claim_invariant` fixture (which runs the same five checks) does not run
    for any of them at all. An invariant not checked here is an invariant
    this file is exempt from -- and this file commits claims, lot memberships
    and dispositions for real, so it is exactly where an exemption would
    matter most.

    The check runs in a `try` and the deletes in its `finally` on purpose: a
    real violation must still fail the suite (the `raise` propagates once the
    `finally` block finishes), but the rows it found are committed by real,
    independent sessions, and `conftest.py`'s `engine` fixture builds
    `ccwebdb_test` fresh at the start of the test *session* and drops it again
    at the end -- so a poisoned row can never outlive one `pytest` run, but it
    can absolutely poison the rest of *this* one: without the `finally`, every
    later `db`-using test in the same run would fail `_claim_invariant`'s
    check against rows this fixture never got the chance to delete.
    `test_a_forced_violation_still_leaves_the_database_clean`, below, proves
    both halves: that a real violation still raises, and that the rows are
    gone afterward regardless.

    The claim delete matches on `listing_id` as well as `inventory_item_id`,
    not `inventory_item_id` alone -- a claim can name a *different* item than
    the listing it holds (the lot-member shape `test_offering_writes.py`'s
    `claim_invariant_waiver`-marked tests already exercise, and which phase 3
    writes for real: `offer` on a lot writes one claim per member, and a lot
    listing has no item of its own at all). Without the `listing_id` half,
    such a claim would survive this delete, and the `listing` delete just
    below it would then fail on `offer_claim.listing_id`'s
    `ondelete="RESTRICT"` -- inside this same `finally`, replacing whatever
    `ClaimInvariantViolation` the `try` raised with a foreign-key error
    instead. No longer latent, and this is the one file in the suite that
    commits claims for real, so it is the one place it would surface.

    A **lot** listing's `inventory_item_id` is NULL, so matching listings on
    that column alone leaves every lot listing this file commits behind --
    and the `inventory_item` delete two statements later then fails on
    `sales_lot_item.inventory_item_id`'s `ondelete="RESTRICT"`, inside this
    same `finally`, replacing whatever the `try` raised with a foreign-key
    error. Hence the `or_` on `Listing.sales_lot_id` below: a lot listing is
    this file's if any of its memberships names one of this file's items.

    Deletion order, parent by parent, each step naming the FK that fixes it:

    1. `sales_order_item_share`, `sales_order_item`, `sales_order` -- ahead
       of the listings, because `sales_order_item.listing_id` is RESTRICT,
       and ahead of `inventory_item`, because
       `sales_order_item_share.inventory_item_id` is too. Children first
       within the three, so nothing relies on a DB-level cascade this
       ordering makes unnecessary.
    2. `customer` then `users` -- `sales_order.customer_id` is RESTRICT, so
       the orders must already be gone; `customer.user_id` is SET NULL, so
       the user can follow the customer safely either way.
    3. `offer_claim` -- it points at both `listing` and `inventory_item`.
    4. `listing`, in one statement: its self-referential
       `paused_by_listing_id` is RESTRICT, but a single DELETE removing both
       a paused listing and what it points to never violates that.
    5. `sales_lot_item`, then `sales_lot`. **After** the listings, because
       `listing.sales_lot_id` is RESTRICT; **before** `inventory_item`,
       because `sales_lot_item.inventory_item_id` is. `lot_ids` is
       materialised into a Python list *first*: it is derived from
       `sales_lot_item`, so as a live subquery it would read back empty the
       moment those membership rows were deleted, and every lot this file
       made would survive.
    6. `inventory_item`, then the venues this file made.
    """
    item_ids = select(InventoryItem.id).where(InventoryItem.source_title == RACE_TITLE)
    race_lot_ids = select(SalesLotItem.sales_lot_id).where(
        SalesLotItem.inventory_item_id.in_(item_ids)
    )
    is_race_listing = or_(
        Listing.inventory_item_id.in_(item_ids),
        Listing.sales_lot_id.in_(race_lot_ids),
    )
    race_listing_ids = select(Listing.id).where(is_race_listing)
    race_customer_ids = select(Customer.id).where(Customer.display_name == RACE_BUYER)
    race_order_ids = select(SalesOrder.id).where(
        SalesOrder.customer_id.in_(race_customer_ids)
    )
    race_line_ids = select(SalesOrderItem.id).where(
        SalesOrderItem.sales_order_id.in_(race_order_ids)
    )
    try:
        check_all_invariants(cleanup)
    finally:
        lot_ids = list(cleanup.scalars(race_lot_ids).all())
        cleanup.query(SalesOrderItemShare).filter(
            SalesOrderItemShare.sales_order_item_id.in_(race_line_ids)
        ).delete(synchronize_session=False)
        cleanup.query(SalesOrderItem).filter(
            SalesOrderItem.sales_order_id.in_(race_order_ids)
        ).delete(synchronize_session=False)
        cleanup.query(SalesOrder).filter(SalesOrder.id.in_(race_order_ids)).delete(
            synchronize_session=False
        )
        cleanup.query(Customer).filter(Customer.display_name == RACE_BUYER).delete(
            synchronize_session=False
        )
        cleanup.query(User).filter(User.email == RACE_ADMIN_EMAIL).delete(
            synchronize_session=False
        )
        cleanup.query(OfferClaim).filter(
            or_(
                OfferClaim.inventory_item_id.in_(item_ids),
                OfferClaim.listing_id.in_(race_listing_ids),
            )
        ).delete(synchronize_session=False)
        cleanup.query(Listing).filter(is_race_listing).delete(synchronize_session=False)
        cleanup.query(SalesLotItem).filter(
            SalesLotItem.inventory_item_id.in_(item_ids)
        ).delete(synchronize_session=False)
        cleanup.query(SalesLot).filter(SalesLot.id.in_(lot_ids)).delete(
            synchronize_session=False
        )
        cleanup.query(InventoryItem).filter(
            InventoryItem.source_title == RACE_TITLE
        ).delete(synchronize_session=False)
        cleanup.query(SalesVenue).filter(SalesVenue.code.like("race-%")).delete(
            synchronize_session=False
        )
        cleanup.commit()


@pytest.fixture
def committed(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Real, committing sessions; removes every row the race creates.

    Checks the five suite-wide invariants against these rows *before*
    deleting them, not after (see `_cleanup_race_rows`). `_claim_invariant`
    (conftest.py) is autouse, but this fixture is requested explicitly by
    name, and pytest tears an explicitly-requested fixture down before an
    autouse one the test never named -- confirmed with a standalone probe,
    not assumed. Left unchecked, this fixture's own cleanup would delete
    every row the race committed before `_claim_invariant` ever got to look
    at them, so the suite-wide check would silently grade nothing for
    exactly the file where a second writer racing `offering_writes` is most
    likely to leave a claim and its listing disagreeing.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    with factory() as cleanup:
        _cleanup_race_rows(cleanup)


def _seed_item(factory: sessionmaker[Session], *, title: str = RACE_TITLE) -> int:
    """One received, unheld item, real and committed.

    `title` defaults to `RACE_TITLE` -- what every existing caller wants --
    but `test_a_lot_shaped_claim_does_not_survive_cleanup_or_block_it` passes
    a different one on purpose, for an item `_cleanup_race_rows`'s own
    `item_ids` must *not* match.
    """
    with factory() as session:
        item = InventoryItem(
            source_title=title,
            item_kind_id=code_id(session, ItemKind, "coin"),
            storage_form_id=code_id(session, StorageForm, "single"),
            authenticity_id=code_id(session, Authenticity, "unverified"),
            status_id=code_id(session, ItemStatus, "received"),
            disposition_id=code_id(session, Disposition, "held"),
            valuation_basis_id=code_id(session, ValuationBasis, "numismatic"),
        )
        session.add(item)
        session.commit()
        return item.id


def _venue(factory: sessionmaker[Session], code: str) -> int:
    """A non-store marketplace platform to offer on, real and committed."""
    with factory() as session:
        kind_id = code_id(session, SalesVenueKind, "marketplace")
        venue = SalesVenue(code=code, name=code.title(), sales_venue_kind_id=kind_id)
        session.add(venue)
        session.commit()
        return venue.id


def _active_claims(factory: sessionmaker[Session], item_id: int) -> list[OfferClaim]:
    with factory() as session:
        return list(
            session.scalars(
                select(OfferClaim).where(
                    OfferClaim.inventory_item_id == item_id,
                    OfferClaim.state == ClaimState.active,
                )
            ).all()
        )


def _open_lot_ids(factory: sessionmaker[Session], item_id: int) -> list[int]:
    """Which lots hold this item as an open member, right now.

    Ids rather than `SalesLotItem` rows, and read with a plain `select`
    rather than through `lot_writes.lot_holding`: `lot_holding` ends in
    `.one_or_none()`, so it *raises* on the very state this asks about --
    an item open in two lots at once. A test that means to assert "exactly
    one" has to be able to see "two" and say so.
    """
    with factory() as session:
        return list(
            session.scalars(
                select(SalesLotItem.sales_lot_id).where(
                    SalesLotItem.inventory_item_id == item_id,
                    SalesLotItem.released_at.is_(None),
                )
            ).all()
        )


def _store_venue_id(factory: sessionmaker[Session]) -> int:
    """The web store's venue id, read in a committed session of its own."""
    with factory() as session:
        return store_venue_id(session)


def _seed_lot_listing_and_buyer(
    factory: sessionmaker[Session], member_ids: list[int], store_id: int
) -> tuple[int, int, int]:
    """A lot of these items, offered in the shop, plus a RACE buyer and admin.

    Returns `(listing_id, customer_id, admin_id)` -- **ids, not instances**,
    for the reason every other helper in this file returns ids: each racing
    thread gets a session of its own, and an instance loaded here would
    belong to a session that is already closed.

    The customer's `display_name` and the administrator's email are the two
    module constants `_cleanup_race_rows` matches on, so these rows cannot
    outlive the test that made them.
    """
    with factory() as session:
        lot = lot_writes.create_lot(
            session, title="RACE lot in the shop", description=""
        )
        for item_id in member_ids:
            lot_writes.add_member(session, lot, session.get_one(InventoryItem, item_id))
        session.flush()
        listing = offering_writes.offer(
            session,
            lot=lot,
            venue=session.get_one(SalesVenue, store_id),
            listing_format=ListingFormat.fixed_price,
            price=Decimal("250.00"),
            title="RACE lot in the shop",
            description="",
            external_id=None,
        )
        admin = User(
            email=RACE_ADMIN_EMAIL,
            full_name="RACE Admin",
            hashed_password=hash_password("racepassword"),
            role=UserRole.manager,
        )
        customer = Customer(display_name=RACE_BUYER, email=None)
        session.add_all([admin, customer])
        session.commit()
        return listing.id, customer.id, admin.id


def test_a_forced_violation_still_leaves_the_database_clean(
    committed: sessionmaker[Session],
) -> None:
    """`_cleanup_race_rows` must delete even when its own check raises.

    Poisons a real, committed claim/listing pair directly -- offered through
    `offering_writes.offer` first, so the row shape is exactly what a real
    race would leave, then flipped by hand the way `test_offering_writes.py`'s
    `_claim()` helper does for the rolled-back suite, bypassing
    `offering_writes` on purpose. Calls `_cleanup_race_rows` on a separate
    session directly, rather than relying on this test's own `committed`
    fixture teardown, so this proves the point without depending on
    cross-test ordering: without the `try`/`finally` split inside it, this
    would leave the poisoned rows behind for the rest of *this* `pytest` run
    (see that function's docstring) -- every later `db`-using test would fail
    `_claim_invariant`'s check against them. They cannot outlive the run
    itself: `conftest.py`'s `engine` fixture drops and rebuilds `ccwebdb_test`
    fresh every session.

    Known limit: the `pytest.raises(ClaimInvariantViolation)` below does not
    assert that the violation names *this* listing specifically, only that
    one was raised -- an unrelated pre-existing violation elsewhere would
    also satisfy it. The cleanliness assertions after it are unaffected by
    that, and per-test transactions mean only seed data and this file's own
    rows are ever committed to begin with, so there is nothing else here for
    an unrelated violation to come from in practice.
    """
    item_id = _seed_item(committed)
    venue_id = _venue(committed, "race-poison")
    with committed() as session:
        item = session.get_one(InventoryItem, item_id)
        venue = session.get_one(SalesVenue, venue_id)
        listing = offering_writes.offer(
            session,
            item=item,
            venue=venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
            quantity=1,
        )
        session.commit()
        claim = session.scalar(
            select(OfferClaim).where(OfferClaim.listing_id == listing.id)
        )
        assert claim is not None
        claim.state = ClaimState.released  # the listing is still active
        session.commit()

    with committed() as cleanup, pytest.raises(ClaimInvariantViolation):
        _cleanup_race_rows(cleanup)

    with committed() as verify:
        assert (
            verify.scalar(
                select(InventoryItem.id).where(InventoryItem.source_title == RACE_TITLE)
            )
            is None
        )
        assert (
            verify.scalar(select(SalesVenue.id).where(SalesVenue.code == "race-poison"))
            is None
        )


def test_a_lot_shaped_claim_does_not_survive_cleanup_or_block_it(
    committed: sessionmaker[Session],
) -> None:
    """A claim naming a different item than its listing must not wreck cleanup.

    Builds the exact shape `_cleanup_race_rows`'s docstring warns about: a
    claim whose `inventory_item_id` is *not* one of this file's own
    RACE-tagged items, but whose `listing_id` *is* one of its listings --
    the lot-member shape `test_offering_writes.py`'s `claim_invariant_waiver`
    tests already exercise, constructed directly the same way they are,
    bypassing `offering_writes` on purpose. `offer` writes that shape for a
    real lot now; what no write path produces is the *mismatched state*
    below -- a `released` claim on a still-active listing -- which is the
    part this test rests on.
    Before the extra `OfferClaim.listing_id.in_(...)` predicate, this claim
    would survive the delete keyed only on `inventory_item_id`, and the
    `listing` delete right after it would then fail on
    `offer_claim.listing_id`'s `ondelete="RESTRICT"` -- inside the same
    `finally` that was supposed to guarantee cleanup -- masking the genuine
    `ClaimInvariantViolation` the claim's mismatched state should raise
    with a foreign-key error instead.

    The "member" item is deliberately *not* tagged `RACE_TITLE`, so it falls
    outside every filter in `_cleanup_race_rows` on purpose; the outer
    `try`/`finally` here removes it and its claim regardless of whether the
    assertions below pass, so this test cannot itself leak a row into
    `ccwebdb_test`.
    """
    member_id = _seed_item(committed, title="RACE Lot Member (not RACE-tagged)")
    try:
        item_id = _seed_item(committed)
        venue_id = _venue(committed, "race-lot")
        with committed() as session:
            item = session.get_one(InventoryItem, item_id)
            venue = session.get_one(SalesVenue, venue_id)
            listing = offering_writes.offer(
                session,
                item=item,
                venue=venue,
                listing_format=ListingFormat.fixed_price,
                price=Decimal("10.00"),
                title="",
                description="",
                external_id=None,
                quantity=1,
            )
            session.commit()
            listing_id = listing.id
            session.add(
                OfferClaim(
                    inventory_item_id=member_id,
                    listing_id=listing_id,
                    state=ClaimState.released,  # the listing is still active
                )
            )
            session.commit()

        with committed() as cleanup, pytest.raises(ClaimInvariantViolation):
            _cleanup_race_rows(cleanup)

        with committed() as verify:
            assert verify.get(Listing, listing_id) is None
            assert (
                verify.scalar(
                    select(OfferClaim.id).where(OfferClaim.listing_id == listing_id)
                )
                is None
            )
    finally:
        with committed() as cleanup:
            cleanup.query(OfferClaim).filter(
                OfferClaim.inventory_item_id == member_id
            ).delete(synchronize_session=False)
            cleanup.query(InventoryItem).filter(InventoryItem.id == member_id).delete(
                synchronize_session=False
            )
            cleanup.commit()


def test_two_platforms_racing_the_same_item_leave_exactly_one_winner(
    committed: sessionmaker[Session],
) -> None:
    """Two threads offer one item on two platforms at once: one wins.

    Both lock the same item row (``offering_writes._lock_items``) before
    reading what claims it, so the loser should see the winner's committed
    listing and be refused cleanly -- but the backstop is the database's own
    partial unique index (``uq_offer_claim_active``), so an ``IntegrityError``
    is an acceptable loss too, not just ``OfferRefused``.
    """
    item_id = _seed_item(committed)
    venue_ids = [_venue(committed, "race-ebay"), _venue(committed, "race-whatnot")]
    barrier = threading.Barrier(2)

    def attempt(venue_id: int, price: Decimal) -> Outcome:
        session = committed()
        try:
            item = session.get_one(InventoryItem, item_id)
            venue = session.get_one(SalesVenue, venue_id)
            barrier.wait(timeout=10)
            offering_writes.offer(
                session,
                item=item,
                venue=venue,
                listing_format=ListingFormat.fixed_price,
                price=price,
                title="race",
                description="",
                external_id=None,
                quantity=1,
            )
            session.commit()
            return "ok"
        except OfferRefused:
            session.rollback()
            return "refused"
        except IntegrityError:
            session.rollback()
            return "integrity_error"
        finally:
            session.close()

    jobs: list[tuple[int, Decimal]] = list(
        zip(venue_ids, [Decimal("10.00"), Decimal("20.00")], strict=True)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            pool.map(lambda job: attempt(*job), jobs, timeout=30), key=str
        )

    assert outcomes.count("ok") == 1, outcomes
    losses = outcomes.count("refused") + outcomes.count("integrity_error")
    assert losses == 1, outcomes

    active = _active_claims(committed, item_id)
    assert len(active) == 1, active


def test_offering_elsewhere_and_ending_the_store_listing_leave_no_orphan(
    committed: sessionmaker[Session],
) -> None:
    """One thread offers the item elsewhere; another ends the store listing.

    Whatever order the two locks land in, the end state must be consistent:
    no listing left `paused` whose pauser is `ended`, and the item has at
    most one active claim.
    """
    item_id = _seed_item(committed)
    elsewhere_venue_id = _venue(committed, "race-elsewhere")

    with committed() as session:
        item = session.get_one(InventoryItem, item_id)
        store = session.get_one(SalesVenue, store_venue_id(session))
        store_listing = offering_writes.offer(
            session,
            item=item,
            venue=store,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("50.00"),
            title="race store",
            description="",
            external_id=None,
            quantity=1,
        )
        session.commit()
        store_listing_id = store_listing.id

    barrier = threading.Barrier(2)

    def offer_elsewhere() -> Outcome:
        session = committed()
        try:
            item = session.get_one(InventoryItem, item_id)
            venue = session.get_one(SalesVenue, elsewhere_venue_id)
            barrier.wait(timeout=10)
            offering_writes.offer(
                session,
                item=item,
                venue=venue,
                listing_format=ListingFormat.fixed_price,
                price=Decimal("40.00"),
                title="race elsewhere",
                description="",
                external_id=None,
                quantity=1,
            )
            session.commit()
            return "offered"
        except OfferRefused:
            session.rollback()
            return "refused"
        except IntegrityError:
            session.rollback()
            return "integrity_error"
        except StaleDataError:
            session.rollback()
            return "stale"
        finally:
            session.close()

    def end_store() -> Outcome:
        session = committed()
        try:
            # Loaded before the barrier, exactly as a router handler would
            # have it in hand already -- so this is the same object whether
            # or not the other thread changes the row underneath it.
            listing = session.get_one(Listing, store_listing_id)
            barrier.wait(timeout=10)
            offering_writes.end_offer(session, listing, sold=False)
            session.commit()
            return "ended"
        except OfferRefused:
            session.rollback()
            return "refused"
        except IntegrityError:
            session.rollback()
            return "integrity_error"
        except StaleDataError:
            session.rollback()
            return "stale"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            pool.map(lambda f: f(), [offer_elsewhere, end_store], timeout=30), key=str
        )

    with committed() as session:
        orphaned = session.scalars(
            select(Listing).where(
                Listing.status == ListingStatus.paused,
                Listing.paused_by_listing_id.in_(
                    select(Listing.id).where(Listing.status == ListingStatus.ended)
                ),
            )
        ).all()

    assert orphaned == [], (outcomes, orphaned)
    active = _active_claims(committed, item_id)
    assert len(active) <= 1, (outcomes, active)
    # Both operations are legitimate on their own; the item lock the pair
    # share is what has to make them safe together, not a rejection of one of
    # them. `"stale"` names the defect this test found: `end_offer` mutating
    # a `Listing` object the caller loaded before a concurrent `offer`
    # elsewhere paused it underneath them, using a version already gone by
    # the time this function's own item lock let it proceed.
    assert "stale" not in outcomes, outcomes


def test_two_lots_cannot_both_claim_one_item(
    committed: sessionmaker[Session],
) -> None:
    """Two lots being assembled around one coin at once: exactly one gets it.

    Survives: removing the `uq_sales_lot_item_open` index from
    `SalesLotItem.__table_args__` (`app/models/sales.py:481-486`) makes this
    fail -- both memberships are then accepted and the coin is open in two
    lots at once, which is the one thing `sales_lot_item` exists to prevent.
    Measured, eight runs of eight: `AssertionError: ['won', 'won']`.

    The race is decided by the *membership commit*, not by the offer. A
    losing thread rolls its whole transaction back, membership included, so
    a race that offers inside the same transaction hides the damage: with
    the index gone the loser is still refused at the offer, still rolls
    back, and still leaves exactly one membership behind. Committing the
    assembly is what makes the second membership visible to be asserted on.
    """
    shared = _seed_item(committed)
    # One item to warm each thread up with before the barrier, and three more
    # to keep it busy after the contended add -- see `claim_it` for why the
    # race needs both halves.
    own_items = [[_seed_item(committed) for _ in range(4)] for _ in range(2)]
    venue_id = _venue(committed, "race-lot-a")
    barrier = threading.Barrier(2)

    def claim_it(mine: list[int], title: str) -> Outcome:
        """Assemble a lot around `shared`, committing the membership."""
        warm, *tail = mine
        with committed() as session:
            try:
                lot = lot_writes.create_lot(session, title=title, description="")
                # A member of this thread's own before the barrier: same code
                # path, no contention. It settles the connection, the lot's
                # row lock and every statement `add_member` issues, so the
                # two threads arrive at the contended add below in the same
                # state rather than one of them paying first-call costs.
                lot_writes.add_member(
                    session, lot, session.get_one(InventoryItem, warm)
                )
                # The barrier sits *before* the contended `add_member`, never
                # after it. `add_member` flushes its INSERT inside a savepoint
                # as it goes, so a barrier placed after that flush is one
                # neither thread can reach: the second thread's INSERT blocks
                # on the first thread's uncommitted row, the first waits at
                # the barrier for a partner that cannot arrive, and the race
                # ends in `BrokenBarrierError` rather than in a result.
                barrier.wait(timeout=10)
                lot_writes.add_member(
                    session, lot, session.get_one(InventoryItem, shared)
                )
                # Three more of this thread's own, between the contended
                # INSERT and the COMMIT. `add_member`'s own docstring says
                # `uq_sales_lot_item_open` "is the backstop, not the check
                # above" -- and reaching the backstop means both threads must
                # get past `lot_holding` before *either* commits. Committing
                # straight after the contended add closes that window: the
                # first thread commits in a round trip or two, and the second
                # then loses to the sequential check instead, so the race
                # decides nothing and the index is never asked.
                for item_id in tail:
                    lot_writes.add_member(
                        session, lot, session.get_one(InventoryItem, item_id)
                    )
                session.commit()
                return "won"
            except (IntegrityError, LotRefused):
                session.rollback()
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(claim_it, own_items[0], "RACE lot A")
        second = pool.submit(claim_it, own_items[1], "RACE lot B")
        outcomes = sorted([first.result(), second.result()])

    assert outcomes == ["refused", "won"], outcomes
    holders = _open_lot_ids(committed, shared)
    assert len(holders) == 1, holders

    # The offer is made afterwards, single-threaded, and is not part of the
    # race. It is here because the state the race leaves has to be one a real
    # offer can still act on: a lot that cannot be offered would make the
    # assertions above true of a database nobody could sell from.
    with committed() as session:
        offering_writes.offer(
            session,
            lot=session.get_one(SalesLot, holders[0]),
            venue=session.get_one(SalesVenue, venue_id),
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="RACE lot winner",
            description="",
            external_id=None,
        )
        session.commit()
    assert len(_active_claims(committed, shared)) == 1


def test_offering_a_lot_races_offering_one_of_its_members(
    committed: sessionmaker[Session],
) -> None:
    """A lot and one of its own members cannot both be offered.

    Survives: removing `.with_for_update()` from
    `offering_writes._lock_items` (`app/offering_writes.py`) makes this
    fail -- neither writer holds the coin's row, both decide on what they
    read before the other committed, and the loser's disposition write dies
    on `InventoryItem.version` instead of being refused with a reason.
    Measured, six runs of six: `AssertionError: ['stale', 'won']`.

    **Re-measured after `offer` began taking these rows through
    `lock_for_sale`: fourteen of sixteen, not sixteen of sixteen.** Two runs
    of the mutated code passed. This mutation was always a race about which
    thread gets to its disposition write first, so it reproduces *often*
    rather than *always*; the earlier "six of six" was a smaller sample of
    the same thing, not a stronger result. The guarantee itself is not
    probabilistic -- what varies is only whether the two threads overlap
    closely enough for the missing lock to matter on a given run.

    **Not** `uq_offer_claim_active`, which is what this test was originally
    specified against. Measured six runs of six with that index removed and
    the item lock left in: all six pass. The lock serializes the two
    writers, so the loser is refused by `_locked_offers` or `_refuse_grouped`
    -- an ordinary sequential check -- and the partial unique index is never
    reached. It is a real backstop and it is not what this race proves;
    a test naming it here would be a test that cannot fail for its stated
    reason. With *both* guards removed this also fails, six of six.
    """
    shared = _seed_item(committed)
    partner = _seed_item(committed)
    lot_venue = _venue(committed, "race-lot-c")
    item_venue = _venue(committed, "race-item-c")
    barrier = threading.Barrier(2)

    def offer_the_lot() -> Outcome:
        with committed() as session:
            try:
                lot = lot_writes.create_lot(session, title="RACE lot C", description="")
                for item_id in (shared, partner):
                    lot_writes.add_member(
                        session, lot, session.get_one(InventoryItem, item_id)
                    )
                session.flush()
                # Loaded before the barrier, exactly as a router handler would
                # have it in hand already -- and, here, so that the two
                # threads reach `offer` at the same instant instead of one of
                # them paying for two `get_one` round trips first.
                venue = session.get_one(SalesVenue, lot_venue)
                barrier.wait(timeout=10)
                offering_writes.offer(
                    session,
                    lot=lot,
                    venue=venue,
                    listing_format=ListingFormat.fixed_price,
                    price=Decimal("10.00"),
                    title="RACE lot C",
                    description="",
                    external_id=None,
                )
                session.commit()
                return "won"
            except (IntegrityError, OfferRefused):
                session.rollback()
                return "refused"
            except StaleDataError:
                session.rollback()
                return "stale"

    def offer_the_member() -> Outcome:
        with committed() as session:
            try:
                item = session.get_one(InventoryItem, shared)
                venue = session.get_one(SalesVenue, item_venue)
                barrier.wait(timeout=10)
                offering_writes.offer(
                    session,
                    item=item,
                    venue=venue,
                    listing_format=ListingFormat.fixed_price,
                    price=Decimal("10.00"),
                    title="",
                    description="",
                    external_id=None,
                )
                session.commit()
                return "won"
            except (IntegrityError, OfferRefused):
                session.rollback()
                return "refused"
            except StaleDataError:
                session.rollback()
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            future.result()
            for future in [pool.submit(offer_the_lot), pool.submit(offer_the_member)]
        )
    # `"stale"` named and asserted against separately, exactly as
    # `test_offering_elsewhere_and_ending_the_store_listing_leave_no_orphan`
    # does it: a loser is entitled to be *refused*, never to fail on an
    # optimistic-lock conflict. Catching `StaleDataError` as an ordinary
    # refusal would make this test pass with the item lock removed, which is
    # the one guarantee it exists to prove.
    assert "stale" not in outcomes, outcomes
    assert outcomes == ["refused", "won"], outcomes
    assert len(_active_claims(committed, shared)) == 1


def test_two_checkouts_race_for_one_lot(
    committed: sessionmaker[Session],
) -> None:
    """One lot sitting in two carts is bought exactly once, and ends once.

    Survives: removing `.with_for_update(...)` from **all three** statements
    in `offering_writes._acquire` -- `_lock_lots`, `_lock_items` and
    `_lock_listing_rows` -- makes this fail: both checkouts then decide on a
    `quantity_available` they read before the other committed, and the loser
    dies on `Listing.version` rather than being told what is left. Measured,
    eight runs of eight: `AssertionError: ['stale', 'won']`.

    **Re-measured when the lock order was given a single owner, and the named
    mutation had to change.** It used to be the listing lock alone, in
    `order_writes._lock_listings`. That statement now reaches through
    `offering_writes.lock_for_sale`, which takes the lot's row and the
    members' rows *before* it -- and **any one of those three serializes two
    checkouts of one lot on its own**. Measured: removing only the listing
    lock passes eight of eight, and leaving only the item lock passes four of
    four. The guarantee is over-determined now rather than less well
    protected, which is why this test names three sites instead of one; the
    *ordering* those three are taken in is what
    `test_a_checkout_takes_the_three_kinds_of_row_in_the_canonical_order`
    (`tests/test_offering_writes.py`) measures, because no single-lock
    mutation can reach it.

    **This is not the obvious race**, and the substitution is
    deliberate. The obvious one is a checkout racing a *pause* of the same
    lot, the pause coming from offering one member elsewhere. No such pause
    exists any more: `offering_writes._refuse_grouped` refuses offering a
    member of an `offered` lot at all, and a lot cannot be re-offered
    (`_lot_members` refuses a lot that is not `assembling`) nor can a member
    join a second open lot (`uq_sales_lot_item_open`) -- so nothing in the
    code can pause an offered lot's store listing. Written that way, the
    race passed because the pause was simply refused, which is
    `_refuse_grouped`'s guarantee and not a concurrency one; and in the
    fuller run it instead hit
    a genuine Postgres deadlock, because `place_order` took listings before
    items while `offering_writes.offer` took items before listings. That
    deadlock was reported as a defect, and is now fixed and pinned by
    `test_buying_a_lot_races_offering_one_of_its_coins` below.

    Two checkouts of one lot listing is the guarantee that *is* both real and
    lot-specific: `_settle_sold_lots` must end the lot exactly once, and its
    members must be paid their shares exactly once.

    This race is also what proved the lock-order design note wrong about an
    offered lot's membership being frozen outright. The loser reads the two
    members, waits, and finds none: `offering_writes._end` released them when
    the winner's sale ended the listing. `lock_for_sale`'s confirming re-read
    holds the set frozen only while the listing is still on offer, and this
    test is what fails if that exemption goes -- with `LockSetChanged`
    instead of the 409 below.
    """
    members = [_seed_item(committed) for _ in range(2)]
    store_id = _store_venue_id(committed)
    listing_id, customer_id, admin_id = _seed_lot_listing_and_buyer(
        committed, members, store_id
    )
    barrier = threading.Barrier(2)

    def buy_it() -> Outcome:
        with committed() as session:
            try:
                # Buyer and operator loaded before the barrier, the way a
                # request handler already holds them -- and so that the two
                # threads reach `place_order` together instead of one of them
                # paying for two `get_one` round trips inside the race.
                buyer = session.get_one(Customer, customer_id)
                operator = session.get_one(User, admin_id)
                barrier.wait(timeout=10)
                order_writes.place_order(
                    session,
                    buyer,
                    [order_writes.Line(listing_id=listing_id, quantity=1)],
                    operator,
                )
                session.commit()
                return "won"
            except (HTTPException, IntegrityError):
                session.rollback()
                return "refused"
            except StaleDataError:
                session.rollback()
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        # Both submitted before either is waited on. A tuple of two
        # `pool.submit(...).result()` calls looks symmetric and is not: it
        # is evaluated left to right, so the first thread would be waited
        # out before the second was ever submitted, and the barrier inside
        # would time out with nobody to meet.
        first = pool.submit(buy_it)
        second = pool.submit(buy_it)
        outcomes = sorted([first.result(), second.result()])

    # `"stale"` named apart from `"refused"`, the idiom this file already uses
    # two tests above: the loser of a checkout is entitled to a 409 saying
    # what is left, never to an optimistic-lock failure. Folding
    # `StaleDataError` in with the refusals is what makes the test pass with the
    # listing lock removed, because `Listing.version` then refuses the second
    # writer instead of the lock serializing them.
    assert "stale" not in outcomes, outcomes
    assert outcomes == ["refused", "won"], outcomes
    with committed() as verify:
        sold = verify.get_one(Listing, listing_id)
        # The outcome strings alone would pass on a listing left at -1, on a
        # lot still `offered`, and on two orders against one listing. Each of
        # those is the real damage the lock prevents, so each is asserted.
        assert sold.quantity_available == 0
        assert sold.status is ListingStatus.ended
        assert sold.sales_lot_id is not None
        lot = verify.get_one(SalesLot, sold.sales_lot_id)
        assert lot.status is SalesLotStatus.sold
        assert _open_lot_ids(committed, members[0]) == []
        lines = list(
            verify.scalars(
                select(SalesOrderItem).where(SalesOrderItem.listing_id == listing_id)
            ).all()
        )
        assert len(lines) == 1
        # One share per member, exactly once: `_sync_shares` divides the
        # line's money among the lot's open members, and a second winning
        # checkout would either double them or find no members left to
        # divide among.
        shares = list(
            verify.scalars(
                select(SalesOrderItemShare).where(
                    SalesOrderItemShare.sales_order_item_id == lines[0].id
                )
            ).all()
        )
        assert sorted(share.inventory_item_id for share in shares) == sorted(members)
        assert sum((share.amount for share in shares), Decimal("0.00")) == Decimal(
            "250.00"
        )


def test_buying_a_lot_races_offering_one_of_its_coins(
    committed: sessionmaker[Session],
) -> None:
    """A checkout of a lot and an offer of one of its coins never deadlock.

    The cross-writer race the phase-3 branch could not write. `place_order`
    took the listing and then the items; `offering_writes.offer` took the
    items and then the listings, so a checkout of a lot and an offer of one
    of its coins could each hold what the other waited for and Postgres
    aborted one of them. `"deadlock"` below is that abort, and it is asserted
    against separately from `"refused"` for the reason this file already
    asserts `"stale"` separately: a loser is entitled to be *refused*, with a
    reason a person can act on, never to a 500 from an aborted transaction.

    Both operations are legitimate on their own and exactly one of them can
    win. Whichever order the two transactions land in, the offer is the one
    refused -- by `_refuse_grouped` while the lot is still offered, or by
    `_refuse_sold` once the checkout has bought it -- so the outcome pair is
    the same and only the *reason* differs. That is the point: with one owner
    for the acquisition order, which thread arrives first stops deciding
    whether anyone gets an error page.

    Survives: **bypassing the single owner** from `order_writes._lock_listings`
    -- putting back its own
    `select(Listing).where(...).order_by(Listing.id).with_for_update()`, so
    that module takes listings first again while `offering_writes` still
    takes items first -- makes this fail. Measured, eight runs of eight:
    `AssertionError: ['bought', 'deadlock']`, which is also what the
    pre-fix code gave, eight runs of eight, before the owner existed.

    **Inverting `offering_writes._acquire` does *not* make this fail**, and
    that is worth stating rather than leaving as a gap: the inversion moves
    both writers at once, so they still agree and there is still no cycle
    (measured, eight runs of eight green). A deadlock needs *disagreement*,
    not a particular direction. The inversion is caught by
    `test_a_checkout_takes_the_three_kinds_of_row_in_the_canonical_order`
    (`tests/test_offering_writes.py`), which measures the sequence of kinds
    on one connection. The two tests are a pair and neither covers the
    other's mutation.

    Stability: twenty runs of twenty green with the fix in place, no result
    varying.
    """
    members = [_seed_item(committed) for _ in range(2)]
    store_id = _store_venue_id(committed)
    listing_id, customer_id, admin_id = _seed_lot_listing_and_buyer(
        committed, members, store_id
    )
    elsewhere_id = _venue(committed, "race-lock-order")
    barrier = threading.Barrier(2)

    def buy_the_lot() -> Outcome:
        with committed() as session:
            try:
                # Buyer and operator loaded before the barrier, the way a
                # request handler already holds them, so the first statement
                # this thread issues after the barrier is the lock pass
                # itself rather than two `get_one` round trips.
                buyer = session.get_one(Customer, customer_id)
                operator = session.get_one(User, admin_id)
                barrier.wait(timeout=10)
                order_writes.place_order(
                    session,
                    buyer,
                    [order_writes.Line(listing_id=listing_id, quantity=1)],
                    operator,
                )
                session.commit()
                return "bought"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except (HTTPException, IntegrityError):
                session.rollback()
                return "refused"
            except StaleDataError:
                session.rollback()
                return "stale"

    def offer_a_coin() -> Outcome:
        with committed() as session:
            try:
                item = session.get_one(InventoryItem, members[0])
                venue = session.get_one(SalesVenue, elsewhere_id)
                barrier.wait(timeout=10)
                offering_writes.offer(
                    session,
                    item=item,
                    venue=venue,
                    listing_format=ListingFormat.fixed_price,
                    price=Decimal("10.00"),
                    title="RACE coin from a lot",
                    description="",
                    external_id=None,
                )
                session.commit()
                return "offered"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except (OfferRefused, IntegrityError):
                session.rollback()
                return "refused"
            except StaleDataError:
                session.rollback()
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        # Both submitted before either is waited on, for the reason
        # `test_two_checkouts_race_for_one_lot` gives: waiting on the first
        # result before submitting the second leaves the barrier with nobody
        # to meet.
        first = pool.submit(buy_the_lot)
        second = pool.submit(offer_a_coin)
        outcomes = sorted([first.result(), second.result()])

    assert "deadlock" not in outcomes, outcomes
    assert "stale" not in outcomes, outcomes
    assert outcomes == ["bought", "refused"], outcomes

    with committed() as verify:
        # The outcome strings alone would pass on a coin offered in two
        # places at once, on a lot left `offered`, and on shares that do not
        # add up to what was paid. Each is the real damage, so each is asserted.
        sold = verify.get_one(Listing, listing_id)
        assert sold.quantity_available == 0
        assert sold.status is ListingStatus.ended
        assert sold.sales_lot_id is not None
        lot = verify.get_one(SalesLot, sold.sales_lot_id)
        assert lot.status is SalesLotStatus.sold
        assert (
            verify.scalar(
                select(Listing.id).where(Listing.sales_venue_id == elsewhere_id)
            )
            is None
        )
        for member_id in members:
            assert verify.get_one(InventoryItem, member_id).disposition.code == "sold"
        shares = list(
            verify.scalars(
                select(SalesOrderItemShare).where(
                    SalesOrderItemShare.inventory_item_id.in_(members)
                )
            ).all()
        )
        assert sorted(share.inventory_item_id for share in shares) == sorted(members)
        assert sum((share.amount for share in shares), Decimal("0.00")) == Decimal(
            "250.00"
        )


def test_buying_a_lot_races_marking_one_of_its_coins_missing(
    committed: sessionmaker[Session],
) -> None:
    """A checkout of a lot and a receipt against one of its coins never deadlock.

    The second cross-writer pair, found in review after the first commit.
    `routers.inventory.receive_items` wrote its `inventory_item` status rows
    and flushed them -- taking their exclusive row locks -- and only then
    called `end_offer`, whose pass waits on the **lot** row. So receiving
    acquired items before lots, the inverse of every other writer, and an
    administrator marking a lot's member `missing` while a shopper checked
    that lot out could each hold what the other waited for:
    `order_writes._lock_listings` takes the lot row as its first statement
    and then waits on the member row receiving holds.

    Not a regression of the lock-order fix -- the same pair deadlocked before
    it, on the listing instead of the lot -- and closed by giving receiving
    the same `lock_for_sale` pass before its first write.

    **Both may succeed here, and that is correct** -- unlike the offer race
    above, where one side is always refused. `acknowledge_for_sale=True` is
    the operator saying "I know a buyer is looking at this coin; record it
    missing anyway", so `sale_state.guard` does not refuse and the receipt
    always goes through. What the interleaving decides is whether the
    *checkout* gets in first. `"deadlock"` is asserted against separately for
    the reason this file already asserts `"stale"` separately.

    **This test found a hole in the first version of its own fix**, which is
    why the state assertions below are as specific as they are. Receiving read
    `offers_holding` once, before the locks, and ended those listings
    afterwards. A checkout that committed in between left the receipt ending
    a listing already ended -- and `_end` with `sold=False` rewrote
    `sales_lot.status` from `sold` to `dissolved`, two histories the spec says
    never collapse into one, on a lot somebody had just bought. The read
    before the locks now only *chooses what to lock*; the authoritative read
    is the second one, under them.

    Survives, two separate mutations, each measured eight runs of eight:

    - Removing the `lock_for_sale` pass from
      `routers.inventory.receive_items` -- either back below the `db.flush()`
      that follows `set_status`, where the inversion was, or altogether,
      which is the exact pre-fix arrangement -- makes this fail
      `['bought', 'stale']`. **Not** `'deadlock'`, on this interleaving:
      the checkout reaches the member row first, the receipt's unlocked
      UPDATE queues behind it and then writes through a version that has
      moved, so the loss lands as `StaleDataError` rather than as a Postgres
      abort. The abort is the other possible loss of the same inversion --
      the receipt holding the member row while waiting on the lot -- and it
      was not observed in sixteen mutated runs. Both are a 500 for an
      operator, and the pass removes both: it locks and re-reads the member
      row before writing it, which is the same false conflict
      `offering_writes._lock_items`' docstring describes.
    - Ending the listings from the *first* `offers_holding` read instead of
      the second makes it fail on `lot.status`: `AssertionError: dissolved`,
      after a completed sale.

    Stability: **16 runs, 16 passes, and the outcome was
    `['bought', 'marked']` all sixteen times**, measured. The checkout always
    reaches the lot row first here, so **the `else` branch below has never
    been observed to run.** It is kept because which side wins is a property
    of this machine's timing rather than of the code, and a test asserting
    only the observed branch would quietly stop checking anything the day
    that changed. The state it describes is covered by a test that always
    runs: `test_marking_a_coin_missing_first_refuses_the_checkout_of_its_lot`
    below, which is the same pair in a fixed order rather than a race.
    """
    members = [_seed_item(committed) for _ in range(2)]
    store_id = _store_venue_id(committed)
    listing_id, customer_id, admin_id = _seed_lot_listing_and_buyer(
        committed, members, store_id
    )
    barrier = threading.Barrier(2)

    def buy_the_lot() -> Outcome:
        with committed() as session:
            try:
                buyer = session.get_one(Customer, customer_id)
                operator = session.get_one(User, admin_id)
                barrier.wait(timeout=10)
                order_writes.place_order(
                    session,
                    buyer,
                    [order_writes.Line(listing_id=listing_id, quantity=1)],
                    operator,
                )
                session.commit()
                return "bought"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except (HTTPException, IntegrityError):
                session.rollback()
                return "refused"
            except StaleDataError:
                session.rollback()
                return "stale"

    def mark_it_missing() -> Outcome:
        with committed() as session:
            try:
                operator = session.get_one(User, admin_id)
                payload = ReceiveRequest(
                    item_ids=[members[0]],
                    outcome="missing",
                    acknowledge_for_sale=True,
                )
                barrier.wait(timeout=10)
                # The router handler itself, not a re-implementation of it:
                # the sequence under test is the one `receive_items` runs,
                # including the flush that used to come before the lot lock.
                # It commits internally, as `update_order_status` does in
                # `test_order_revision_race.py`.
                receive_items(payload, session, operator)
                return "marked"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except (HTTPException, IntegrityError):
                session.rollback()
                return "refused"
            except StaleDataError:
                session.rollback()
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(buy_the_lot)
        second = pool.submit(mark_it_missing)
        outcomes = sorted([first.result(), second.result()])

    assert "deadlock" not in outcomes, outcomes
    assert "stale" not in outcomes, outcomes
    # The receipt is never refused: it acknowledged the for-sale warning.
    assert "marked" in outcomes, outcomes
    assert outcomes in (["bought", "marked"], ["marked", "refused"]), outcomes

    with committed() as verify:
        listing = verify.get_one(Listing, listing_id)
        assert listing.sales_lot_id is not None
        lot = verify.get_one(SalesLot, listing.sales_lot_id)
        # Either way the offer is over and the named coin is missing.
        assert listing.status is ListingStatus.ended
        assert verify.get_one(InventoryItem, members[0]).status.code == "missing"
        if "bought" in outcomes:
            # The sale stands whole. `sold`, **not** `dissolved`: the receipt
            # arrived after the sale had already ended this listing, so it has
            # no offer left to end and must not rewrite the lot's history.
            # This is the assertion the first version of the fix failed.
            assert lot.status is SalesLotStatus.sold, lot.status
            assert verify.get_one(Listing, listing_id).quantity_available == 0
            for member_id in members:
                assert (
                    verify.get_one(InventoryItem, member_id).disposition.code == "sold"
                )
            shares = list(
                verify.scalars(
                    select(SalesOrderItemShare).where(
                        SalesOrderItemShare.inventory_item_id.in_(members)
                    )
                ).all()
            )
            assert sorted(share.inventory_item_id for share in shares) == sorted(
                members
            )
            assert sum((share.amount for share in shares), Decimal("0.00")) == Decimal(
                "250.00"
            )
        else:
            # The receipt stands whole: the offer ended, the lot dissolved --
            # `dissolved`, not `sold`, because nothing was bought -- and no
            # order line exists against the listing at all.
            assert lot.status is SalesLotStatus.dissolved, lot.status
            assert (
                verify.scalar(
                    select(SalesOrderItem.id).where(
                        SalesOrderItem.listing_id == listing_id
                    )
                )
                is None
            )


def test_marking_a_coin_missing_first_refuses_the_checkout_of_its_lot(
    committed: sessionmaker[Session],
) -> None:
    """The receipt-first ordering, fixed rather than raced.

    The deterministic companion to
    `test_buying_a_lot_races_marking_one_of_its_coins_missing`. That race is
    honest about which side wins -- measured sixteen of sixteen, the checkout
    does -- so the state a receipt-first ordering leaves would otherwise be
    asserted only in a branch nothing ever executes. Here the order is
    imposed: the receipt commits, and only then does the checkout run.

    Not a concurrency guarantee, and it does not pretend to be one. It is the
    *outcome* half: ending a lot's offer dissolves the lot (there is no
    "withdraw but keep the group"), so the other coins come back to the
    drawer individually and the cart that held the lot is told the offer is
    over rather than selling a group one of whose coins is missing.
    """
    members = [_seed_item(committed) for _ in range(2)]
    store_id = _store_venue_id(committed)
    listing_id, customer_id, admin_id = _seed_lot_listing_and_buyer(
        committed, members, store_id
    )

    with committed() as session:
        receive_items(
            ReceiveRequest(
                item_ids=[members[0]], outcome="missing", acknowledge_for_sale=True
            ),
            session,
            session.get_one(User, admin_id),
        )

    with committed() as session:
        buyer = session.get_one(Customer, customer_id)
        operator = session.get_one(User, admin_id)
        with pytest.raises(HTTPException) as excinfo:
            order_writes.place_order(
                session,
                buyer,
                [order_writes.Line(listing_id=listing_id, quantity=1)],
                operator,
            )
        session.rollback()
    assert excinfo.value.status_code == 409
    assert "not currently for sale" in excinfo.value.detail

    with committed() as verify:
        listing = verify.get_one(Listing, listing_id)
        assert listing.status is ListingStatus.ended
        assert listing.sales_lot_id is not None
        lot = verify.get_one(SalesLot, listing.sales_lot_id)
        # `dissolved`, not `sold`: nothing was bought, and the two histories
        # never collapse into one.
        assert lot.status is SalesLotStatus.dissolved
        assert verify.get_one(InventoryItem, members[0]).status.code == "missing"
        # The coin that was fine goes back to the drawer rather than staying
        # `listed` on an offer that no longer exists.
        assert verify.get_one(InventoryItem, members[1]).disposition.code == "held"
        assert _open_lot_ids(committed, members[1]) == []
        assert (
            verify.scalar(
                select(SalesOrderItem.id).where(SalesOrderItem.listing_id == listing_id)
            )
            is None
        )
