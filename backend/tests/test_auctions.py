"""Auction transitions: adding and removing lots, and the auction's own life.

`app.auctions` is the sole writer of `auction` and `auction_lot`. Adding or
removing a lot **is** an offer or an ending, so most of what these tests
prove is that `app.auctions` hands off to `offering_writes` correctly rather
than reimplementing its rules -- the same reason `test_lot_writes.py` builds
its fixtures through `offering_writes.offer` rather than a bare `Listing(...)`.

Fix round 1 (rulings R8, R9, R10) added: the status-boundary tests Important
#2/#3 named as missing, the `add_lot`/`consign` staleness regression
Important #1 named, and the `returned_to_location_id` coverage Important #4
required.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from app import lot_writes, offering_writes
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
    User,
)
from app.routers.inventory import receive_items
from app.schemas import ReceiveRequest
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory
from tests.conftest import build_lot, item_of

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


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
    """An auction already closed, so no more lots may be added.

    `close` no longer accepts `draft` (ruling R8), so this fixture must
    schedule first -- closing straight from `draft` is now itself a refusal,
    covered by `test_closing_a_draft_auction_is_refused`.
    """
    schedule(db, auction)
    close(db, auction)
    return auction


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def items_of(auction_row: Auction) -> list[InventoryItem]:
    """Every item held by this auction's lots.

    Takes no `Session`: every relationship it walks (`Auction.lots`,
    `Listing.sales_lot`, `SalesLot.members`) is reachable through the object
    graph on the session `auction_row` is already attached to, the same way
    `lot_writes.members_held` reads a loaded collection rather than asking
    for one.

    Every listing `add_lot` creates is a **lot** listing -- even a single
    item is wrapped into a lot of one (`_lot_of_one`) -- so this only ever
    walks `Listing.sales_lot`, never `Listing.inventory_item`. An earlier
    version branched on both and the item-listing branch was dead code
    (fix round 1, Minor #10); removed rather than kept unreachable.
    """
    items: list[InventoryItem] = []
    for row in auction_row.lots:
        lot = row.listing.sales_lot
        assert lot is not None
        items.extend(
            member.item for member in lot.members if member.released_at is None
        )
    return items


def location_history(db: Session, item: InventoryItem) -> list[StorageLocation]:
    """The item's location moves in order, oldest first, each location resolved.

    `LocationHistory` itself carries only `storage_location_id`, no
    relationship -- nothing else in the codebase needed one. Every row this
    module writes carries a real, non-`NULL` location: `consign` and the
    return-from-consignment path both call `lifecycle_writes.set_location`
    with a concrete id, never `None`. An earlier version of this helper
    handled a `None` location for that reason and the branch was dead code
    (fix round 1, Minor #10); removed rather than kept unreachable -- a
    `NULL` move, if this file ever needs one, asserts loudly here instead of
    silently returning `None`.
    """
    rows = db.scalars(
        select(LocationHistory)
        .where(LocationHistory.inventory_item_id == item.id)
        .order_by(LocationHistory.id)
    ).all()
    locations: list[StorageLocation] = []
    for row in rows:
        assert row.storage_location_id is not None
        location = db.get(StorageLocation, row.storage_location_id)
        assert location is not None
        locations.append(location)
    return locations


def _home_location(db: Session) -> StorageLocation:
    """A plain, non-consigned location, for a test to return items to."""
    kind_id = db.scalar(
        select(StorageLocationKind.id).where(StorageLocationKind.code == "home")
    )
    assert kind_id is not None
    location = StorageLocation(storage_location_kind_id=kind_id)
    db.add(location)
    db.flush()
    return location


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


def test_add_lot_accepts_explicit_title_description_and_external_id(
    db: Session, auction: Auction, received_item: InventoryItem
) -> None:
    """Ruling 1's overrides, not just its defaults (fix round 1, Minor #9)."""
    auction_lot = add_lot(
        db,
        auction,
        received_item,
        lot_number="1",
        reserve=None,
        price=Decimal("10.00"),
        title="Custom lot title",
        description="Custom lot description",
        external_id="LOT-42",
    )
    listing = auction_lot.listing
    assert listing.title == "Custom lot title"
    assert listing.description == "Custom lot description"
    assert listing.external_id == "LOT-42"


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


def test_add_lot_is_visible_to_consign_even_if_lots_was_loaded_first(
    db: Session, auction: Auction, make_item: ItemFactory
) -> None:
    """Regression for Important #1, fix round 1.

    `AuctionLot(auction_id=auction.id, ...)` fired no backref event, so a
    session that had already loaded `auction.lots` kept seeing a stale,
    short collection -- and `consign` and `cancel` both iterate it. Loading
    `auction.lots` *before* the add is exactly the shape every other fixture
    in this file avoided by loading it only afterward, which is why 15
    passing tests missed this the first time. `AuctionLot(auction=auction,
    listing=listing, ...)` -- relationship assignment -- is the fix.

    `item` is built *before* `auction.lots` is loaded, not after: `make_item`
    commits internally (`tests/conftest.py`'s `build_item`), and a commit
    expires every loaded attribute in the session by default -- including
    `auction.lots` -- which would silently reload it fresh on next access and
    mask the very bug this test exists to catch. This ordering mistake is
    exactly why the first version of this test passed against the
    FK-only construction it was meant to red; caught by mutation-testing this
    test itself, not assumed.
    """
    item = make_item(title="Late addition")
    assert auction.lots == []  # loads and caches the (empty) collection last
    add_lot(db, auction, item, lot_number="1", reserve=None, price=Decimal("10.00"))
    # The stale-collection bug would show 0 lots here, before consign even runs.
    assert len(auction.lots) == 1

    schedule(db, auction)
    consign(db, auction, on_date=date(2026, 10, 1))

    for item in items_of(auction):
        location = db.get(StorageLocation, item.storage_location_id)
        assert location is not None
        assert location.kind.code == "consigned"


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


def test_removing_a_lot_from_a_closed_auction_is_refused(
    db: Session, auction_with_three_lots: Auction
) -> None:
    """`remove_lot`'s own status gate (Important #2, fix round 1).

    Distinct from `cancel`'s: `remove_lot` refuses `closed` even though
    `cancel` (ruling R8) now accepts it -- pulling a single lot out of a
    closed auction makes no sense; abandoning the whole auction does.
    """
    schedule(db, auction_with_three_lots)
    close(db, auction_with_three_lots)
    (one_lot,) = auction_with_three_lots.lots[:1]
    with pytest.raises(AuctionRefused, match="closed"):
        remove_lot(db, one_lot)


def test_removing_a_lot_from_a_consigned_auction_requires_a_return_location(
    db: Session, house_auction: Auction
) -> None:
    """Ruling R9: items physically left the premises; withdrawal must say where."""
    consign(db, house_auction, on_date=date(2026, 10, 1))
    (auction_lot,) = house_auction.lots
    with pytest.raises(AuctionRefused, match="returned_to_location_id"):
        remove_lot(db, auction_lot)


def test_removing_a_lot_from_a_consigned_auction_moves_its_items_back(
    db: Session, house_auction: Auction
) -> None:
    """Ruling R9: the items physically come home before the lot is withdrawn."""
    consign(db, house_auction, on_date=date(2026, 10, 1))
    (auction_lot,) = house_auction.lots
    items = list(items_of(house_auction))
    home = _home_location(db)

    remove_lot(db, auction_lot, returned_to_location_id=home.id)

    for item in items:
        db.refresh(item)
        assert item.storage_location_id == home.id
        assert location_history(db, item)[-1].id == home.id


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


def test_closing_a_draft_auction_is_refused(db: Session, auction: Auction) -> None:
    """Ruling R8: a `draft` auction never happened -- there is nothing to close."""
    with pytest.raises(AuctionRefused, match="draft"):
        close(db, auction)


def test_closing_a_closed_auction_is_refused(
    db: Session, closed_auction: Auction
) -> None:
    """Important #2, fix round 1: `close`'s own guard, exercised for real."""
    with pytest.raises(AuctionRefused, match="closed"):
        close(db, closed_auction)


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


def test_cancelling_a_closed_auction_is_allowed(
    db: Session, auction_with_three_lots: Auction
) -> None:
    """Ruling R8, Important #3: a closed sale can still be abandoned.

    `close` accepts `scheduled`/`consigned` only, so this schedules first;
    the point under test is that `cancel`, unlike `remove_lot`, does not
    refuse `closed`.
    """
    schedule(db, auction_with_three_lots)
    close(db, auction_with_three_lots)
    listings = [row.listing for row in auction_with_three_lots.lots]

    cancel(db, auction_with_three_lots)

    assert auction_with_three_lots.status is AuctionStatus.cancelled
    for row in listings:
        db.refresh(row)
        assert row.status is ListingStatus.ended


def test_cancelling_a_settled_auction_is_refused(db: Session, auction: Auction) -> None:
    """A settled sale is a fact, not a draft to discard."""
    auction.status = AuctionStatus.settled
    db.flush()
    with pytest.raises(AuctionRefused, match="settled"):
        cancel(db, auction)


def test_cancelling_a_cancelled_auction_is_refused(
    db: Session, auction: Auction
) -> None:
    """Important #2, fix round 1: the other half of `cancel`'s refusal tuple."""
    cancel(db, auction)
    with pytest.raises(AuctionRefused, match="cancelled"):
        cancel(db, auction)


def test_cancelling_a_consigned_auction_requires_a_return_location(
    db: Session, house_auction: Auction
) -> None:
    """Ruling R9: every lot needs somewhere to return its items to."""
    consign(db, house_auction, on_date=date(2026, 10, 1))
    with pytest.raises(AuctionRefused, match="returned_to_location_id"):
        cancel(db, house_auction)


def test_cancelling_a_consigned_auction_with_no_lots_requires_a_return_location(
    db: Session, heritage_venue: SalesVenue
) -> None:
    """`cancel`'s own guard, not `_remove_lot`'s borrowed one.

    A consigned auction can have zero lots -- `consign` never requires at
    least one -- so `_remove_lot`'s identical check, which only runs inside
    the per-lot removal loop, never fires here. This is the one test that can
    red `cancel`'s own top-level check on its own.
    """
    row = Auction(sales_venue_id=heritage_venue.id, title="Empty consigned sale")
    db.add(row)
    db.flush()
    schedule(db, row)
    consign(db, row, on_date=date(2026, 10, 1))
    with pytest.raises(AuctionRefused, match="returned_to_location_id"):
        cancel(db, row)


def test_cancelling_a_consigned_auction_returns_items_and_clears_consigned_on(
    db: Session, house_auction: Auction
) -> None:
    """Ruling R9: nothing is left claiming a house that no longer holds the coins."""
    consign(db, house_auction, on_date=date(2026, 10, 1))
    items = list(items_of(house_auction))
    home = _home_location(db)

    cancel(db, house_auction, returned_to_location_id=home.id)

    assert house_auction.status is AuctionStatus.cancelled
    assert house_auction.consigned_on is None
    for item in items:
        db.refresh(item)
        assert item.storage_location_id == home.id


# --------------------------------------------------------------------------
# Ruling R13, fix round 2: custody is tracked by consigned_on, not status
# --------------------------------------------------------------------------


def test_cancelling_a_closed_consigned_auction_requires_a_return_location(
    db: Session, house_auction: Auction
) -> None:
    """The defect fix round 2 exists to close.

    `close` accepts a `consigned` auction and does not clear `consigned_on`,
    so `consign -> close -> cancel` used to read `status == closed`, not
    `consigned`, and skip the return-location requirement entirely --
    leaving every coin filed at the house with nothing linking it back to
    the cancelled auction. Keying the requirement on `auction.consigned_on
    is not None` instead closes it.
    """
    consign(db, house_auction, on_date=date(2026, 10, 1))
    close(db, house_auction)
    assert house_auction.status is AuctionStatus.closed
    assert house_auction.consigned_on is not None

    with pytest.raises(AuctionRefused, match="returned_to_location_id"):
        cancel(db, house_auction)


def test_cancelling_a_closed_consigned_auction_returns_items_and_clears_consigned_on(
    db: Session, house_auction: Auction
) -> None:
    """Given a return location, the closed-but-consigned auction cancels cleanly."""
    consign(db, house_auction, on_date=date(2026, 10, 1))
    close(db, house_auction)
    items = list(items_of(house_auction))
    home = _home_location(db)

    cancel(db, house_auction, returned_to_location_id=home.id)

    assert house_auction.status is AuctionStatus.cancelled
    assert house_auction.consigned_on is None
    remaining = db.scalars(
        select(AuctionLot).where(AuctionLot.auction_id == house_auction.id)
    ).all()
    assert remaining == []
    for item in items:
        db.refresh(item)
        assert item.storage_location_id == home.id


def test_removing_one_lot_from_a_consigned_auction_leaves_consigned_on_set(
    db: Session, heritage_venue: SalesVenue, make_item: ItemFactory
) -> None:
    """Partial return: the house still holds what was not brought home.

    `consigned_on` names an auction-level fact -- something of this
    auction's is still at the house -- so returning one lot out of two must
    not clear it, even though that lot's own items really did come home.

    Reaches the rule through a path that can actually occur (ruling R14, fix
    round 3, replacing round 2's post-`close` version of this test): the
    auction stays `consigned`, never `close`d, because `remove_lot` refuses
    `closed` unconditionally now -- pulling a single lot from a *closed*
    auction is `settle`'s job (`AuctionLotResult.withdrawn`), not this
    function's.
    """
    auction = Auction(sales_venue_id=heritage_venue.id, title="Two-lot house sale")
    db.add(auction)
    db.flush()
    first = add_lot(
        db,
        auction,
        make_item(title="Lot one"),
        lot_number="1",
        reserve=None,
        price=Decimal("10.00"),
    )
    add_lot(
        db,
        auction,
        make_item(title="Lot two"),
        lot_number="2",
        reserve=None,
        price=Decimal("10.00"),
    )
    schedule(db, auction)
    consign(db, auction, on_date=date(2026, 10, 1))
    assert auction.status is AuctionStatus.consigned
    consigned_on = auction.consigned_on
    assert consigned_on is not None

    first_lot = first.listing.sales_lot
    assert first_lot is not None
    first_items = [
        member.item for member in first_lot.members if member.released_at is None
    ]
    home = _home_location(db)

    remove_lot(db, first, returned_to_location_id=home.id)

    for item in first_items:
        db.refresh(item)
        assert item.storage_location_id == home.id
    # The house still holds the second lot -- custody has not fully returned.
    assert auction.consigned_on == consigned_on
    remaining = db.scalars(
        select(AuctionLot).where(AuctionLot.auction_id == auction.id)
    ).all()
    assert len(remaining) == 1


def test_removing_a_lot_from_a_closed_auction_is_refused_even_if_consigned(
    db: Session, house_auction: Auction
) -> None:
    """Ruling R14, fix round 3: `closed` refuses unconditionally.

    Complements `test_removing_a_lot_from_a_closed_auction_is_refused`
    (fix round 1), which covers a `closed` auction that was **never**
    consigned; this covers the other half of "whether or not it was ever
    consigned". Once closed, a lot that did not sell is
    `AuctionLotResult.withdrawn` -- a settlement result Task 3's `settle`
    will record, along with returning its items -- not something this
    function may pull out with no record of what became of it. Reverts fix
    round 2's own widening of this same gate, which the coordinator ruled
    was not intended.
    """
    consign(db, house_auction, on_date=date(2026, 10, 1))
    close(db, house_auction)
    assert house_auction.status is AuctionStatus.closed
    assert house_auction.consigned_on is not None
    (auction_lot,) = house_auction.lots

    with pytest.raises(AuctionRefused, match="closed"):
        remove_lot(db, auction_lot, returned_to_location_id=1)


def test_cancelling_a_never_consigned_auction_still_needs_no_return_location(
    db: Session, auction_with_three_lots: Auction
) -> None:
    """The predicate must not over-fire: no custody means no requirement.

    Same scenario `test_cancelling_removes_every_lot` already exercises,
    named explicitly here as the fix round 2 confirmation the coordinator
    asked for: `auction_with_three_lots` was never consigned, so
    `auction.consigned_on is None` throughout, and `cancel` must still ask
    for nothing.
    """
    assert auction_with_three_lots.consigned_on is None
    cancel(db, auction_with_three_lots)  # must not raise
    assert auction_with_three_lots.status is AuctionStatus.cancelled


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
        assert location_history(db, item)[-1].institution == "Heritage"
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


def test_consigned_location_lookup_ignores_a_row_with_an_identifier(
    db: Session, house_auction: Auction
) -> None:
    """Minor #5, fix round 1: the lookup matches the table's real uniqueness key.

    `uq_storage_location_identity` is `(kind_id, institution, identifier)`,
    not just the first two. A "Consigned: Heritage" row that also carries an
    `identifier` -- a crate or shelf the owner recorded by hand -- must not
    be reused: `consign` only ever creates rows with `identifier IS NULL`.
    """
    kind_id = db.scalar(
        select(StorageLocationKind.id).where(StorageLocationKind.code == "consigned")
    )
    assert kind_id is not None
    decoy = StorageLocation(
        storage_location_kind_id=kind_id, institution="Heritage", identifier="Shelf 3"
    )
    db.add(decoy)
    db.flush()

    consign(db, house_auction, on_date=date(2026, 10, 1))

    for item in items_of(house_auction):
        location = db.get(StorageLocation, item.storage_location_id)
        assert location is not None
        assert location.id != decoy.id
        assert location.identifier is None


# --------------------------------------------------------------------------
# Whole-branch review, Important #2: a receipt may not end an auction lot
# --------------------------------------------------------------------------


def test_marking_one_member_of_an_auction_lot_missing_is_refused(
    db: Session, admin_user: User, auction_lot: AuctionLot
) -> None:
    """`POST /inventory/receive` must not end an auction lot's offer. 409.

    The third door onto an orphaned `auction_lot`, after
    `routers.offers.end_listing` and `sales_writes.record_sale`, and the one
    the whole-branch review found still open (Important #2). It was thought
    unreachable because a coin in an auction has already been received --
    false by one line: the "already received" refusal in `receive_items` is
    conditioned on `payload.outcome == "received"`, and `missing`, `returned`
    and `canceled`, the three outcomes that *end an offer*, skip it. From
    there `offering_writes.offers_holding` is format-blind and hands the
    endpoint the lot's `auction`-format listing through its derived half.

    **The multi-member case, which nothing on the branch covered.** The race
    suite's own helper builds single-coin lots, so the damage it could show
    was confined to the contested coin. Here one member of a two-member lot
    is marked `missing`. Measured against the unguarded endpoint, by removing
    `routers.inventory._refuse_auction_lots` and reading the rows back:

        lot listing status:            ended
        sales lot status:              dissolved
        other member's store listing:  active   (resumed, back in the shop)
        auction_lot row still present: True

    -- the **whole lot** out of the sale over one missing coin, the innocent
    member quietly returned to the store, and an `auction_lot` row still
    holding its lot number and pointing at an ended listing, which is what
    `consign` then silently skips and what `settle` reads as an empty lot.
    (A member with no other offer would go back to `held` instead of to the
    shop; this fixture's `stored_item` has a store listing, so it shows the
    resumption.) So the assertions below are deliberately about the member
    that was **not** received.

    The refusal is raised under `lock_for_sale`'s locks, after the
    authoritative `offers_holding` re-read, so nothing is written when it
    fires -- the endpoint commits once, at the end. The pending status write
    that got as far as a flush is discarded with the savepoint here, exactly
    as `get_db` discards it in production when the handler raises.

    The trade is stated in the message, because the operator has to act on
    it: remove the lot from the auction first, then record the loss. Ruling
    R25 accepted the same two-step for Record sale.
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

    savepoint = db.begin_nested()
    with pytest.raises(HTTPException) as refused:
        receive_items(
            ReceiveRequest(
                item_ids=[plain_item.id],
                outcome="missing",
                acknowledge_for_sale=True,
            ),
            db,
            admin_user,
        )
    savepoint.rollback()

    assert refused.value.status_code == 409
    detail = str(refused.value.detail)
    assert f"listing #{listing.id} of auction #{auction_lot.auction_id}" in detail
    # The way out, named: a refusal an operator cannot act on is a 500 with
    # better manners.
    assert "Take the lot out of the auction first" in detail
    assert "settle it as withdrawn" in detail
    assert "missing" in detail

    db.refresh(listing)
    db.refresh(lot)
    db.refresh(store_listing)
    db.refresh(stored_item)
    db.refresh(plain_item)
    # The lot is still on sale, whole: this is the assertion that fails
    # against the unguarded endpoint.
    assert listing.status is ListingStatus.active
    assert lot.status is SalesLotStatus.offered
    assert {member.inventory_item_id for member in lot.members} == {
        stored_item.id,
        plain_item.id,
    }
    # The innocent member is untouched -- not released back to `held`, and
    # its store listing still set aside by the auction rather than resumed
    # under it.
    assert stored_item.disposition.code == "listed"
    assert store_listing.status is ListingStatus.paused
    # And the coin the receipt named kept its status, because nothing was
    # committed.
    assert plain_item.status.code == "received"
    assert db.get(AuctionLot, auction_lot.id) is not None


def test_marking_a_coin_on_a_direct_auction_listing_missing_ends_it(
    db: Session, admin_user: User, make_item: ItemFactory, ebay_venue: SalesVenue
) -> None:
    """A receipt still ends an auction-format listing that is in no auction.

    The Offer dialog offers a coin directly on eBay by auction; no
    `auction_lot` row exists, so there is nothing to take it out of first.
    `_refuse_auction_lots` keyed on `format` alone would have refused this
    with advice that cannot be followed (review of the final fix wave,
    Important #1). It keys on the `auction_lot` row now, so the receipt ends
    the offer the way it ends any other.
    """
    item = make_item()
    listing = offering_writes.offer(
        db,
        item=item,
        venue=ebay_venue,
        listing_format=ListingFormat.auction,
        price=Decimal("10.00"),
        title="1921 Morgan dollar",
        description="",
        external_id=None,
    )
    db.flush()

    receive_items(
        ReceiveRequest(
            item_ids=[item.id], outcome="missing", acknowledge_for_sale=True
        ),
        db,
        admin_user,
    )

    db.expire_all()
    assert db.get_one(Listing, listing.id).status is ListingStatus.ended
    assert db.get_one(InventoryItem, item.id).status.code == "missing"


def test_consigning_notes_the_move_with_the_auction(
    db: Session, house_auction: Auction
) -> None:
    """History says *why* the item moved, not just where."""
    consign(db, house_auction, on_date=date(2026, 10, 1))
    items = items_of(house_auction)
    assert items, "the auction holds no items, so nothing here is checked"
    for item in items:
        rows = db.scalars(
            select(LocationHistory)
            .where(LocationHistory.inventory_item_id == item.id)
            .order_by(LocationHistory.id.desc())
        ).first()
        assert rows is not None
        assert rows.note == f"Consigned to auction #{house_auction.id}"
