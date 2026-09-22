"""Auction transitions: adding and removing lots, and the auction's own life.

`app.auctions` is the sole writer of `auction` and `auction_lot`. Adding or
removing a lot **is** an offer or an ending, so most of what these tests
prove is that `app.auctions` hands off to `offering_writes` correctly rather
than reimplementing its rules -- the same reason `test_lot_writes.py` builds
its fixtures through `offering_writes.offer` rather than a bare `Listing(...)`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import pytest
from app import lot_writes
from app.auctions import (
    AuctionRefused,
    add_lot,
    cancel,
    close,
    consign,
    remove_lot,
    schedule,
)
from app.models import (
    Auction,
    AuctionLot,
    AuctionStatus,
    InventoryItem,
    Listing,
    ListingFormat,
    ListingStatus,
    LocationHistory,
    SalesLot,
    SalesLotStatus,
    SalesVenue,
    StorageLocation,
    StorageLocationKind,
)
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tests.conftest import build_lot, item_of

ItemFactory = Callable[..., InventoryItem]


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def auction(db: Session, heritage_venue: SalesVenue) -> Auction:
    """A draft auction at an auction house, with no lots yet."""
    row = Auction(sales_venue_id=heritage_venue.id, title="September Signature Sale")
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def assembled_lot(db: Session, make_item: ItemFactory) -> SalesLot:
    """An assembling lot of two items, ready to be offered in an auction."""
    items = [make_item(title=f"Lot member {i}") for i in range(2)]
    return build_lot(db, items, title="Two Morgan Dollars")


@pytest.fixture
def auction_lot(
    db: Session,
    auction: Auction,
    make_listing: Callable[..., Listing],
    make_item: ItemFactory,
) -> AuctionLot:
    """A two-member auction lot covering both halves of "As End".

    One member (`stored_item`) already has an active store listing --
    `quantity_available=1`, a whole item, since `lot_writes.add_member`
    refuses anything with more than one unit on offer (the `listing`
    fixture's own default is 5, which `_refuse_partial` would reject
    outright). Offering the lot pauses that store listing, so `remove_lot`
    has a real paused listing to resume -- the same shape `stored_then_ebay`
    (`tests/conftest.py`) sets up for a plain offer. The other member
    (`plain_item`) has never been offered anywhere, so nothing but the lot
    itself holds it, and `remove_lot` has a real item to send back to `held`.
    """
    store_listing = make_listing(quantity_available=1)
    stored_item = item_of(store_listing)
    plain_item = make_item(title="Plain lot member")
    lot = lot_writes.create_lot(db, title="Two lot members", description="")
    lot_writes.add_member(db, lot, stored_item)
    lot_writes.add_member(db, lot, plain_item)
    return add_lot(
        db, auction, lot, lot_number="1", reserve=None, price=Decimal("10.00")
    )


@pytest.fixture
def auction_with_three_lots(
    db: Session, auction: Auction, make_item: ItemFactory
) -> Auction:
    """A draft auction with three single-item lots, ready to be cancelled."""
    for number in range(1, 4):
        add_lot(
            db,
            auction,
            make_item(title=f"Lot {number}"),
            lot_number=str(number),
            reserve=None,
            price=Decimal("10.00"),
        )
    return auction


@pytest.fixture
def house_auction(db: Session, auction: Auction, make_item: ItemFactory) -> Auction:
    """A scheduled auction-house auction with one lot, ready to consign."""
    add_lot(
        db,
        auction,
        make_item(title="Consignment item"),
        lot_number="1",
        reserve=None,
        price=Decimal("100.00"),
    )
    schedule(db, auction)
    return auction


@pytest.fixture
def ebay_auction(db: Session, ebay_venue: SalesVenue) -> Auction:
    """A scheduled auction on a marketplace platform -- eBay never takes custody."""
    row = Auction(sales_venue_id=ebay_venue.id, title="Weekly eBay auction")
    db.add(row)
    db.flush()
    schedule(db, row)
    return row


@pytest.fixture
def closed_auction(db: Session, auction: Auction) -> Auction:
    """An auction already closed, so no more lots may be added."""
    close(db, auction)
    return auction


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def items_of(auction_row: Auction) -> list[InventoryItem]:
    """Every item held by this auction's lots, singly or in a group.

    Takes no `Session`: every relationship it walks (`Auction.lots`,
    `Listing.sales_lot`, `SalesLot.members`) is reachable through the object
    graph on the session `auction_row` is already attached to, the same way
    `lot_writes.members_held` reads a loaded collection rather than asking
    for one.
    """
    items: list[InventoryItem] = []
    for row in auction_row.lots:
        listing = row.listing
        if listing.sales_lot_id is None:
            assert listing.inventory_item is not None
            items.append(listing.inventory_item)
        else:
            assert listing.sales_lot is not None
            items.extend(
                member.item
                for member in listing.sales_lot.members
                if member.released_at is None
            )
    return items


@dataclass(frozen=True)
class _LocationMove:
    """One recorded move, with its `StorageLocation` resolved for a test's convenience.

    `LocationHistory` itself carries only `storage_location_id`, no
    relationship -- nothing else in the codebase needed one -- so this
    wraps the row and its resolved location rather than adding a
    relationship to the model for one test file.
    """

    moved_at: datetime
    location: StorageLocation | None


def location_history(db: Session, item: InventoryItem) -> list[_LocationMove]:
    """The item's location moves in order, oldest first, each location resolved."""
    rows = db.scalars(
        select(LocationHistory)
        .where(LocationHistory.inventory_item_id == item.id)
        .order_by(LocationHistory.id)
    ).all()
    return [
        _LocationMove(
            moved_at=row.moved_at,
            location=(
                db.get(StorageLocation, row.storage_location_id)
                if row.storage_location_id is not None
                else None
            ),
        )
        for row in rows
    ]


# --------------------------------------------------------------------------
# add_lot
# --------------------------------------------------------------------------


def test_adding_a_lot_offers_it_with_auction_format(
    db: Session, auction: Auction, assembled_lot: SalesLot
) -> None:
    """Same refusals and pausing as any offer -- it *is* an offer."""
    auction_lot = add_lot(
        db,
        auction,
        assembled_lot,
        lot_number="14",
        reserve=None,
        price=Decimal("50.00"),
    )
    assert auction_lot.listing.format is ListingFormat.auction
    assert auction_lot.listing.status is ListingStatus.active


def test_a_single_item_becomes_a_lot_of_one(
    db: Session, auction: Auction, received_item: InventoryItem
) -> None:
    """The spec: a single item offered in an auction is a sales lot of one."""
    auction_lot = add_lot(
        db, auction, received_item, lot_number="1", reserve=None, price=Decimal("25.00")
    )
    listing = auction_lot.listing
    assert listing.sales_lot_id is not None
    lot = listing.sales_lot
    assert lot is not None
    assert [member.inventory_item_id for member in lot.members] == [received_item.id]
    # The wrapping lot's own title/description default the listing's, since
    # add_lot's caller passed neither.
    assert listing.title == received_item.source_title
    assert listing.description == received_item.description


def test_lots_cannot_be_added_after_closing(
    db: Session, closed_auction: Auction, assembled_lot: SalesLot
) -> None:
    """The sale has happened."""
    with pytest.raises(AuctionRefused, match="closed"):
        add_lot(
            db,
            closed_auction,
            assembled_lot,
            lot_number="1",
            reserve=None,
            price=Decimal("10.00"),
        )


def test_a_zero_starting_bid_is_the_default(
    db: Session, auction: Auction, received_item: InventoryItem
) -> None:
    """`price` is the starting bid, 0 when the caller gives none."""
    auction_lot = add_lot(
        db, auction, received_item, lot_number="1", reserve=None, price=None
    )
    assert auction_lot.listing.price == Decimal("0")


# --------------------------------------------------------------------------
# remove_lot
# --------------------------------------------------------------------------


def test_removing_a_lot_ends_its_listing_and_dissolves_it(
    db: Session, auction_lot: AuctionLot
) -> None:
    """As End: paused store listings resume, members go back to held.

    Both halves in one test, on the two-member `auction_lot` fixture:
    `stored_item`'s own store listing resumes and keeps claiming it (so it
    stays `listed`, correctly -- it is still for sale, just back in the
    shop), while `plain_item`, which nothing else ever offered, goes back to
    `held`.
    """
    listing = auction_lot.listing
    lot = listing.sales_lot
    assert lot is not None
    store_listing = db.scalars(
        select(Listing).where(Listing.paused_by_listing_id == listing.id)
    ).one()
    stored_item = store_listing.inventory_item
    assert stored_item is not None
    (plain_item,) = [
        member.item
        for member in lot.members
        if member.inventory_item_id != stored_item.id
    ]

    remove_lot(db, auction_lot)

    db.refresh(listing)
    db.refresh(lot)
    db.refresh(store_listing)
    db.refresh(stored_item)
    db.refresh(plain_item)
    assert listing.status is ListingStatus.ended
    assert lot.status is SalesLotStatus.dissolved
    assert store_listing.status is ListingStatus.active
    assert stored_item.disposition.code == "listed"
    assert plain_item.disposition.code == "held"
    assert (
        db.scalars(
            select(AuctionLot).where(AuctionLot.id == auction_lot.id)
        ).one_or_none()
        is None
    )


def test_a_removed_lot_number_can_be_reused(
    db: Session, auction: Auction, received_item: InventoryItem, make_item: ItemFactory
) -> None:
    """Deleting the `auction_lot` row frees its lot number.

    `uq_auction_lot_auction_lot_number` would otherwise hold "1" for ever
    against a lot that was pulled before the sale.
    """
    first = add_lot(
        db, auction, received_item, lot_number="1", reserve=None, price=Decimal("10.00")
    )
    remove_lot(db, first)

    second = add_lot(
        db, auction, make_item(), lot_number="1", reserve=None, price=Decimal("10.00")
    )
    assert second.lot_number == "1"


# --------------------------------------------------------------------------
# schedule / close / cancel
# --------------------------------------------------------------------------


def test_scheduling_moves_a_draft_auction_forward(
    db: Session, auction: Auction
) -> None:
    """The one legal move out of `draft`."""
    schedule(db, auction)
    assert auction.status is AuctionStatus.scheduled


def test_scheduling_a_scheduled_auction_is_refused(
    db: Session, auction: Auction
) -> None:
    """Refused with a message naming the status in the way."""
    schedule(db, auction)
    with pytest.raises(AuctionRefused, match="scheduled"):
        schedule(db, auction)


def test_cancelling_removes_every_lot(
    db: Session, auction_with_three_lots: Auction
) -> None:
    """Nothing is left claimed by a sale that is not happening."""
    listings = [row.listing for row in auction_with_three_lots.lots]
    assert len(listings) == 3

    cancel(db, auction_with_three_lots)

    assert auction_with_three_lots.status is AuctionStatus.cancelled
    remaining = db.scalars(
        select(AuctionLot).where(AuctionLot.auction_id == auction_with_three_lots.id)
    ).all()
    assert remaining == []
    for row in listings:
        db.refresh(row)
        assert row.status is ListingStatus.ended


def test_cancelling_a_settled_auction_is_refused(db: Session, auction: Auction) -> None:
    """A settled sale is a fact, not a draft to discard."""
    auction.status = AuctionStatus.settled
    db.flush()
    with pytest.raises(AuctionRefused, match="settled"):
        cancel(db, auction)


# --------------------------------------------------------------------------
# consign
# --------------------------------------------------------------------------


def test_consigning_moves_every_item_to_the_house(
    db: Session, house_auction: Auction
) -> None:
    """Through `lifecycle_writes.set_location`, so history stays complete."""
    consign(db, house_auction, on_date=date(2026, 10, 1))
    for item in items_of(house_auction):
        location = db.get(StorageLocation, item.storage_location_id)
        assert location is not None
        assert location.kind.code == "consigned"
        last_move = location_history(db, item)[-1]
        assert last_move.location is not None
        assert last_move.location.institution == "Heritage"
    assert house_auction.status is AuctionStatus.consigned
    assert house_auction.consigned_on == date(2026, 10, 1)


def test_consigning_a_marketplace_auction_is_refused(
    db: Session, ebay_auction: Auction
) -> None:
    """Nothing leaves the premises for an eBay auction."""
    with pytest.raises(AuctionRefused, match="auction house"):
        consign(db, ebay_auction, on_date=date(2026, 10, 1))


def test_consigning_an_unscheduled_auction_is_refused(
    db: Session, house_auction: Auction
) -> None:
    """A draft auction has not been sent to the house yet."""
    house_auction.status = AuctionStatus.draft
    db.flush()
    with pytest.raises(AuctionRefused, match="draft"):
        consign(db, house_auction, on_date=date(2026, 10, 1))


def test_consign_fails_loudly_if_the_consigned_kind_is_missing(
    db: Session, house_auction: Auction
) -> None:
    """A migrated-but-unseeded database says so, rather than guessing or crashing.

    Ruling from the Task 2 brief: naming the missing kind and the seeder
    command, never creating the kind on the fly or falling back to another
    one, and never letting a `None` lookup reach an opaque error.
    """
    db.execute(
        delete(StorageLocationKind).where(StorageLocationKind.code == "consigned")
    )
    db.flush()
    with pytest.raises(RuntimeError, match=r"consigned.*app\.seeding load"):
        consign(db, house_auction, on_date=date(2026, 10, 1))


def test_consigning_twice_reuses_the_same_location(
    db: Session, heritage_venue: SalesVenue, make_item: ItemFactory
) -> None:
    """One consigned location per house, not one per auction."""
    first_auction = Auction(sales_venue_id=heritage_venue.id, title="Sale one")
    db.add(first_auction)
    db.flush()
    add_lot(
        db,
        first_auction,
        make_item(title="First sale item"),
        lot_number="1",
        reserve=None,
        price=Decimal("10.00"),
    )
    schedule(db, first_auction)
    consign(db, first_auction, on_date=date(2026, 10, 1))

    second_auction = Auction(sales_venue_id=heritage_venue.id, title="Sale two")
    db.add(second_auction)
    db.flush()
    add_lot(
        db,
        second_auction,
        make_item(title="Second sale item"),
        lot_number="1",
        reserve=None,
        price=Decimal("10.00"),
    )
    schedule(db, second_auction)
    consign(db, second_auction, on_date=date(2026, 11, 1))

    locations = db.scalars(
        select(StorageLocation).where(StorageLocation.institution == "Heritage")
    ).all()
    assert len(locations) == 1
