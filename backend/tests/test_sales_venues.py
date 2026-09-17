"""Sales platforms (selling design, phase 1)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from app.models import (
    Listing,
    ListingFormat,
    ListingStatus,
    SalesVenue,
    SalesVenueKind,
    Vendor,
)
from app.routers.reference import retirable
from app.sales_venues import STORE_CODE, ensure_store_venue, store_venue_id
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_the_platform_kinds_are_seeded(db: Session) -> None:
    codes = set(db.scalars(select(SalesVenueKind.code)))
    assert codes == {"own_store", "marketplace", "live_auction", "auction_house"}


def test_platform_kinds_cannot_be_retired() -> None:
    # The store is found by kind, and the API branches on it.
    assert not retirable("sales_venue_kind", "marketplace")


def _kind_id(db: Session, code: str) -> int:
    return db.scalar(select(SalesVenueKind.id).where(SalesVenueKind.code == code))


def test_the_test_database_has_exactly_one_store(db: Session) -> None:
    stores = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).all()
    assert [s.code for s in stores] == [STORE_CODE]
    assert store_venue_id(db) == stores[0].id


def test_ensure_store_venue_is_idempotent(db: Session) -> None:
    assert ensure_store_venue(db) == store_venue_id(db)


def test_a_second_store_is_refused_by_the_database(db: Session) -> None:
    db.add(
        SalesVenue(
            code="store2",
            name="Another store",
            sales_venue_kind_id=_kind_id(db, "own_store"),
            is_own_store=True,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_purchase_source_links_to_at_most_one_platform(db: Session) -> None:
    vendor = Vendor(name="linked.example")
    db.add(vendor)
    db.flush()
    kind = _kind_id(db, "marketplace")
    db.add(
        SalesVenue(code="a", name="A", sales_venue_kind_id=kind, vendor_id=vendor.id)
    )
    db.add(
        SalesVenue(code="b", name="B", sales_venue_kind_id=kind, vendor_id=vendor.id)
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_is_active_follows_status(db: Session, listing: Listing) -> None:
    assert listing.status is ListingStatus.active
    assert listing.is_active is True

    listing.status = ListingStatus.paused
    db.commit()
    db.refresh(listing)
    assert listing.is_active is False


def test_a_new_listing_is_active_fixed_price(db: Session, listing: Listing) -> None:
    assert listing.format is ListingFormat.fixed_price
    assert listing.sales_venue_id == store_venue_id(db)


def test_public_catalog_shows_only_store_fixed_price_listings(
    db: Session, make_listing: Callable[..., Listing]
) -> None:
    store_listing = make_listing()
    ebay = SalesVenue(
        code="ebay", name="eBay", sales_venue_kind_id=_kind_id(db, "marketplace")
    )
    db.add(ebay)
    db.flush()
    elsewhere = make_listing(sales_venue_id=ebay.id)
    auction = make_listing(format=ListingFormat.auction)

    shown = set(db.scalars(text("select listing_id from public_catalog")))
    assert store_listing.id in shown
    assert elsewhere.id not in shown
    assert auction.id not in shown
