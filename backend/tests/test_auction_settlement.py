"""Settling an auction: one transaction, and the money reconciles to the cent.

Task 3 of the auctions phase. `app.auctions.settle` is the second caller
`sales_writes`' single entry point was built for (spec, *Where record-a-sale
lives*), so most of what these tests prove is that settlement hands off to
`sales_writes`, `offering_writes` and `lifecycle_writes` correctly rather
than growing a second copy of fees, shares, endings or location moves --
the same discipline `test_auctions.py` holds Task 2 to.

Three things here cannot be proved by reading the code, and each has a test
whose failure is the evidence:

* **All or nothing.** `test_a_failure_part_way_through_leaves_nothing_written`
  is mutation-verified -- see its own docstring for what was removed and what
  the failure looked like.
* **The cents.** A fee bills per buyer *order* and therefore spans lots, so
  the allocation that divides it is wider than the one that divides a
  hammer price. `test_fees_are_divided_the_same_way_as_the_price` picks
  hammer prices that are **not** proportional to cost, so a per-lot
  allocation produces different numbers and cannot pass.
* **The one obligation `offering_writes.lock_for_sale` leaves to its
  callers.** `test_a_lot_listing_can_never_be_paused_by_another_offer`
  measures the refusal that makes settlement's `end_offer(sold=True)` safe.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal

import pytest
from app import offering_writes, sales_writes
from app.auctions import (
    AuctionRefused,
    SettlementInputInvalid,
    SettlementLine,
    add_lot,
    close,
    consign,
    schedule,
    settle,
)
from app.models import (
    Auction,
    AuctionLot,
    AuctionLotResult,
    AuctionStatus,
    Customer,
    InventoryItem,
    Listing,
    ListingFormat,
    ListingStatus,
    ListingStatusHistory,
    SalesLot,
    SalesOrder,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesOrderStatus,
    SalesVenue,
    StorageLocation,
    StorageLocationKind,
    User,
)
from app.sales_writes import FeeLine
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.conftest import build_lot, item_of

ItemFactory = Callable[..., InventoryItem]
ListingFactory = Callable[..., Listing]

#: Cost bases that divide a price unevenly in one direction and a fee in
#: another, so a test can tell a cost-weighted split from a price-weighted
#: one. `tax_rate=0` everywhere below, because `total_cost` is generated as
#: `item_cost + shipping_cost + sales_tax` and only a zero tax makes it equal
#: `item_cost` exactly -- the same reason `conftest.lot_of_three` does it.
_UNEVEN_COSTS = (Decimal("500.00"), Decimal("300.00"), Decimal("200.00"))


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


#: The house's own number for the sale, carried by the `auction` fixture so
#: `external_order_id` is never `None` in this file. A fixture that left it
#: unset made ruling R18 unwritable by any test: `settle` could have stopped
#: copying it and every assertion would still have compared `None` to `None`.
_SALE_NUMBER = "SIG-2026-09"


@pytest.fixture
def auction(db: Session, heritage_venue: SalesVenue) -> Auction:
    """A draft auction at an auction house, with no lots yet."""
    row = Auction(
        sales_venue_id=heritage_venue.id,
        title="September Signature Sale",
        external_id=_SALE_NUMBER,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def ebay_auction(db: Session, ebay_venue: SalesVenue) -> Auction:
    """A draft auction on a marketplace, which always names its buyer."""
    row = Auction(
        sales_venue_id=ebay_venue.id,
        title="Weekly eBay auction",
        external_id="EB-2026-09",
    )
    db.add(row)
    db.flush()
    return row


def priced_item(make_item: ItemFactory, title: str, cost: Decimal) -> InventoryItem:
    """An item with an exact cost basis, for an allocation assertion."""
    return make_item(title=title, item_cost=cost, tax_rate=Decimal("0"))


@pytest.fixture
def closed_auction(db: Session, auction: Auction, make_item: ItemFactory) -> Auction:
    """Four single-item lots at an auction house, closed and awaiting results."""
    for number in range(1, 5):
        add_lot(
            db,
            auction,
            priced_item(make_item, f"Lot {number}", Decimal("100.00")),
            lot_number=str(number),
            reserve=None,
            price=Decimal("10.00"),
        )
    schedule(db, auction)
    close(db, auction)
    return auction


@pytest.fixture
def closed_ebay_auction(
    db: Session, ebay_auction: Auction, make_item: ItemFactory
) -> Auction:
    """One lot on a marketplace auction, closed -- a platform that names buyers."""
    add_lot(
        db,
        ebay_auction,
        priced_item(make_item, "eBay lot", Decimal("100.00")),
        lot_number="1",
        reserve=None,
        price=Decimal("10.00"),
    )
    schedule(db, ebay_auction)
    close(db, ebay_auction)
    return ebay_auction


@pytest.fixture
def closed_auction_with_stored_members(
    db: Session,
    auction: Auction,
    make_listing: ListingFactory,
    make_item: ItemFactory,
) -> tuple[Auction, Listing]:
    """A closed auction whose first lot paused a real store listing.

    `quantity_available=1`, a whole item, because `lot_writes.add_member`
    refuses anything with more than one unit on offer -- `make_listing`'s own
    default is 5. Offering the lot pauses that store listing for real, which
    is what gives the resume-or-end assertions something that can fail.
    """
    store_listing = make_listing(quantity_available=1, price=Decimal("189.00"))
    add_lot(
        db,
        auction,
        item_of(store_listing),
        lot_number="1",
        reserve=None,
        price=Decimal("10.00"),
    )
    add_lot(
        db,
        auction,
        priced_item(make_item, "Second lot", Decimal("100.00")),
        lot_number="2",
        reserve=None,
        price=Decimal("10.00"),
    )
    schedule(db, auction)
    close(db, auction)
    return auction, store_listing


@pytest.fixture
def closed_auction_of_one_lot_of_three(
    db: Session, auction: Auction, make_item: ItemFactory
) -> Auction:
    """One closed lot of three equally costed coins: the cent cannot divide."""
    items = [
        priced_item(make_item, f"Member {index}", Decimal("40.00"))
        for index in range(1, 4)
    ]
    add_lot(
        db,
        auction,
        build_lot(db, items, title="Three Morgan Dollars"),
        lot_number="1",
        reserve=None,
        price=Decimal("10.00"),
    )
    schedule(db, auction)
    close(db, auction)
    return auction


@pytest.fixture
def closed_auction_of_two_lots(
    db: Session, auction: Auction, make_item: ItemFactory
) -> Auction:
    """Two lots, four coins, costs that no hammer price is proportional to.

    Lot 1 is three coins costing 500/300/200; lot 2 is one coin costing
    3,000. The settlement below hammers lot 1 at 3,000 and lot 2 at 1,000 --
    deliberately the wrong way round -- so an implementation that divided the
    order's fee by *line amount* first, or per lot at all, produces different
    cents than one that divides it once across all four cost bases.
    """
    trio = [
        priced_item(make_item, f"Trio {index}", cost)
        for index, cost in enumerate(_UNEVEN_COSTS, start=1)
    ]
    add_lot(
        db,
        auction,
        build_lot(db, trio, title="Three Morgan Dollars"),
        lot_number="1",
        reserve=None,
        price=Decimal("10.00"),
    )
    add_lot(
        db,
        auction,
        priced_item(make_item, "Single", Decimal("3000.00")),
        lot_number="2",
        reserve=None,
        price=Decimal("10.00"),
    )
    schedule(db, auction)
    close(db, auction)
    return auction


@pytest.fixture
def consigned_closed_auction(
    db: Session, auction: Auction, make_item: ItemFactory
) -> Auction:
    """Two lots that physically left the premises, and whose sale has closed.

    `consign` then `close`: `close` leaves `consigned_on` set, which is why
    custody is keyed on that date rather than on the status (ruling R13).
    """
    for number in range(1, 3):
        add_lot(
            db,
            auction,
            priced_item(make_item, f"Consigned {number}", Decimal("100.00")),
            lot_number=str(number),
            reserve=None,
            price=Decimal("10.00"),
        )
    schedule(db, auction)
    consign(db, auction, on_date=date(2026, 9, 1))
    close(db, auction)
    return auction


@pytest.fixture
def drawer(db: Session) -> StorageLocation:
    """A plain, non-consigned location for returned coins to come back to."""
    kind_id = db.scalar(
        select(StorageLocationKind.id).where(StorageLocationKind.code == "home")
    )
    assert kind_id is not None
    location = StorageLocation(
        storage_location_kind_id=kind_id, institution="Home", identifier="Drawer 3"
    )
    db.add(location)
    db.flush()
    return location


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def lots_of(db: Session, auction: Auction) -> list[AuctionLot]:
    """This auction's lots, read fresh and in id order.

    Never `auction.lots`: that collection carries no `order_by`, and a
    settlement is exactly the call most likely to have been preceded by
    something that left it stale -- the defect fix round 1 found in
    `add_lot`. Reading it back is also how these tests stay honest about
    what `settle` wrote rather than about what the session remembers.
    """
    return list(
        db.scalars(
            select(AuctionLot)
            .where(AuctionLot.auction_id == auction.id)
            .order_by(AuctionLot.id)
        ).all()
    )


def shares_of(db: Session, order: SalesOrder) -> list[SalesOrderItemShare]:
    """Every share on an order, in line then item order.

    Read with a `select` rather than through `line.shares`, for the reason
    `order_writes._sync_shares` gives: a collection read before the rows
    existed stays cached empty, and a share assertion that silently saw none
    would pass for the wrong reason.
    """
    return list(
        db.scalars(
            select(SalesOrderItemShare)
            .join(
                SalesOrderItem,
                SalesOrderItem.id == SalesOrderItemShare.sales_order_item_id,
            )
            .where(SalesOrderItem.sales_order_id == order.id)
            .order_by(
                SalesOrderItemShare.sales_order_item_id,
                SalesOrderItemShare.inventory_item_id,
            )
        ).all()
    )


def status_code_of(db: Session, order: SalesOrder) -> str:
    """An order's status code. `SalesOrder` has no `status` relationship."""
    row = db.get(SalesOrderStatus, order.sales_order_status_id)
    assert row is not None
    return row.code


def sold_everything(
    db: Session, auction: Auction, *, price: Decimal, buyer: str | None
) -> list[SettlementLine]:
    """A settlement grid saying every lot sold to one buyer at one price."""
    return [
        SettlementLine(
            auction_lot_id=row.id,
            result=AuctionLotResult.sold,
            hammer_price=price,
            buyer_username=buyer,
        )
        for row in lots_of(db, auction)
    ]


def members_of(db: Session, auction_lot: AuctionLot) -> list[InventoryItem]:
    """The coins a lot still offers, asked the way the application asks."""
    return offering_writes.offered_items(db, auction_lot.listing)


# --------------------------------------------------------------------------
# One order per buyer
# --------------------------------------------------------------------------


def test_settling_creates_one_order_per_buyer(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """Two lots to one buyer is one order with two lines, not two orders.

    An auction house bills the buyer once for everything they took, so the
    order is the unit the fee attaches to (spec, *Settle*). Four lots, two
    buyers.
    """
    lots = lots_of(db, closed_auction)
    orders = settle(
        db,
        closed_auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("100.00"), "amy"),
            SettlementLine(lots[1].id, AuctionLotResult.sold, Decimal("200.00"), "amy"),
            SettlementLine(lots[2].id, AuctionLotResult.sold, Decimal("300.00"), "bo"),
            SettlementLine(lots[3].id, AuctionLotResult.sold, Decimal("400.00"), "bo"),
        ],
        fees={
            "amy": [FeeLine("commission", Decimal("30.00"))],
            "bo": [FeeLine("commission", Decimal("70.00"))],
        },
        settled_by=admin_user,
    )

    assert len(orders) == 2
    assert [len(order.items) for order in orders] == [2, 2]
    assert [order.total_amount for order in orders] == [
        Decimal("300.00"),
        Decimal("700.00"),
    ]
    assert {order.customer.venue_username for order in orders} == {"amy", "bo"}


def test_every_buyer_s_order_carries_the_auction_s_sale_number(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """Ruling R18: `external_order_id` is the **sale** number, on every order.

    It is what the owner reconciles an auction house's statement against, and
    the statement names the sale rather than one order per buyer inside it --
    so the same value deliberately appears on both orders here. Nothing
    constrains the column to be unique, so that is the reconciliation key
    rather than a collision.

    Written by a test because it was written by none: every fixture in this
    file left `Auction.external_id` at `None` until now, so `settle` could
    have stopped copying it altogether and every assertion in the suite would
    still have compared `None` to `None`. Nothing would have noticed until a
    statement did not match.
    """
    lots = lots_of(db, closed_auction)
    orders = settle(
        db,
        closed_auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("100.00"), "amy"),
            SettlementLine(lots[1].id, AuctionLotResult.sold, Decimal("200.00"), "bo"),
            SettlementLine(lots[2].id, AuctionLotResult.unsold),
            SettlementLine(lots[3].id, AuctionLotResult.unsold),
        ],
        fees={"amy": [], "bo": []},
        settled_by=admin_user,
    )
    assert len(orders) == 2
    assert [order.external_order_id for order in orders] == [
        _SALE_NUMBER,
        _SALE_NUMBER,
    ]


def test_an_auction_house_order_is_already_delivered(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """The house shipped for us, so the order starts past `paid`.

    The rule is `sales_writes._STATUS_BY_VENUE_KIND`'s, not settlement's --
    this proves settlement does not pass a status of its own and bypass it.
    """
    orders = settle(
        db,
        closed_auction,
        lines=sold_everything(db, closed_auction, price=Decimal("100.00"), buyer=None),
        fees={None: [FeeLine("commission", Decimal("80.00"))]},
        settled_by=admin_user,
    )
    assert len(orders) == 1
    assert status_code_of(db, orders[0]) == "delivered"


def test_an_auction_house_may_leave_the_buyer_undisclosed(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """Heritage does not name its buyers, so one standing customer holds them.

    The spec's *Decisions* table: "one 'undisclosed buyer' per auction house
    that does not name buyers". So a missing username at a house is the
    ordinary case, not the "sold lot lacks a buyer" the *Errors* section
    refuses -- see `test_a_marketplace_lot_must_name_its_buyer`, which is
    where that refusal really lives.
    """
    orders = settle(
        db,
        closed_auction,
        lines=sold_everything(db, closed_auction, price=Decimal("100.00"), buyer=None),
        fees={None: []},
        settled_by=admin_user,
    )
    assert len(orders) == 1
    assert orders[0].customer.venue_username is None
    assert orders[0].customer.display_name == "Undisclosed buyer (Heritage)"


def test_the_auction_and_its_lots_keep_the_result_that_was_entered(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """`auction_lot.result` is the permanent record; the row is not deleted.

    Unlike `remove_lot`, which deletes the row because nothing is left for it
    to describe, settlement is exactly what those columns were added for.
    """
    lots = lots_of(db, closed_auction)
    settle(
        db,
        closed_auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("100.00"), "amy"),
            SettlementLine(lots[1].id, AuctionLotResult.unsold),
            SettlementLine(lots[2].id, AuctionLotResult.withdrawn),
            SettlementLine(lots[3].id, AuctionLotResult.sold, Decimal("250.00"), "amy"),
        ],
        fees={"amy": []},
        settled_by=admin_user,
    )
    db.expire_all()

    assert closed_auction.status is AuctionStatus.settled
    rows = lots_of(db, closed_auction)
    assert [row.result for row in rows] == [
        AuctionLotResult.sold,
        AuctionLotResult.unsold,
        AuctionLotResult.withdrawn,
        AuctionLotResult.sold,
    ]
    assert rows[0].hammer_price == Decimal("100.00")
    assert rows[1].hammer_price is None
    assert rows[0].buyer_customer_id is not None
    assert rows[1].buyer_customer_id is None


# --------------------------------------------------------------------------
# What happens to the coins
# --------------------------------------------------------------------------


def test_an_unsold_lot_resumes_its_members_store_listings_at_the_old_price(
    db: Session,
    closed_auction_with_stored_members: tuple[Auction, Listing],
    admin_user: User,
) -> None:
    """The coin goes back in the shop exactly as it was, not repriced."""
    auction, store_listing = closed_auction_with_stored_members
    before = store_listing.price
    lots = lots_of(db, auction)
    settle(
        db,
        auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.unsold),
            SettlementLine(lots[1].id, AuctionLotResult.unsold),
        ],
        fees={},
        settled_by=admin_user,
    )
    db.expire_all()

    assert store_listing.status is ListingStatus.active
    assert store_listing.price == before
    assert store_listing.paused_by_listing_id is None


def test_a_sold_lot_s_paused_store_listings_end_rather_than_resume(
    db: Session,
    closed_auction_with_stored_members: tuple[Auction, Listing],
    admin_user: User,
) -> None:
    """The coin is gone; resuming would offer something that no longer exists.

    The spec's *Record a sale* row -- "members' paused store listings
    **ended** rather than resumed" -- reached through settlement rather than
    through the Listings page.
    """
    auction, store_listing = closed_auction_with_stored_members
    lots = lots_of(db, auction)
    settle(
        db,
        auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("500.00"), "amy"),
            SettlementLine(lots[1].id, AuctionLotResult.unsold),
        ],
        fees={"amy": []},
        settled_by=admin_user,
    )
    db.expire_all()

    assert store_listing.status is ListingStatus.ended
    assert item_of(store_listing).disposition.code == "sold"


def test_an_unsold_lot_sends_an_unoffered_coin_back_to_held(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """Nothing offers it any more, so it is held -- `end_offer`'s own rule."""
    lots = lots_of(db, closed_auction)
    coins = [members_of(db, row)[0] for row in lots]
    settle(
        db,
        closed_auction,
        lines=[SettlementLine(row.id, AuctionLotResult.unsold) for row in lots],
        fees={},
        settled_by=admin_user,
    )
    db.expire_all()

    assert {coin.disposition.code for coin in coins} == {"held"}
    assert {row.listing.status for row in lots_of(db, closed_auction)} == {
        ListingStatus.ended
    }


def test_the_history_says_a_lot_came_back_unsold_not_withdrawn(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """`ListingStatusHistory` keeps the settlement fact, not a bare "withdrawn".

    An unsold lot at a sale is not an owner's withdrawal; reconciling against
    the house's statement needs to tell the two apart.
    """
    lots = lots_of(db, closed_auction)
    settle(
        db,
        closed_auction,
        lines=[SettlementLine(row.id, AuctionLotResult.unsold) for row in lots],
        fees={},
        settled_by=admin_user,
    )

    notes = db.scalars(
        select(ListingStatusHistory.note).where(
            ListingStatusHistory.listing_id.in_([row.listing_id for row in lots]),
            ListingStatusHistory.to_status == ListingStatus.ended,
        )
    ).all()
    assert notes == [f"unsold at auction #{closed_auction.id}"] * len(lots)


def test_a_withdrawn_lot_returns_its_items_exactly_as_an_unsold_one_does(
    db: Session,
    consigned_closed_auction: Auction,
    drawer: StorageLocation,
    admin_user: User,
) -> None:
    """`withdrawn` is what "was in a closed auction and did not sell" means.

    Which is why the public `remove_lot` refuses a closed auction (ruling
    R14): a lot pulled out after the sale has a settlement result, not no
    record at all. So it comes home the same way an unsold lot does.
    """
    lots = lots_of(db, consigned_closed_auction)
    coins = [members_of(db, row)[0] for row in lots]
    settle(
        db,
        consigned_closed_auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.withdrawn),
            SettlementLine(lots[1].id, AuctionLotResult.unsold),
        ],
        fees={},
        settled_by=admin_user,
        returned_to_location_id=drawer.id,
    )
    db.expire_all()

    assert [coin.storage_location_id for coin in coins] == [drawer.id, drawer.id]


def test_returned_items_move_to_the_chosen_location(
    db: Session,
    consigned_closed_auction: Auction,
    drawer: StorageLocation,
    admin_user: User,
) -> None:
    """For an auction house, the owner picks where unsold coins come back to.

    And the sold lot's coin stays where it is: it left with the buyer, so
    moving it home would be a lie in the location history.
    """
    lots = lots_of(db, consigned_closed_auction)
    sold_coin = members_of(db, lots[0])[0]
    unsold_coin = members_of(db, lots[1])[0]
    consigned_location_id = sold_coin.storage_location_id
    assert consigned_location_id is not None

    settle(
        db,
        consigned_closed_auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("900.00"), None),
            SettlementLine(lots[1].id, AuctionLotResult.unsold),
        ],
        fees={None: [FeeLine("commission", Decimal("180.00"))]},
        settled_by=admin_user,
        returned_to_location_id=drawer.id,
    )
    db.expire_all()

    assert unsold_coin.storage_location_id == drawer.id
    assert sold_coin.storage_location_id == consigned_location_id
    assert consigned_closed_auction.consigned_on is None
    assert consigned_closed_auction.status is AuctionStatus.settled


def test_consignment_is_cleared_even_when_every_lot_sold(
    db: Session, consigned_closed_auction: Auction, admin_user: User
) -> None:
    """The house holds nothing of ours any more, and nothing had to come back.

    So no `returned_to_location_id` is required -- the argument is demanded
    only when there is actually something to return.
    """
    settle(
        db,
        consigned_closed_auction,
        lines=sold_everything(
            db, consigned_closed_auction, price=Decimal("100.00"), buyer=None
        ),
        fees={None: []},
        settled_by=admin_user,
    )
    db.expire_all()
    assert consigned_closed_auction.consigned_on is None


def test_a_consigned_settlement_with_something_to_return_needs_a_location(
    db: Session, consigned_closed_auction: Auction, admin_user: User
) -> None:
    """Something has to say where the coins came back to before they are home.

    `match` names the lot, not just the missing argument. `settle` carries a
    second, identical guard further down -- the same deliberate duplication
    `cancel` has, so a future reordering cannot walk past the requirement --
    and that one has no lot to name. Matching on the argument alone would
    pass whichever of the two fired, which is how this test would stop
    measuring the check it was written for; mutating the grid check away
    leaves it green until the lot number is in the pattern.
    """
    lots = lots_of(db, consigned_closed_auction)
    with pytest.raises(AuctionRefused, match=r"returned_to_location_id .* lot\(s\) 2"):
        settle(
            db,
            consigned_closed_auction,
            lines=[
                SettlementLine(
                    lots[0].id, AuctionLotResult.sold, Decimal("100.00"), None
                ),
                SettlementLine(lots[1].id, AuctionLotResult.unsold),
            ],
            fees={None: []},
            settled_by=admin_user,
        )
    db.expire_all()
    assert consigned_closed_auction.status is AuctionStatus.closed


# --------------------------------------------------------------------------
# The money
# --------------------------------------------------------------------------


def test_shares_of_a_lot_sum_to_its_hammer_price(
    db: Session, closed_auction_of_one_lot_of_three: Auction, admin_user: User
) -> None:
    """Three coins at $100.00, weighted by cost: the cents still add up.

    Equal cost bases, so the division is the one that cannot come out even.
    `allocation.allocate` hands the odd penny to the first part; the point of
    the assertion is that the three still total exactly the hammer price.
    """
    auction = closed_auction_of_one_lot_of_three
    orders = settle(
        db,
        auction,
        lines=sold_everything(db, auction, price=Decimal("100.00"), buyer="amy"),
        fees={"amy": []},
        settled_by=admin_user,
    )
    shares = shares_of(db, orders[0])

    assert [share.amount for share in shares] == [
        Decimal("33.34"),
        Decimal("33.33"),
        Decimal("33.33"),
    ]
    assert sum(share.amount for share in shares) == Decimal("100.00")


def test_fees_are_divided_the_same_way_as_the_price(
    db: Session, closed_auction_of_two_lots: Auction, admin_user: User
) -> None:
    """One fee, one order, four coins across two lots -- divided by cost basis.

    This is the genuinely new arithmetic on this branch. A hammer price is a
    *line's* money and divides among that lot's coins; a fee is the
    *order's* money, because the house bills per buyer, and divides across
    every coin on every line. The two allocations therefore have different
    denominators, which is exactly where a cent can go missing.

    The fixture hammers the cheap lot high and the dear lot low on purpose,
    so an implementation that divided the fee per lot -- by line amount or
    evenly -- lands on different cents and the assertion below fails. Both
    weightings are `sales_writes._weights` over `total_cost`; only the set
    they run over differs.
    """
    auction = closed_auction_of_two_lots
    lots = lots_of(db, auction)
    orders = settle(
        db,
        auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("3000.00"), "am"),
            SettlementLine(lots[1].id, AuctionLotResult.sold, Decimal("1000.00"), "am"),
        ],
        fees={"am": [FeeLine("commission", Decimal("1.00"))]},
        settled_by=admin_user,
    )

    assert len(orders) == 1
    shares = shares_of(db, orders[0])
    # The price: each lot's own money over its own coins. 3,000 over
    # 500/300/200 and 1,000 over the single 3,000-cost coin.
    assert [share.amount for share in shares] == [
        Decimal("1500.00"),
        Decimal("900.00"),
        Decimal("600.00"),
        Decimal("1000.00"),
    ]
    # The fee: one dollar over all four cost bases at once -- 500, 300, 200,
    # 3,000 -- with the odd penny going to the largest remainder.
    assert [share.fee_amount for share in shares] == [
        Decimal("0.13"),
        Decimal("0.07"),
        Decimal("0.05"),
        Decimal("0.75"),
    ]
    assert sum(share.fee_amount for share in shares) == Decimal("1.00")
    assert orders[0].total_amount == Decimal("4000.00")


def test_each_buyer_is_billed_only_their_own_fees(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """A fee keyed by buyer lands on that buyer's order and nowhere else."""
    lots = lots_of(db, closed_auction)
    orders = settle(
        db,
        closed_auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("100.00"), "amy"),
            SettlementLine(lots[1].id, AuctionLotResult.sold, Decimal("100.00"), "amy"),
            SettlementLine(lots[2].id, AuctionLotResult.sold, Decimal("100.00"), "bo"),
            SettlementLine(lots[3].id, AuctionLotResult.sold, Decimal("100.00"), "bo"),
        ],
        fees={"amy": [FeeLine("commission", Decimal("40.00"))], "bo": []},
        settled_by=admin_user,
    )
    totals = {
        order.customer.venue_username: sum(
            share.fee_amount for share in shares_of(db, order)
        )
        for order in orders
    }
    assert totals == {"amy": Decimal("40.00"), "bo": Decimal("0.00")}


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_settlement_is_refused_while_any_lot_lacks_a_result(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """Every lot has to say what it did before any of them can be settled."""
    lots = lots_of(db, closed_auction)
    incomplete = [SettlementLine(row.id, AuctionLotResult.unsold) for row in lots[:3]]
    with pytest.raises(AuctionRefused, match="result"):
        settle(db, closed_auction, lines=incomplete, fees={}, settled_by=admin_user)


def test_settlement_is_refused_when_a_sold_lot_has_no_price(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """A sale with no money is not a sale; it is a half-filled grid.

    `match` requires the grid refusal's own prefix, not just the words
    "hammer price". `_sold_by_buyer` carries a second, identical guard whose
    message is the same sentence *without* `cannot be settled:` -- the same
    deliberate duplication `cancel` has -- so matching on the words alone
    passes off whichever guard fired, and mutating the grid check away leaves
    this green. Tightened the same way the return-location test was.
    """
    lots = lots_of(db, closed_auction)
    lines = [SettlementLine(row.id, AuctionLotResult.unsold) for row in lots]
    lines[1] = SettlementLine(lots[1].id, AuctionLotResult.sold, None, "amy")
    with pytest.raises(
        AuctionRefused, match=r"cannot be settled: .*lot 2 sold but has no hammer price"
    ):
        settle(db, closed_auction, lines=lines, fees={"amy": []}, settled_by=admin_user)


def test_one_lot_given_two_results_is_refused(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """Two rows for one lot is a grid nobody can act on.

    Whichever row won would be arbitrary, and the loser would be a result the
    owner entered and the system silently discarded. Advertised in `settle`'s
    docstring and, until now, written by no test.
    """
    lots = lots_of(db, closed_auction)
    lines = [SettlementLine(row.id, AuctionLotResult.unsold) for row in lots]
    lines.append(
        SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("100.00"), "amy")
    )
    with pytest.raises(AuctionRefused, match="lot 1 was given two results"):
        settle(db, closed_auction, lines=lines, fees={}, settled_by=admin_user)


def test_a_lot_that_did_not_sell_cannot_carry_a_hammer_price(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """Money against an unsold lot is a mis-filled row, not a fact.

    Silently ignoring it would be worse than refusing: the owner typed a
    number, and `auction_lot.hammer_price` would then read `NULL` beside a
    figure they believe they entered. Advertised in `settle`'s docstring and,
    until now, written by no test -- and the message names the result so the
    grid can point at the right cell.
    """
    lots = lots_of(db, closed_auction)
    lines = [SettlementLine(row.id, AuctionLotResult.unsold) for row in lots]
    lines[2] = SettlementLine(lots[2].id, AuctionLotResult.withdrawn, Decimal("250.00"))
    with pytest.raises(
        AuctionRefused, match="lot 3 is withdrawn, so it cannot have a hammer price"
    ):
        settle(db, closed_auction, lines=lines, fees={}, settled_by=admin_user)


def test_a_marketplace_lot_must_name_its_buyer(
    db: Session, closed_ebay_auction: Auction, admin_user: User
) -> None:
    """Only an auction house may leave a buyer undisclosed.

    The spec's *Errors* line -- settle refuses when a sold lot "lacks a
    price or buyer" -- read against its *Decisions* table, which gives the
    undisclosed buyer to auction houses alone. eBay always names who bought
    it, so a blank there is a half-filled grid rather than a real fact.
    """
    lots = lots_of(db, closed_ebay_auction)
    with pytest.raises(AuctionRefused, match="buyer"):
        settle(
            db,
            closed_ebay_auction,
            lines=[SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("50.00"))],
            fees={},
            settled_by=admin_user,
        )


def test_a_negative_fee_refuses_the_whole_settlement(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """A refund is not a negative fee, and one bad number stops everything.

    `SettlementInputInvalid` by name (ruling R15) -- the wider
    `AuctionRefused` would keep passing if the pair were collapsed, since
    this is a subclass of it.
    """
    before = db.scalar(select(func.count()).select_from(SalesOrder))
    with pytest.raises(SettlementInputInvalid, match="negative"):
        settle(
            db,
            closed_auction,
            lines=sold_everything(
                db, closed_auction, price=Decimal("100.00"), buyer="amy"
            ),
            fees={"amy": [FeeLine("commission", Decimal("-1.00"))]},
            settled_by=admin_user,
        )
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(SalesOrder)) == before
    assert closed_auction.status is AuctionStatus.closed
    assert [row.result for row in lots_of(db, closed_auction)] == [None] * 4


def test_a_sub_cent_hammer_price_is_refused(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """`Numeric(12, 2)` and `allocate` round a half-cent differently.

    So a row and the shares split from it would disagree by a cent. The
    predicate is `sales_writes.money_problem`'s, asked here rather than
    written out a second time.

    `SettlementInputInvalid`, not the wider `AuctionRefused`: sub-cent binds
    hammer prices and not only fees, and it is bad input either way (ruling
    R15). Naming the narrower class is what makes this fail if the pair is
    ever collapsed -- `pytest.raises(AuctionRefused)` would keep passing,
    since the narrower one is a subclass.
    """
    with pytest.raises(SettlementInputInvalid, match="cent"):
        settle(
            db,
            closed_auction,
            lines=sold_everything(
                db, closed_auction, price=Decimal("100.005"), buyer="amy"
            ),
            fees={"amy": []},
            settled_by=admin_user,
        )


def test_a_refusal_lists_every_problem_lot_not_just_the_first(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """The console shows a grid; fixing one problem at a time is miserable.

    Three different faults across four lots, and the message names all of
    them by lot number.
    """
    lots = lots_of(db, closed_auction)
    with pytest.raises(AuctionRefused) as caught:
        settle(
            db,
            closed_auction,
            lines=[
                SettlementLine(lots[0].id, AuctionLotResult.sold, None, "amy"),
                SettlementLine(
                    lots[1].id, AuctionLotResult.sold, Decimal("-5.00"), "amy"
                ),
                SettlementLine(
                    lots[2].id, AuctionLotResult.sold, Decimal("1.005"), "amy"
                ),
            ],
            fees={"amy": []},
            settled_by=admin_user,
        )
    message = str(caught.value)
    assert "lot 1" in message
    assert "lot 2" in message
    assert "lot 3" in message
    assert "lot 4" in message


def test_settling_an_auction_that_is_not_closed_is_refused(
    db: Session, auction: Auction, make_item: ItemFactory, admin_user: User
) -> None:
    """Results belong to a sale that has actually happened."""
    add_lot(
        db,
        auction,
        priced_item(make_item, "Draft lot", Decimal("100.00")),
        lot_number="1",
        reserve=None,
        price=Decimal("10.00"),
    )
    with pytest.raises(AuctionRefused, match="cannot be settled"):
        settle(
            db,
            auction,
            lines=sold_everything(db, auction, price=Decimal("10.00"), buyer=None),
            fees={},
            settled_by=admin_user,
        )


def test_a_line_naming_a_lot_from_another_auction_is_refused(
    db: Session, closed_auction: Auction, ebay_auction: Auction, admin_user: User
) -> None:
    """The grid belongs to one sale; a stray id is a mis-posted form."""
    lines = [
        SettlementLine(row.id, AuctionLotResult.unsold)
        for row in lots_of(db, closed_auction)
    ]
    lines.append(SettlementLine(9_999_999, AuctionLotResult.unsold))
    with pytest.raises(AuctionRefused, match="9999999"):
        settle(db, closed_auction, lines=lines, fees={}, settled_by=admin_user)


def test_fees_for_a_buyer_who_bought_nothing_are_refused(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """A fee with no order to sit on would be money silently dropped.

    The buyer who *did* buy is named in two cases here, `CoinFan88` on the
    lots and `coinfan88` in the fees, so this also proves ruling R17's
    folding did not widen the comparison into matching everything: `ghost`
    casefolds to itself and still bought nothing. One test rather than two
    near-identical ones, which is what the first attempt at this left behind.
    """
    with pytest.raises(AuctionRefused, match="bought nothing"):
        settle(
            db,
            closed_auction,
            lines=sold_everything(
                db, closed_auction, price=Decimal("100.00"), buyer="CoinFan88"
            ),
            fees={
                "coinfan88": [],
                "ghost": [FeeLine("commission", Decimal("10.00"))],
            },
            settled_by=admin_user,
        )


# --------------------------------------------------------------------------
# All or nothing
# --------------------------------------------------------------------------


def test_a_failure_part_way_through_leaves_nothing_written(
    db: Session,
    closed_auction: Auction,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guarantee that matters most: no half-settled auction.

    `record_sale_lines` is forced to raise on the **second** buyer, after the
    first buyer's order, fee, shares and ended listings are already in the
    database. Everything is then read back with `db.expire_all()`, so these
    assertions come from Postgres rather than from the session's memory of
    what it did.

    **Mutation-verified.** Replacing `settle`'s `with db.begin_nested():`
    boundary with a `db.commit()` after each buyer -- the brief's Step 4 --
    reddens this test:

        assert 1 == 0
        E   assert 1 == 0
        (backend/tests/test_auction_settlement.py, the SalesOrder count)

    The auction's own status and the first lot's listing survive the
    rollback unchanged too, which is what makes this a test of the boundary
    rather than of the order table alone.
    """
    orders_before = db.scalar(select(func.count()).select_from(SalesOrder))
    lots = lots_of(db, closed_auction)
    first_listing_id = lots[0].listing_id
    real = sales_writes.record_sale_lines
    calls = {"n": 0}

    def flaky(
        db_: Session,
        sale_lines: Sequence[sales_writes.SaleLine],
        *,
        buyer_username: str | None,
        external_order_id: str | None,
        fees: Sequence[FeeLine],
        recorded_by: User,
        equal_shares: bool = False,
        status_code: str | None = None,
    ) -> SalesOrder:
        """The real thing, except that the second buyer's order blows up.

        Spelled out rather than `*args: Any, **kwargs: Any`, which ruff's
        `ANN401` refuses: a shim that accepts anything would also keep
        accepting it after `record_sale_lines`' own signature changed, and
        this test would then be passing arguments the real function no
        longer takes.
        """
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("the second buyer's order failed")
        return real(
            db_,
            sale_lines,
            buyer_username=buyer_username,
            external_order_id=external_order_id,
            fees=fees,
            recorded_by=recorded_by,
            equal_shares=equal_shares,
            status_code=status_code,
        )

    monkeypatch.setattr(sales_writes, "record_sale_lines", flaky)

    with pytest.raises(RuntimeError, match="second buyer"):
        settle(
            db,
            closed_auction,
            lines=[
                SettlementLine(
                    lots[0].id, AuctionLotResult.sold, Decimal("100.00"), "amy"
                ),
                SettlementLine(
                    lots[1].id, AuctionLotResult.sold, Decimal("200.00"), "amy"
                ),
                SettlementLine(
                    lots[2].id, AuctionLotResult.sold, Decimal("300.00"), "bo"
                ),
                SettlementLine(
                    lots[3].id, AuctionLotResult.sold, Decimal("400.00"), "bo"
                ),
            ],
            fees={"amy": [FeeLine("commission", Decimal("30.00"))], "bo": []},
            settled_by=admin_user,
        )

    assert calls["n"] == 2
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(SalesOrder)) == orders_before
    assert closed_auction.status is AuctionStatus.closed
    assert [row.result for row in lots_of(db, closed_auction)] == [None] * 4
    first_listing = db.get(Listing, first_listing_id)
    assert first_listing is not None
    assert first_listing.status is ListingStatus.active


# --------------------------------------------------------------------------
# The obligation `lock_for_sale` leaves to its callers
# --------------------------------------------------------------------------


def test_a_lot_listing_can_never_be_paused_by_another_offer(
    db: Session,
    store_lot_listing: Listing,
    lot_of_three: SalesLot,
    ebay_venue: SalesVenue,
) -> None:
    """Why settlement needs no `offering_writes.refuse_if_lot_unheld`.

    `end_offer(sold=True)` -- which settlement calls and Task 2 never did --
    **ends** the listings this one paused instead of resuming them, and those
    are listings settlement never named. The obligation at
    `lock_for_sale`'s door covers exactly one case: one of them being a
    **lot** listing, whose lot row `_end` would then rewrite without this
    pass necessarily holding it.

    That case cannot arise, and this is the measurement rather than a reading
    of the code. `paused_by_listing_id` is written in exactly one place
    (`offering_writes.offer`), over the own-store listings that already hold
    a member -- and each member passes `_refuse_unofferable` first, in the
    same loop iteration, which refuses any coin that is an open member of an
    *offered* lot. A lot listing holds its coins only through the claims
    `offer` wrote, and `_end` releases those claims and the memberships in
    the same breath, so "a lot listing holds this coin" and "this coin is in
    an offered lot" are the same fact. The refusal below is therefore the
    only outcome, and no lot listing can ever be paused by anything.
    """
    member = lot_of_three.members[0].item
    with pytest.raises(offering_writes.OfferRefused, match="which is offered"):
        offering_writes.offer(
            db,
            item=member,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("50.00"),
            title="One Morgan Dollar",
            description="",
            external_id=None,
        )

    assert store_lot_listing.status is ListingStatus.active
    assert store_lot_listing.paused_by_listing_id is None


def test_settlement_ends_only_item_listings_it_did_not_name(
    db: Session,
    closed_auction_with_stored_members: tuple[Auction, Listing],
    admin_user: User,
) -> None:
    """The same claim, asserted on the rows a real settlement acts on.

    Every listing pointing at another through `paused_by_listing_id` is an
    item listing (`sales_lot_id IS NULL`). If that ever stops being true,
    `end_offer(sold=True)`'s second `_end` would dissolve a lot whose row
    this transaction may not hold, and this test is what says so first.

    The pause is inspected **before** `settle`, and the set is asserted to
    be exactly the one listing rather than merely lot-free: `_end` clears
    `paused_by_listing_id` as it ends a listing, so the same query run
    afterwards comes back empty and an `all()` over it is vacuously true --
    which is how this test would pass without proving anything at all.
    """
    auction, store_listing = closed_auction_with_stored_members
    paused = list(
        db.scalars(
            select(Listing).where(Listing.paused_by_listing_id.is_not(None))
        ).all()
    )
    assert [row.id for row in paused] == [store_listing.id]
    assert all(row.sales_lot_id is None for row in paused)

    lots = lots_of(db, auction)
    settle(
        db,
        auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("500.00"), "amy"),
            SettlementLine(lots[1].id, AuctionLotResult.unsold),
        ],
        fees={"amy": []},
        settled_by=admin_user,
    )
    db.expire_all()

    assert store_listing.status is ListingStatus.ended
    assert store_listing.paused_by_listing_id is None


# --------------------------------------------------------------------------
# Which refusal (ruling R15), and who the buyer is (ruling R17)
# --------------------------------------------------------------------------


def test_bad_money_and_a_conflict_are_different_refusals(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """A number that is not money is 422; a conflict with the auction is 409.

    The two are a subclass pair, mirroring `sales_writes.SaleInputInvalid`
    under `SaleRefused`, so **`pytest.raises(AuctionRefused)` catches both**
    and asserting the type alone would prove nothing. Each case therefore
    asserts `isinstance` in the direction that can fail: the money one *is*
    a `SettlementInputInvalid`, the conflict one is *not*.

    This is also the only thing standing between a reversed pair of `except`
    clauses in Task 5's router and every 422 silently becoming a 409 -- mypy
    cannot see that ordering, because the narrower type is still assignable
    to the wider one.
    """
    lots = lots_of(db, closed_auction)
    sold = sold_everything(db, closed_auction, price=Decimal("100.00"), buyer="amy")

    with pytest.raises(AuctionRefused) as bad_money:
        settle(
            db,
            closed_auction,
            lines=sold,
            fees={"amy": [FeeLine("commission", Decimal("-1.00"))]},
            settled_by=admin_user,
        )
    assert isinstance(bad_money.value, SettlementInputInvalid)

    with pytest.raises(AuctionRefused) as conflict:
        settle(
            db,
            closed_auction,
            lines=[SettlementLine(lots[0].id, AuctionLotResult.unsold)],
            fees={},
            settled_by=admin_user,
        )
    assert not isinstance(conflict.value, SettlementInputInvalid)


def test_a_grid_with_both_kinds_of_problem_refuses_as_the_wider_one(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """409 is the safer answer about a mixture: something really is in the way.

    Lot 1 carries a negative price (bad input) and lot 2 no result at all (a
    conflict). Saying "the form was wrong" about that would send the owner
    looking only at the numbers.
    """
    lots = lots_of(db, closed_auction)
    with pytest.raises(AuctionRefused) as caught:
        settle(
            db,
            closed_auction,
            lines=[
                SettlementLine(
                    lots[0].id, AuctionLotResult.sold, Decimal("-1.00"), "amy"
                )
            ],
            fees={"amy": []},
            settled_by=admin_user,
        )
    assert not isinstance(caught.value, SettlementInputInvalid)
    assert "negative" in str(caught.value)
    assert "lot 2 has no result" in str(caught.value)


def test_two_spellings_of_one_buyer_make_one_order(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """`CoinFan88` and `coinfan88` are one person, so they are one order.

    Grouping case-sensitively while `buyers.venue_buyer` matches
    case-insensitively wrote **two orders against a single customer** --
    which reconciles against the house's statement one order short, with
    nothing in the schema saying the two belong together (ruling R17).

    The fee is keyed with a third spelling, so this also proves the `fees`
    mapping is folded the same way rather than only the lots.
    """
    lots = lots_of(db, closed_auction)
    orders = settle(
        db,
        closed_auction,
        lines=[
            SettlementLine(
                lots[0].id, AuctionLotResult.sold, Decimal("100.00"), "CoinFan88"
            ),
            SettlementLine(
                lots[1].id, AuctionLotResult.sold, Decimal("200.00"), "coinfan88"
            ),
            SettlementLine(
                lots[2].id, AuctionLotResult.sold, Decimal("300.00"), " COINFAN88 "
            ),
            SettlementLine(lots[3].id, AuctionLotResult.unsold),
        ],
        fees={"COINFAN88": [FeeLine("commission", Decimal("60.00"))]},
        settled_by=admin_user,
    )

    assert len(orders) == 1
    assert len(orders[0].items) == 3
    assert orders[0].total_amount == Decimal("600.00")
    assert sum(share.fee_amount for share in shares_of(db, orders[0])) == Decimal(
        "60.00"
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(Customer)
            .where(Customer.sales_venue_id == closed_auction.sales_venue_id)
        )
        == 1
    )
    db.expire_all()
    assert {row.buyer_customer_id for row in lots_of(db, closed_auction)} == {
        orders[0].customer_id,
        None,
    }


def test_the_customer_keeps_the_spelling_the_owner_typed(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """Grouping folds case; the customer record must not.

    The first spelling the grid used is what is stored -- never
    `coinfan88`, which is a key this module invented and not a name anyone
    entered.
    """
    lots = lots_of(db, closed_auction)
    orders = settle(
        db,
        closed_auction,
        lines=[
            SettlementLine(
                lots[0].id, AuctionLotResult.sold, Decimal("100.00"), "CoinFan88"
            ),
            SettlementLine(
                lots[1].id, AuctionLotResult.sold, Decimal("100.00"), "coinfan88"
            ),
            SettlementLine(lots[2].id, AuctionLotResult.unsold),
            SettlementLine(lots[3].id, AuctionLotResult.unsold),
        ],
        fees={"CoinFan88": []},
        settled_by=admin_user,
    )
    # Both assertions, because either alone passes for the wrong reason: a
    # grouping that did not fold would still store `CoinFan88` on its first
    # order, and a group that stored the fold would still be one order.
    assert len(orders) == 1
    assert orders[0].customer.venue_username == "CoinFan88"


def test_one_buyer_given_two_fee_keys_that_fold_together_is_refused(
    db: Session, closed_auction: Auction, admin_user: User
) -> None:
    """Two fee rows for one buyer would silently drop one of them.

    `fee_lines` is a dict keyed on the folded name, so without this refusal
    the second spelling would overwrite the first and a real fee would vanish
    from the order.
    """
    with pytest.raises(AuctionRefused, match="given twice"):
        settle(
            db,
            closed_auction,
            lines=sold_everything(
                db, closed_auction, price=Decimal("100.00"), buyer="CoinFan88"
            ),
            fees={
                "CoinFan88": [FeeLine("commission", Decimal("10.00"))],
                "coinfan88": [FeeLine("processing", Decimal("5.00"))],
            },
            settled_by=admin_user,
        )
