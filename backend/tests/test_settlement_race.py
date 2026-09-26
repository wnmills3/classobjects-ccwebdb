"""Two settlements of one auction cannot both run (selling design, phase 4).

Task 4 of the auctions phase, and the only thing in the suite that can
measure either of the two locks Task 3 added. `app.auctions.settle` takes the
`auction` row `FOR UPDATE` as a **new outermost lock level** (ruling R2) and
then takes every coin in the whole auction in **one**
`offering_writes.lock_for_sale` call (ruling R1). Neither is observable from
a single session -- Task 3's own review said so -- because a lock nobody is
contending for behaves exactly like no lock at all.

A third writer joined them after the whole-branch review: `app.auctions.
cancel` is legal on a `closed` auction (ruling R8) and used to take the
`auction` row only as its **last** statement, the inverse of `settle`'s
order. `test_cancelling_an_auction_races_settling_it` is that pair, and it
measures `_lock_auction` from the *other* side -- the two-settlement test
below cannot, because removing the lock from `cancel` leaves `settle`'s
intact and the test still green.

Real, committing sessions, one per thread, released together by a
`threading.Barrier`, exactly as `test_offer_races.py` does it. `TestClient`
cannot do this job at all: it funnels every request through Starlette's
single portal, so two settlements of one auction would run one after the
other and the test would pass against code holding no locks whatsoever.

These tests are **below HTTP on purpose** -- Task 5 has not written the
router yet -- so they assert the exception `settle` actually raises rather
than a status code. The loser raises `auctions.AuctionRefused` with a message
naming the auction's state, which is the class Task 5 maps to 409 (see
`SettlementInputInvalid`'s docstring, which fixes that mapping and its 422
sibling). Every *other* way a settlement can lose is named and asserted
against separately -- `SaleRefused`, `StaleDataError`, `LockSetChanged`, a
Postgres abort -- for the reason `test_offer_races.py` already asserts
`"stale"` apart from `"refused"`: a loser is entitled to be refused with a
reason a person can act on, never to a 500. Folding those in with the
refusals is exactly what would make these tests pass against the mutated
code they exist to catch.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from app import auctions
from app.auctions import AuctionRefused, SettlementInputInvalid, SettlementLine
from app.models import (
    Auction,
    AuctionLot,
    AuctionLotResult,
    AuctionStatus,
    ClaimState,
    Customer,
    InventoryItem,
    Listing,
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
    StorageLocation,
    StorageLocationKind,
    User,
    UserRole,
)
from app.offering_writes import LockSetChanged, OfferRefused
from app.routers.inventory import receive_items
from app.sales_writes import SaleRefused
from app.schemas import ReceiveRequest
from app.security import hash_password
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from tests.conftest import (
    ClaimInvariantViolation,
    build_item,
    check_all_invariants,
)

#: What every item this file commits is titled, and what `_cleanup_rows`
#: matches them on. One literal, because the seed and the cleanup filter have
#: to agree or the cleanup silently grades and deletes nothing.
RACE_TITLE = "RACE Settlement Contested Coin"

#: The prefix for every venue and storage location this file makes.
#: Deliberately **not** `race-`, which is what `test_offer_races.py`'s own
#: cleanup deletes on: two files sweeping one prefix is two files that must
#: never run in the same session, and nothing enforces that.
RACE_PREFIX = "settle-race"

RACE_ADMIN_EMAIL = "settle-race-admin@example.com"

#: The buyer the settlement grid names. A real username rather than the
#: house's undisclosed buyer, so the customer row this file writes is
#: identifiable -- though `_cleanup_rows` matches on the venue, not on this.
RACE_BUYER = "RaceBidder"

Outcome = str


def _cleanup_rows(cleanup: Session) -> None:
    """Check all five suite invariants against these rows, then delete regardless.

    The same shape, and for the same reasons, as
    `test_offer_races.py::_cleanup_race_rows`: every test here takes
    `committed`, not `db`, so `conftest.py`'s autouse `_claim_invariant`
    fixture never runs for any of them and an invariant not called here is
    one this file is exempt from. The check runs in a `try` and the deletes
    in its `finally`, so a genuine violation still fails the suite while the
    rows that caused it cannot poison every later `db`-using test in the same
    `pytest` run.

    Deletion order, parent by parent, each step naming what makes it
    necessary:

    1. `auction_lot` first of all. It is RESTRICT onto `auction`, `listing`
       **and** `customer`, so it blocks three of the deletes below and
       nothing blocks it.
    2. `auction`, now unreferenced.
    3. `sales_order` -- its items, shares, fees and change rows are all
       `ondelete="CASCADE"` from it, so one statement removes the lot of
       them, and it must precede `listing`, `inventory_item` and `customer`,
       each of which a cascaded child holds RESTRICT on.
    4. `customer`, then the administrator this file made.
    5. `offer_claim`, which points at both `listing` and `inventory_item`;
       matched on either column for the reason the offer races give -- a
       lot's claims name a *member* item while holding the **lot** listing,
       whose own `inventory_item_id` is NULL.
    6. `listing`, matched the same two ways, then `sales_lot_item` and
       `sales_lot`: `listing.sales_lot_id` is RESTRICT, so the listings go
       first, and `sales_lot_item.inventory_item_id` is RESTRICT, so they go
       before the items. `lot_ids` is materialised into a Python list before
       anything is deleted, because it is derived from `sales_lot_item` and
       would read back empty once those rows were gone.
    7. `inventory_item`. `item_status_history` and `location_history` are
       CASCADE from it, which is also what frees the storage locations below:
       `location_history.storage_location_id` is RESTRICT.
    8. `storage_location`, then `sales_venue` -- both matched on this file's
       own prefix.
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
    race_venue_ids = select(SalesVenue.id).where(
        SalesVenue.code.like(f"{RACE_PREFIX}-%")
    )
    race_customer_ids = select(Customer.id).where(
        Customer.sales_venue_id.in_(race_venue_ids)
    )
    race_auction_ids = select(Auction.id).where(
        Auction.sales_venue_id.in_(race_venue_ids)
    )
    try:
        check_all_invariants(cleanup)
    finally:
        lot_ids = list(cleanup.scalars(race_lot_ids).all())
        cleanup.query(AuctionLot).filter(
            AuctionLot.auction_id.in_(race_auction_ids)
        ).delete(synchronize_session=False)
        cleanup.query(Auction).filter(Auction.id.in_(race_auction_ids)).delete(
            synchronize_session=False
        )
        cleanup.query(SalesOrder).filter(
            SalesOrder.customer_id.in_(race_customer_ids)
        ).delete(synchronize_session=False)
        cleanup.query(Customer).filter(Customer.id.in_(race_customer_ids)).delete(
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
        cleanup.query(StorageLocation).filter(
            StorageLocation.institution.like(f"{RACE_PREFIX}-%")
        ).delete(synchronize_session=False)
        cleanup.query(SalesVenue).filter(
            SalesVenue.code.like(f"{RACE_PREFIX}-%")
        ).delete(synchronize_session=False)
        cleanup.commit()


@pytest.fixture
def committed(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Real, committing sessions; removes every row the race creates.

    Checks the five suite-wide invariants against those rows *before*
    deleting them, for the reason `test_offer_races.py`'s twin gives: this
    fixture is requested by name, pytest tears an explicitly-requested
    fixture down before an autouse one the test never named, and without the
    check here the suite-wide `_claim_invariant` would grade nothing at all
    for the one file that commits a whole settlement for real.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    with factory() as cleanup:
        _cleanup_rows(cleanup)


def _house_venue(factory: sessionmaker[Session], code: str) -> int:
    """An auction house that sells on the owner's behalf, real and committed.

    An `auction_house` platform specifically, because it is the only kind
    `auctions.consign` accepts -- a live or marketplace auction never leaves
    the premises -- and the consignment race below needs one.
    """
    with factory() as session:
        kind_id = session.execute(
            select(SalesVenueKind.id).where(SalesVenueKind.code == "auction_house")
        ).scalar_one()
        venue = SalesVenue(
            code=code,
            name=f"{RACE_PREFIX}-house",
            sales_venue_kind_id=kind_id,
            commission_rate=Decimal("0.20"),
        )
        session.add(venue)
        session.commit()
        return venue.id


def _drawer(factory: sessionmaker[Session]) -> int:
    """A plain, non-consigned location for returned coins to come back to."""
    with factory() as session:
        kind_id = session.execute(
            select(StorageLocationKind.id).where(StorageLocationKind.code == "home")
        ).scalar_one()
        location = StorageLocation(
            storage_location_kind_id=kind_id,
            institution=f"{RACE_PREFIX}-home",
            identifier="Drawer 1",
        )
        session.add(location)
        session.commit()
        return location.id


def _admin(factory: sessionmaker[Session]) -> int:
    """The operator who settles, real and committed."""
    with factory() as session:
        user = User(
            email=RACE_ADMIN_EMAIL,
            full_name="Settle Race Admin",
            hashed_password=hash_password("racepassword"),
            role=UserRole.manager,
        )
        session.add(user)
        session.commit()
        return user.id


def _closed_auction(
    factory: sessionmaker[Session],
    venue_id: int,
    *,
    lot_count: int,
    consigned: bool = False,
    closed: bool = True,
) -> tuple[int, list[int], list[int]]:
    """A closed auction of `lot_count` single-coin lots, real and committed.

    Returns `(auction_id, auction_lot_ids, item_ids)` -- **ids, not
    instances**, for the reason every helper in `test_offer_races.py` returns
    ids: each racing thread gets a session of its own, and an instance loaded
    here belongs to a session that is already closed by the time the race
    starts.

    Each coin carries an exact cost basis with `tax_rate=0`, so `total_cost`
    (generated as `item_cost + shipping_cost + sales_tax`) equals `item_cost`
    and the share allocation below is predictable to the cent.

    `consigned=True` runs `consign` between `schedule` and `close`, which is
    the state ruling R13 is about: `close` leaves `consigned_on` set, so a
    closed auction's coins may still be sitting at the house.

    `closed=False` stops at `scheduled`, for the race between `consign` and
    `cancel`, which is the one status both accept.
    """
    with factory() as session:
        venue = session.get_one(SalesVenue, venue_id)
        auction = Auction(
            sales_venue_id=venue.id,
            title="RACE settlement sale",
            external_id="RACE-2026-09",
        )
        session.add(auction)
        session.flush()
        lot_ids: list[int] = []
        item_ids: list[int] = []
        for number in range(1, lot_count + 1):
            item = build_item(
                session,
                title=RACE_TITLE,
                item_cost=Decimal("100.00"),
                tax_rate=Decimal("0"),
            )
            item_ids.append(item.id)
            auction_lot = auctions.add_lot(
                session,
                auction,
                item,
                lot_number=str(number),
                reserve=None,
                price=Decimal("10.00"),
            )
            lot_ids.append(auction_lot.id)
        auctions.schedule(session, auction)
        if consigned:
            auctions.consign(session, auction, on_date=date(2026, 9, 1))
        if closed:
            auctions.close(session, auction)
        session.commit()
        return auction.id, lot_ids, item_ids


def _settlement_grid(lot_ids: Sequence[int]) -> list[SettlementLine]:
    """Two lots sold to one buyer and one left unsold.

    Both halves of `settle` on purpose. The sold pair is what writes an order,
    its lines and one share per coin -- the thing that must not happen twice.
    The unsold lot is what ends an offer with no sale and sends its coin back
    to the drawer, which is the half a second settlement would either repeat
    or refuse.
    """
    return [
        SettlementLine(
            lot_ids[0], AuctionLotResult.sold, Decimal("140.00"), RACE_BUYER
        ),
        SettlementLine(
            lot_ids[1], AuctionLotResult.sold, Decimal("260.00"), RACE_BUYER
        ),
        SettlementLine(lot_ids[2], AuctionLotResult.unsold),
    ]


def test_two_settlements_of_one_auction_leave_one_set_of_orders(
    committed: sessionmaker[Session],
) -> None:
    """Two operators settle one closed auction at once: exactly one gets to.

    The loser waits on the `auction` row, re-reads `settled`, and is refused
    by `settle`'s own status check with a message naming the state -- an
    `AuctionRefused`, which is the class Task 5 maps to **409**. Nothing is
    written twice: one order, two lines, one share per sold coin, and the
    unsold coin comes home once.

    Survives: removing `.with_for_update()` from `auctions._lock_auction`
    (`app/auctions.py`) makes this fail. Measured, ten runs of ten:
    `AssertionError: ['sale_refused', 'settled']`. Both settlements then read
    `closed` and both go on to record the sale; they serialize on the *items*
    instead, in `offering_writes.lock_for_sale`, and the loser reaches
    `sales_writes.record_sale_lines` to be told
    `Listing 1 on settle-race-house is not on offer (ended)` -- observed
    verbatim; a `SaleRefused`, raised about a *listing*, after the loser had
    already written three `auction_lot.result` values inside its savepoint.
    **This is the precise reason `"sale_refused"` is asserted against
    separately rather than folded in with `"refused"`:**
    fold it in and this test passes against an auction row nobody locks,
    which is the one guarantee it exists to prove.

    That the damage is *contained* in the mutated case is `record_sale_lines`
    doing its own locked re-read, not this lock working -- an accidental
    backstop, and the distinction matters because it is not general: a
    settlement whose lots were all `unsold` calls `record_sale_lines` for
    nobody, so nothing re-reads on its behalf at all. **Measured rather than
    argued**, by running this same race with an all-`unsold` grid against the
    same mutation: five runs of five, `['settled', 'stale']` --
    `StaleDataError` on `auction.version`, a 500 with nothing for an operator
    to act on, where the sold grid at least produced a refusal. The grid this
    test uses is the *milder* of the two failures, which is the safe
    direction for a proof to err in.

    Does **not** survive removing the `offering_writes.lock_for_sale` pass
    from `settle`, and that is stated rather than left as a gap: measured ten
    runs of ten, still green. The auction lock serializes two settlements on
    its own, so the item pass cannot be reached by *this* race at all -- it
    is there for a settlement racing some **other** writer, which is what
    `test_settling_a_consigned_auction_races_a_coin_going_missing` measures.
    The two tests are a pair and neither covers the other's mutation.

    Determinism: there is no timing window to lose. The barrier releases both
    threads and `SELECT ... FOR UPDATE` queues them; whichever arrives second
    blocks until the first commits and then reads the row it wrote. Which
    thread wins varies and nothing here depends on it.
    """
    venue_id = _house_venue(committed, f"{RACE_PREFIX}-two")
    admin_id = _admin(committed)
    auction_id, lot_ids, item_ids = _closed_auction(committed, venue_id, lot_count=3)
    lines = _settlement_grid(lot_ids)
    barrier = threading.Barrier(2)
    #: The loser's message, kept so the assertions can check *why* it was
    #: refused and not merely that it was. `AuctionRefused` is also what a
    #: grid problem raises, so an outcome string alone would score
    #: "this auction is not closed" as a won race. `list.append` is the whole
    #: of the sharing, which is atomic.
    refusals: list[str] = []

    def settle_it() -> Outcome:
        with committed() as session:
            try:
                # Auction and operator loaded before the barrier, the way a
                # request handler already holds them, so the first statement
                # either thread issues after the barrier is `_lock_auction`
                # itself rather than two `get_one` round trips.
                auction = session.get_one(Auction, auction_id)
                operator = session.get_one(User, admin_id)
                barrier.wait(timeout=10)
                auctions.settle(
                    session, auction, lines=lines, fees={}, settled_by=operator
                )
                session.commit()
                return "settled"
            except SettlementInputInvalid:
                # Checked before the wider class, because it is a subclass:
                # an `except AuctionRefused` above this would swallow it and
                # a 422 refusal would be scored as the 409 this race expects.
                session.rollback()
                return "input_invalid"
            except AuctionRefused as exc:
                session.rollback()
                refusals.append(str(exc))
                return "refused"
            except SaleRefused:
                session.rollback()
                return "sale_refused"
            except LockSetChanged:
                session.rollback()
                return "lock_set_changed"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except IntegrityError:
                session.rollback()
                return "integrity_error"
            except StaleDataError:
                session.rollback()
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        # Both submitted before either is waited on, for the reason
        # `test_offer_races.py` gives: waiting on the first result before
        # submitting the second leaves the barrier with nobody to meet.
        first = pool.submit(settle_it)
        second = pool.submit(settle_it)
        outcomes = sorted([first.result(), second.result()])

    assert outcomes == ["refused", "settled"], outcomes
    # The *reason*, not just the refusal: the loser re-read the row the winner
    # wrote and was turned away by `settle`'s own status check.
    expected = f"auction #{auction_id} is settled, so it cannot be settled"
    assert refusals == [expected], refusals

    with committed() as verify:
        auction = verify.get_one(Auction, auction_id)
        assert auction.status is AuctionStatus.settled
        # Never consigned, so nothing was ever holding these coins.
        assert auction.consigned_on is None

        lots = list(
            verify.scalars(
                select(AuctionLot)
                .where(AuctionLot.auction_id == auction_id)
                .order_by(AuctionLot.id)
            ).all()
        )
        assert [row.result for row in lots] == [
            AuctionLotResult.sold,
            AuctionLotResult.sold,
            AuctionLotResult.unsold,
        ]
        assert [row.hammer_price for row in lots] == [
            Decimal("140.00"),
            Decimal("260.00"),
            None,
        ]

        # One buyer, one order. The outcome strings alone would pass on two
        # orders against one customer, or on two customers, so both are
        # asserted: a second settlement that got through would write either.
        customers = list(
            verify.scalars(
                select(Customer).where(Customer.sales_venue_id == venue_id)
            ).all()
        )
        assert [customer.venue_username for customer in customers] == [RACE_BUYER]
        orders = list(
            verify.scalars(
                select(SalesOrder).where(SalesOrder.customer_id == customers[0].id)
            ).all()
        )
        assert len(orders) == 1, orders
        assert orders[0].total_amount == Decimal("400.00")

        sold_listing_ids = [row.listing_id for row in lots[:2]]
        for listing_id in sold_listing_ids:
            # **No coin sold twice**, stated the way the schema can show it:
            # exactly one order line per listing and exactly one share per
            # coin. A second winning settlement doubles both.
            assert (
                len(
                    list(
                        verify.scalars(
                            select(SalesOrderItem).where(
                                SalesOrderItem.listing_id == listing_id
                            )
                        ).all()
                    )
                )
                == 1
            )
        for item_id in item_ids[:2]:
            shares = list(
                verify.scalars(
                    select(SalesOrderItemShare).where(
                        SalesOrderItemShare.inventory_item_id == item_id
                    )
                ).all()
            )
            assert len(shares) == 1, shares
            assert verify.get_one(InventoryItem, item_id).disposition.code == "sold"

        # The unsold coin: offer over, lot dissolved -- not `sold` -- and the
        # coin back in the drawer rather than left `listed` on an offer that
        # no longer exists.
        unsold = verify.get_one(Listing, lots[2].listing_id)
        assert unsold.status is ListingStatus.ended
        assert unsold.sales_lot_id is not None
        assert (
            verify.get_one(SalesLot, unsold.sales_lot_id).status
            is SalesLotStatus.dissolved
        )
        assert verify.get_one(InventoryItem, item_ids[2]).disposition.code == "held"
        assert (
            verify.scalar(
                select(SalesOrderItemShare.id).where(
                    SalesOrderItemShare.inventory_item_id == item_ids[2]
                )
            )
            is None
        )


def test_a_forced_violation_still_leaves_the_database_clean(
    committed: sessionmaker[Session],
) -> None:
    """`_cleanup_rows` must delete even when its own check raises.

    The same guarantee `test_offer_races.py` proves for its own cleanup, and
    it has to be proved here separately because this is a different function
    over a different set of tables -- an auction, its lots, its orders and
    its storage locations, none of which that file deletes.

    A settled auction is built for real and then poisoned by hand: one sold
    coin's claim is flipped to `released` while its listing still stands,
    which is the mismatch `check_claim_invariant` exists to find and which no
    write path in `app/` produces. Without the `try`/`finally` split inside
    `_cleanup_rows`, the raise would leave every row behind -- an auction, two
    orders' worth of lines and shares, four coins -- and every later
    `db`-using test in the same `pytest` run would fail the suite-wide
    invariant against them.
    """
    venue_id = _house_venue(committed, f"{RACE_PREFIX}-poison")
    admin_id = _admin(committed)
    auction_id, lot_ids, item_ids = _closed_auction(committed, venue_id, lot_count=3)

    with committed() as session:
        auctions.settle(
            session,
            session.get_one(Auction, auction_id),
            lines=_settlement_grid(lot_ids),
            fees={},
            settled_by=session.get_one(User, admin_id),
        )
        session.commit()

    with committed() as session:
        # The unsold lot's listing is the one still holding a claim on a coin
        # after settlement, so flipping that claim is the mismatch the
        # invariant catches. Written by hand, bypassing `offering_writes` on
        # purpose, exactly as `test_offer_races.py` poisons its own pair.
        claim = session.scalars(
            select(OfferClaim).where(OfferClaim.inventory_item_id == item_ids[0])
        ).first()
        assert claim is not None
        claim.state = ClaimState.active
        session.commit()

    with committed() as cleanup, pytest.raises(ClaimInvariantViolation):
        _cleanup_rows(cleanup)

    with committed() as verify:
        assert verify.get(Auction, auction_id) is None
        assert (
            verify.scalar(
                select(InventoryItem.id).where(InventoryItem.source_title == RACE_TITLE)
            )
            is None
        )
        assert (
            verify.scalar(select(SalesVenue.id).where(SalesVenue.id == venue_id))
            is None
        )


def test_settling_a_consigned_auction_races_a_coin_going_missing(
    committed: sessionmaker[Session],
) -> None:
    """A settlement and a receipt against one of its coins never deadlock.

    The race that measures ruling **R1** -- the single
    `offering_writes.lock_for_sale` pass covering every coin in the whole
    auction -- which the two-settlement race above cannot reach at all,
    because the `auction` row lock serializes two settlements before either
    of them takes a coin.

    Both operations are legitimate, both take their rows, and **neither is a
    500** -- which is the whole guarantee. `"deadlock"` and `"stale"` are
    asserted against separately, for the reason `test_offer_races.py` gives:
    a loser is entitled to be refused with a reason, never to a 500 on a
    money path.

    **Two outcomes are legal now, and both are asserted** (changed by the
    whole-branch review's Important #2; it used to assert `["marked",
    "settled"]` alone). `routers.inventory._refuse_auction_lots` refuses a
    receipt that would end an auction-format listing -- a coin in an auction
    goes missing in two steps, remove the lot then record the loss, the trade
    ruling R25 already accepted for Record sale. That refusal is raised
    **after** `offering_writes.lock_for_sale` and under its locks, beside
    `refuse_if_lot_unheld`, so the receipt still acquires exactly the rows it
    always did: this is still a race test and not a refusal test, and the
    mutation below still reds it, which is the proof rather than the claim.
    Which pair a run produces depends only on who reaches the item locks
    first:

    - the **receipt** first: it holds the rows, its authoritative
      `offers_holding` re-read still sees a live auction-format listing, and
      it refuses with 409 and rolls back -- `["refused", "settled"]`;
    - the **settlement** first: it commits, so that same re-read finds no
      live offer left to refuse and the coin is recorded missing --
      `["marked", "settled"]`.

    **Both were measured, and the split is not subtle.** Running the whole
    file: `["refused", "settled"]`, fifteen runs of fifteen. Running this
    test with the file's two earlier tests deselected (`-k races`):
    `["marked", "settled"]`, ten runs of ten. Nothing in the test changed
    between those two -- only how warm the process was when the barrier
    released -- which is exactly why the assertion names both pairs instead
    of whichever one the machine happened to produce that afternoon.
    `acknowledge_for_sale=True` is still the operator saying "I know this
    coin is on offer, record it missing anyway", which is what keeps
    `sale_state.guard` from refusing before the locks are taken at all.

    Every lot is `unsold` and the auction is **consigned**, and both are
    load-bearing rather than incidental. That combination is the one path on
    which settlement writes an `inventory_item` row of its own --
    `_return_from_consignment` -> `lifecycle_writes.set_location` -- taking
    the coin's row lock directly. Every other write settlement makes goes
    through `sales_writes` or `offering_writes`, which take their rows
    through `lock_for_sale` and therefore in the canonical order no matter
    what `settle` did first. The contested coin is the **last** lot's, so
    settlement holds it (from the returns pass) for as long as possible
    before asking for its lot row (in the endings pass) -- which is the
    window the receipt has to arrive in.

    Survives: removing the `offering_writes.lock_for_sale(...)` pass from
    `auctions.settle` (`app/auctions.py`, step 3 of its docstring) makes this
    fail. Measured, ten runs of ten, the same result every time:
    `AssertionError: ['deadlock', 'settled']`. Without the pass, settlement's
    first acquisition is `set_location`'s UPDATE of a coin, taken with **no
    lot row held**, and it asks for that coin's lot row afterwards through
    `end_offer` -- items before lots, the exact inversion
    `docs/specs/lock-order-design.md` exists to prevent, against a receipt
    that `lock_for_sale` has already put in the canonical order, lot row
    first.

    **The receipt is the victim, not the settlement**, and that is worth
    naming rather than rounding off to "one of them deadlocks": ten of ten,
    Postgres chose the operator marking a coin missing. Observed verbatim,
    `sqlalchemy.exc.OperationalError` wrapping
    `psycopg.errors.DeadlockDetected`:

        deadlock detected
        DETAIL:  Process A waits for ShareLock on transaction T1; blocked by
        process B.  Process B waits for ShareLock on transaction T2; blocked
        by process A.
        CONTEXT:  while locking tuple (0,18) in relation "inventory_item"

    That is a 500 on `POST /inventory/receive` -- an ordinary admin action,
    with nothing wrong with it and nothing the operator could have done
    differently -- so the damage the pass prevents lands on a *different*
    request than the one settlement was mutated in.

    **The direction is what matters, not the presence of a lock.** Inverting
    the single owner would move both writers at once and produce no cycle at
    all (measured on the phase-3 branch, `test_buying_a_lot_races_offering_
    one_of_its_coins`); what reproduces a deadlock is a caller that *bypasses*
    the owner, which is precisely what removing this pass makes `settle` do.

    Does **not** survive removing `.with_for_update()` from `_lock_auction`:
    measured ten runs of ten, still green. Nothing else in the codebase takes
    an `auction` row, so a race with a non-auction writer cannot contend for
    it. That mutation belongs to the test above, and these two are a pair --
    neither covers the other's.
    """
    venue_id = _house_venue(committed, f"{RACE_PREFIX}-consign")
    admin_id = _admin(committed)
    drawer_id = _drawer(committed)
    auction_id, lot_ids, item_ids = _closed_auction(
        committed, venue_id, lot_count=6, consigned=True
    )
    lines = [SettlementLine(lot_id, AuctionLotResult.unsold) for lot_id in lot_ids]
    contested = item_ids[-1]
    barrier = threading.Barrier(2)

    def settle_it() -> Outcome:
        with committed() as session:
            try:
                auction = session.get_one(Auction, auction_id)
                operator = session.get_one(User, admin_id)
                barrier.wait(timeout=10)
                auctions.settle(
                    session,
                    auction,
                    lines=lines,
                    fees={},
                    settled_by=operator,
                    returned_to_location_id=drawer_id,
                )
                session.commit()
                return "settled"
            except SettlementInputInvalid:
                session.rollback()
                return "input_invalid"
            except AuctionRefused:
                session.rollback()
                return "refused"
            except OfferRefused:
                session.rollback()
                return "offer_refused"
            except SaleRefused:
                session.rollback()
                return "sale_refused"
            except LockSetChanged:
                session.rollback()
                return "lock_set_changed"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except IntegrityError:
                session.rollback()
                return "integrity_error"
            except StaleDataError:
                session.rollback()
                return "stale"

    def mark_it_missing() -> Outcome:
        with committed() as session:
            try:
                operator = session.get_one(User, admin_id)
                payload = ReceiveRequest(
                    item_ids=[contested],
                    outcome="missing",
                    acknowledge_for_sale=True,
                )
                barrier.wait(timeout=10)
                # The router handler itself, not a re-implementation: the
                # sequence under test is the one `receive_items` runs,
                # including the `lock_for_sale` pass its own deadlock fix
                # added. It commits internally.
                receive_items(payload, session, operator)
                return "marked"
            except HTTPException:
                session.rollback()
                return "refused"
            except LockSetChanged:
                session.rollback()
                return "lock_set_changed"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except IntegrityError:
                session.rollback()
                return "integrity_error"
            except StaleDataError:
                session.rollback()
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(settle_it)
        second = pool.submit(mark_it_missing)
        outcomes = sorted([first.result(), second.result()])

    assert "deadlock" not in outcomes, outcomes
    assert "stale" not in outcomes, outcomes
    assert "lock_set_changed" not in outcomes, outcomes
    # Both legal, and which one happens is decided by who reaches the item
    # locks first -- see this test's docstring. Neither is a 500, which is
    # the guarantee.
    assert outcomes in (["marked", "settled"], ["refused", "settled"]), outcomes

    with committed() as verify:
        auction = verify.get_one(Auction, auction_id)
        assert auction.status is AuctionStatus.settled
        # Cleared once every lot that had to come back has: the house is not
        # holding anything of this auction's any more.
        assert auction.consigned_on is None
        lots = list(
            verify.scalars(
                select(AuctionLot)
                .where(AuctionLot.auction_id == auction_id)
                .order_by(AuctionLot.id)
            ).all()
        )
        assert [row.result for row in lots] == [AuctionLotResult.unsold] * 6
        for row in lots:
            listing = verify.get_one(Listing, row.listing_id)
            assert listing.status is ListingStatus.ended
            assert listing.sales_lot_id is not None
            # `dissolved`, not `sold`: nothing was bought, and the two
            # histories never collapse into one.
            assert (
                verify.get_one(SalesLot, listing.sales_lot_id).status
                is SalesLotStatus.dissolved
            )
        # Nothing sold, so no order and no share exists at all -- the
        # assertion a settlement that ran twice, or ran on a lot the receipt
        # had already taken, would break.
        assert (
            verify.scalar(
                select(SalesOrderItemShare.id).where(
                    SalesOrderItemShare.inventory_item_id.in_(item_ids)
                )
            )
            is None
        )
        # The receipt's own effect, and the only thing the two legal
        # interleavings differ on: it recorded the loss when settlement had
        # already ended the offers, and was refused while they were still
        # live.
        expected = "missing" if "marked" in outcomes else "received"
        assert verify.get_one(InventoryItem, contested).status.code == expected
        for item_id in item_ids:
            item = verify.get_one(InventoryItem, item_id)
            assert item.disposition.code == "held"
            # **Every** coin is home now, the contested one included, at the
            # location the settlement was told to return them to rather than
            # still sitting in the house's consigned location. Before
            # `_refuse_auction_lots`, a receipt that won this race ended the
            # lot's offer, so settlement's `offered_items` came back empty
            # for that lot and left the coin at the auction house -- which is
            # why this assertion used to have to skip it.
            assert item.storage_location_id == drawer_id


def test_cancelling_an_auction_races_settling_it(
    committed: sessionmaker[Session],
) -> None:
    """Cancel and settle the same closed auction at once: no deadlock, no 500.

    The race the whole-branch review's **Critical #1** named, and the one
    nothing here covered: the two settlement tests above race settle against
    settle and against a receipt, and nothing cancelled. Both endpoints are
    admin-only, both buttons are present on a `closed` auction in the
    console, and ruling **R8** made `cancel` legal on `closed` -- so two
    tabs, or two operators, is all it takes.

    **The defect.** `settle` took the `auction` row FOR UPDATE first
    (`auctions._lock_auction`, ruling R2) and then the coins through
    `offering_writes.lock_for_sale`: auction -> lots -> items -> listings.
    `cancel` took no auction row at all, ended each lot's offer through
    `offering_writes` and wrote `UPDATE auction SET status='cancelled'` as
    its **last** statement: lots -> items -> listings -> auction. R2's own
    argument that the new outermost level "cannot invert" was true when
    written, because `cancel` was then gated to
    `draft`/`scheduled`/`consigned` while `settle` proceeds only on `closed`
    -- R8 widened `cancel` and created the second party, and nothing
    re-derived R2 after it. The fix finishes R2 rather than undoing it:
    `_lock_auction` is now `cancel`'s first statement too.

    **The mutation that reds this test:** delete the
    `auction = _lock_auction(db, auction)` line from `app.auctions.cancel`.
    Measured, ten runs of ten red, in two shapes:

    - `AssertionError: ['cancelled', 'deadlock']`, seven of ten. `settle` is
      the victim, `sqlalchemy.exc.OperationalError` wrapping
      `psycopg.errors.DeadlockDetected`, observed verbatim:

          deadlock detected
          DETAIL:  Process 3544 waits for ShareLock on transaction 2027215;
          blocked by process 56560.  Process 56560 waits for ShareLock on
          transaction 2027214; blocked by process 3544.
          HINT:  See server log for query details.
          CONTEXT:  while locking tuple (0,2) in relation "sales_lot"

      Neither router catches `OperationalError` -- `routers.auctions`
      catches only `StaleDataError` -- so that is an **HTTP 500 on a money
      path**, with a non-deterministic victim.
    - `AssertionError: ['settled', 'stale']`, three of ten. `settle` got all
      the way through without contending and `cancel`'s final `UPDATE
      auction ... WHERE version = :v` matched no rows. That one is the
      *clean* 409 described below -- correct behavior for the unlocked
      code, and the reason the mutation has to be judged on both assertions
      rather than on the deadlock alone. Neither shape is reachable once the
      lock is back, which is why the test forbids both.

    Restoring the line makes it green again; fifteen further runs of
    fifteen, no failure. The red is by likelihood, not by construction: a
    run in which `cancel` finishes all six lots before `settle` asks for the
    auction row never overlaps and passes even without the lock. It has not
    been observed, which is what six lots are for.

    **One distinction this test deliberately preserves.** If `settle` commits
    without ever contending, `cancel`'s final `UPDATE auction` matches zero
    rows on `Auction.version` and raises `StaleDataError`, which
    `routers.auctions.cancel_auction` already turns into a clean 409. That
    path is **correct and is not the defect**; only the `DeadlockDetected`
    abort is a 500. With the lock in place the loser never reaches that
    UPDATE at all -- it waits on the auction row, re-reads the status the
    winner wrote and is refused by name -- which is why `"stale"` is
    asserted against separately here rather than folded in with `"refused"`.

    **Which one wins is not fixed, and nothing here depends on it.** Both
    threads queue on `SELECT ... FOR UPDATE` of the same row; the winner
    finishes, the loser re-reads and refuses. So exactly two outcome pairs
    are legal, both asserted, and the loser's message is checked against
    whichever of them happened -- a refusal with the *wrong* reason (a grid
    problem, say, which is also an `AuctionRefused`) would otherwise score as
    a won race. Measured over fifteen runs: settlement won eleven,
    cancellation four, every one of them a clean refusal for the loser.

    Six lots rather than three, to widen the window the mutation needs: with
    the lock removed, `cancel` must be holding one lot's rows when `settle`
    asks for all of them, and six `end_offer` passes is six times the
    opportunity. Every lot is `unsold` and the auction is never consigned, so
    no return location is needed on either side and the test measures the
    lock rather than ruling R13's custody contract.
    """
    venue_id = _house_venue(committed, f"{RACE_PREFIX}-cancel")
    admin_id = _admin(committed)
    auction_id, lot_ids, item_ids = _closed_auction(committed, venue_id, lot_count=6)
    lines = [SettlementLine(lot_id, AuctionLotResult.unsold) for lot_id in lot_ids]
    barrier = threading.Barrier(2)
    #: Every refusal either side raised, so the assertions can check *why*
    #: the loser lost. `list.append` is the whole of the sharing, and it is
    #: atomic.
    refusals: list[str] = []

    def settle_it() -> Outcome:
        with committed() as session:
            try:
                auction = session.get_one(Auction, auction_id)
                operator = session.get_one(User, admin_id)
                barrier.wait(timeout=10)
                auctions.settle(
                    session, auction, lines=lines, fees={}, settled_by=operator
                )
                session.commit()
                return "settled"
            except SettlementInputInvalid:
                # Before the wider class, which is its base -- see the
                # two-settlement race above.
                session.rollback()
                return "input_invalid"
            except AuctionRefused as exc:
                session.rollback()
                refusals.append(str(exc))
                return "refused"
            except OfferRefused:
                session.rollback()
                return "offer_refused"
            except SaleRefused:
                session.rollback()
                return "sale_refused"
            except LockSetChanged:
                session.rollback()
                return "lock_set_changed"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except IntegrityError:
                session.rollback()
                return "integrity_error"
            except StaleDataError:
                session.rollback()
                return "stale"

    def cancel_it() -> Outcome:
        with committed() as session:
            try:
                auction = session.get_one(Auction, auction_id)
                barrier.wait(timeout=10)
                auctions.cancel(session, auction)
                session.commit()
                return "cancelled"
            except AuctionRefused as exc:
                session.rollback()
                refusals.append(str(exc))
                return "refused"
            except OfferRefused:
                session.rollback()
                return "offer_refused"
            except LockSetChanged:
                session.rollback()
                return "lock_set_changed"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except IntegrityError:
                session.rollback()
                return "integrity_error"
            except StaleDataError:
                session.rollback()
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(settle_it)
        second = pool.submit(cancel_it)
        outcomes = sorted([first.result(), second.result()])

    assert "deadlock" not in outcomes, outcomes
    assert "stale" not in outcomes, outcomes
    assert "lock_set_changed" not in outcomes, outcomes
    assert outcomes in (["cancelled", "refused"], ["refused", "settled"]), outcomes

    settled_won = "settled" in outcomes
    expected = (
        f"auction #{auction_id} is already settled"
        if settled_won
        else f"auction #{auction_id} is cancelled, so it cannot be settled"
    )
    assert refusals == [expected], refusals

    with committed() as verify:
        auction = verify.get_one(Auction, auction_id)
        lots = list(
            verify.scalars(
                select(AuctionLot)
                .where(AuctionLot.auction_id == auction_id)
                .order_by(AuctionLot.id)
            ).all()
        )
        if settled_won:
            assert auction.status is AuctionStatus.settled
            # Every lot still there, every one resolved: a cancel that also
            # got through would have deleted these rows.
            assert [row.result for row in lots] == [AuctionLotResult.unsold] * 6
        else:
            assert auction.status is AuctionStatus.cancelled
            # `remove_lot` deletes rather than marks (ruling R11), so a
            # cancelled auction keeps none of its lot rows.
            assert lots == []
        assert auction.consigned_on is None

        # True of both winners, and the reason the loser has to lose
        # *cleanly*: each ends every listing and dissolves every sales lot
        # exactly once, and neither sells anything.
        for item_id in item_ids:
            item = verify.get_one(InventoryItem, item_id)
            assert item.disposition.code == "held"
            claims = list(
                verify.scalars(
                    select(OfferClaim).where(OfferClaim.inventory_item_id == item_id)
                ).all()
            )
            assert [claim.state for claim in claims] == [ClaimState.released], claims
        listings = list(
            verify.scalars(
                select(Listing).where(
                    Listing.sales_lot_id.in_(
                        select(SalesLotItem.sales_lot_id).where(
                            SalesLotItem.inventory_item_id.in_(item_ids)
                        )
                    )
                )
            ).all()
        )
        assert len(listings) == 6, listings
        for listing in listings:
            assert listing.status is ListingStatus.ended
            assert listing.sales_lot_id is not None
            assert (
                verify.get_one(SalesLot, listing.sales_lot_id).status
                is SalesLotStatus.dissolved
            )
        # Nothing sold on either path, so no order, no line and no share --
        # the assertion a settlement that ran *through* a cancel would break.
        assert (
            verify.scalar(
                select(SalesOrderItemShare.id).where(
                    SalesOrderItemShare.inventory_item_id.in_(item_ids)
                )
            )
            is None
        )
        assert (
            verify.scalar(
                select(Customer.id).where(Customer.sales_venue_id == venue_id)
            )
            is None
        )


def test_consigning_an_auction_races_cancelling_it(
    committed: sessionmaker[Session],
) -> None:
    """Consign and cancel the same scheduled auction at once: no deadlock.

    Found by the review of the final fix wave (Important #2). Once `cancel`
    took the `auction` row first, `consign` became the next writer that
    reached an auction's coins before its auction row: its item moves
    flushed before its `UPDATE auction`, so on a `scheduled` auction -- the
    one status both accept -- each could hold what the other wanted.
    `consign` now takes `_lock_auction` first as well.

    Two outcomes are legal and both are asserted: `consign` first, and the
    cancel that follows re-reads `consigned`, brings every coin back to the
    drawer and cancels; or `cancel` first, and `consign` re-reads
    `cancelled` and is refused by name. Either way the auction ends
    cancelled with no coin left at the house.
    """
    venue_id = _house_venue(committed, f"{RACE_PREFIX}-consign")
    drawer_id = _drawer(committed)
    auction_id, _, item_ids = _closed_auction(
        committed, venue_id, lot_count=6, closed=False
    )
    barrier = threading.Barrier(2)
    refusals: list[str] = []

    def consign_it() -> Outcome:
        with committed() as session:
            try:
                auction = session.get_one(Auction, auction_id)
                barrier.wait(timeout=10)
                auctions.consign(session, auction, on_date=date(2026, 9, 1))
                session.commit()
                return "consigned"
            except AuctionRefused as exc:
                session.rollback()
                refusals.append(str(exc))
                return "refused"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except StaleDataError:
                session.rollback()
                return "stale"

    def cancel_it() -> Outcome:
        with committed() as session:
            try:
                auction = session.get_one(Auction, auction_id)
                barrier.wait(timeout=10)
                auctions.cancel(session, auction, returned_to_location_id=drawer_id)
                session.commit()
                return "cancelled"
            except AuctionRefused as exc:
                session.rollback()
                refusals.append(str(exc))
                return "refused"
            except OperationalError:
                session.rollback()
                return "deadlock"
            except StaleDataError:
                session.rollback()
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(consign_it)
        second = pool.submit(cancel_it)
        outcomes = sorted([first.result(), second.result()])

    assert "deadlock" not in outcomes, outcomes
    assert "stale" not in outcomes, outcomes
    assert outcomes in (["cancelled", "consigned"], ["cancelled", "refused"]), outcomes
    if "refused" in outcomes:
        assert refusals == [
            f"auction #{auction_id} is cancelled, so it cannot be marked consigned"
        ], refusals

    with committed() as verify:
        auction = verify.get_one(Auction, auction_id)
        assert auction.status is AuctionStatus.cancelled
        assert auction.consigned_on is None
        for item_id in item_ids:
            item = verify.get_one(InventoryItem, item_id)
            if "consigned" in outcomes:
                # They went to the house and the cancel brought them back.
                assert item.storage_location_id == drawer_id, item_id
            else:
                # They never left: a build_item coin has no location at all.
                assert item.storage_location_id is None, item_id
