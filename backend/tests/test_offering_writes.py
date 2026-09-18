"""Offer claims and the offering writer (selling design, phase 2)."""

from __future__ import annotations

import pytest
from app.models import ClaimState, InventoryItem, Listing, ListingStatus, OfferClaim
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _claim(
    db: Session, item: InventoryItem, listing: Listing, state: ClaimState
) -> OfferClaim:
    claim = OfferClaim(inventory_item_id=item.id, listing_id=listing.id, state=state)
    db.add(claim)
    db.flush()
    return claim


def test_one_item_can_have_only_one_active_claim(
    db: Session, listing: Listing, make_listing: object
) -> None:
    """The database, not the application, is what refuses the second offer."""
    item_id = listing.inventory_item_id
    second = make_listing(inventory_item_id=item_id)
    db.add(
        OfferClaim(
            inventory_item_id=item_id, listing_id=listing.id, state=ClaimState.active
        )
    )
    db.flush()

    db.add(
        OfferClaim(
            inventory_item_id=item_id, listing_id=second.id, state=ClaimState.active
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_paused_claim_does_not_block_an_active_one(
    db: Session, listing: Listing, make_listing: object
) -> None:
    item_id = listing.inventory_item_id
    second = make_listing(inventory_item_id=item_id)
    db.add(
        OfferClaim(
            inventory_item_id=item_id, listing_id=listing.id, state=ClaimState.paused
        )
    )
    db.add(
        OfferClaim(
            inventory_item_id=item_id, listing_id=second.id, state=ClaimState.active
        )
    )

    db.flush()  # no IntegrityError

    assert db.query(OfferClaim).filter_by(inventory_item_id=item_id).count() == 2


def test_a_released_claim_does_not_block_an_active_one(
    db: Session, listing: Listing, make_listing: object
) -> None:
    item_id = listing.inventory_item_id
    second = make_listing(inventory_item_id=item_id)
    db.add(
        OfferClaim(
            inventory_item_id=item_id, listing_id=listing.id, state=ClaimState.released
        )
    )
    db.add(
        OfferClaim(
            inventory_item_id=item_id, listing_id=second.id, state=ClaimState.active
        )
    )

    db.flush()

    assert db.query(OfferClaim).filter_by(inventory_item_id=item_id).count() == 2


def test_a_listing_records_what_paused_it(
    db: Session, listing: Listing, make_listing: object
) -> None:
    """Settlement needs to know which offer to resume a store listing for."""
    elsewhere = make_listing(inventory_item_id=listing.inventory_item_id)
    listing.status = ListingStatus.paused
    listing.paused_by_listing_id = elsewhere.id
    db.commit()
    db.refresh(listing)

    assert listing.paused_by.id == elsewhere.id
