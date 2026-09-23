"""The database guarantees behind an auction lot, and consigned custody.

Each of these is a constraint rather than a rule in Python, for the same
reason `test_sales_lot_schema.py` gives: a rule two concurrent requests could
both believe they satisfy needs to live in the database, not just in the
application. This is schema only -- nothing writes these tables yet, so every
row here is built by hand rather than through a writer that does not exist.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app.models import (
    Auction,
    AuctionLot,
    AuctionLotResult,
    AuctionStatus,
    Currency,
    InventoryItem,
    Listing,
    ListingFormat,
    SalesVenue,
    StorageLocationKind,
)
from app.references import require_code
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _usd(db: Session) -> int:
    """The currency id every listing below needs. See test_sales_lot_schema.py."""
    return require_code(db, Currency, "USD", "currency")


def _listing(db: Session, item: InventoryItem, venue: SalesVenue) -> Listing:
    """An auction-format listing to attach one auction lot to.

    Auction-format because an `auction_lot` only ever details one --
    `conftest.check_auction_invariant` holds every test to that.
    """
    listing = Listing(
        inventory_item_id=item.id,
        sales_venue_id=venue.id,
        format=ListingFormat.auction,
        currency_id=_usd(db),
        price=Decimal("10.00"),
        quantity_available=1,
    )
    db.add(listing)
    db.flush()
    return listing


@pytest.fixture
def auction(db: Session, heritage_venue: SalesVenue) -> Auction:
    """An auction at an auction house, ready to have lots consigned to it."""
    row = Auction(sales_venue_id=heritage_venue.id, title="September Signature Sale")
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def other_auction(db: Session, heritage_venue: SalesVenue) -> Auction:
    """A second, distinct auction on the same platform."""
    row = Auction(sales_venue_id=heritage_venue.id, title="October Signature Sale")
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def lot_listing(
    db: Session,
    received_item: InventoryItem,
    heritage_venue: SalesVenue,
) -> Listing:
    """A listing to attach exactly one auction lot to."""
    return _listing(db, received_item, heritage_venue)


def test_a_lot_number_is_unique_within_its_auction(
    db: Session,
    auction: Auction,
    heritage_venue: SalesVenue,
    make_item: Callable[..., InventoryItem],
) -> None:
    """Two lot 14s in one sale is an unresolvable record."""
    first_listing = _listing(db, make_item(), heritage_venue)
    second_listing = _listing(db, make_item(), heritage_venue)
    db.add(
        AuctionLot(auction_id=auction.id, listing_id=first_listing.id, lot_number="14")
    )
    db.flush()
    db.add(
        AuctionLot(auction_id=auction.id, listing_id=second_listing.id, lot_number="14")
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "uq_auction_lot_auction_lot_number" in str(excinfo.value)


def test_the_same_lot_number_in_two_auctions_is_fine(
    db: Session,
    auction: Auction,
    other_auction: Auction,
    heritage_venue: SalesVenue,
    make_item: Callable[..., InventoryItem],
) -> None:
    """Lot numbers restart every sale."""
    first_listing = _listing(db, make_item(), heritage_venue)
    second_listing = _listing(db, make_item(), heritage_venue)
    db.add(
        AuctionLot(auction_id=auction.id, listing_id=first_listing.id, lot_number="14")
    )
    db.add(
        AuctionLot(
            auction_id=other_auction.id, listing_id=second_listing.id, lot_number="14"
        )
    )
    db.flush()  # must not raise: the unique pair is (auction_id, lot_number)


def test_one_auction_lot_per_listing(
    db: Session, auction: Auction, lot_listing: Listing
) -> None:
    """`listing_id` is unique: an auction lot *is* the detail of one listing."""
    db.add(AuctionLot(auction_id=auction.id, listing_id=lot_listing.id, lot_number="1"))
    db.flush()
    db.add(AuctionLot(auction_id=auction.id, listing_id=lot_listing.id, lot_number="2"))
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "uq_auction_lot_listing_id" in str(excinfo.value)


def test_consigned_is_a_storage_location_kind(db: Session) -> None:
    """Custody at an auction house is tracked as a location, per the spec."""
    assert db.scalar(
        select(StorageLocationKind).where(StorageLocationKind.code == "consigned")
    )


def test_a_new_auction_defaults_to_draft_and_version_one(
    db: Session, heritage_venue: SalesVenue
) -> None:
    """Pins the status enum's first member and the optimistic-concurrency start."""
    row = Auction(sales_venue_id=heritage_venue.id, title="A sale")
    db.add(row)
    db.flush()
    assert row.status == AuctionStatus.draft
    assert row.version == 1


@pytest.mark.auction_invariant_waiver(
    reason=(
        "a column test: it sets a result and hammer price on a draft "
        "auction's lot by hand, with no buyer, which only settle may write"
    )
)
def test_a_lot_result_is_null_until_settled(
    db: Session, auction: Auction, lot_listing: Listing
) -> None:
    """`result` and the money columns start empty; a settled lot fills them in."""
    lot = AuctionLot(auction_id=auction.id, listing_id=lot_listing.id, lot_number="1")
    db.add(lot)
    db.flush()
    assert lot.result is None
    assert lot.hammer_price is None
    assert lot.buyer_customer_id is None

    lot.result = AuctionLotResult.sold
    lot.hammer_price = Decimal("250.00")
    db.flush()
    assert lot.result == AuctionLotResult.sold
