"""Offer claims and the offering writer (selling design, phase 2)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app import offering_writes, sale_state
from app.models import (
    ClaimState,
    InventoryItem,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    OfferClaim,
    SalesVenue,
    SalesVenueKind,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


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


# --- the writer ------------------------------------------------------------


def _venue(db: Session, code: str, kind: str = "marketplace") -> SalesVenue:
    """A platform to offer on, built the way the venue tests build one."""
    kind_id = db.scalar(select(SalesVenueKind.id).where(SalesVenueKind.code == kind))
    venue = SalesVenue(code=code, name=code.title(), sales_venue_kind_id=kind_id)
    db.add(venue)
    db.flush()
    return venue


def test_offering_an_item_creates_an_active_listing_and_claim(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item()
    ebay = _venue(db, "ebay-offer")

    listing = offering_writes.offer(
        db,
        item=item,
        venue=ebay,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("120.00"),
        title="1881-S Morgan",
        description="",
        external_id="12345",
        quantity=1,
    )
    db.commit()

    assert listing.status is ListingStatus.active
    assert listing.sales_venue_id == ebay.id
    claims = db.scalars(
        select(OfferClaim).where(OfferClaim.listing_id == listing.id)
    ).all()
    assert [c.state for c in claims] == [ClaimState.active]
    assert item.disposition.code == "listed"


def test_offering_elsewhere_pauses_the_store_listing(
    db: Session, listing: Listing
) -> None:
    item = listing.inventory_item
    ebay = _venue(db, "ebay-pause")

    elsewhere = offering_writes.offer(
        db,
        item=item,
        venue=ebay,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()
    db.refresh(listing)

    assert listing.status is ListingStatus.paused
    assert listing.paused_by_listing_id == elsewhere.id
    assert elsewhere.status is ListingStatus.active
    # The claim follows its listing: paused for the one set aside, active for
    # the offer that set it aside.
    states = {
        claim.listing_id: claim.state
        for claim in db.scalars(
            select(OfferClaim).where(OfferClaim.inventory_item_id == item.id)
        )
    }
    assert states == {listing.id: ClaimState.paused, elsewhere.id: ClaimState.active}


def test_a_second_offer_elsewhere_is_refused(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item()
    first = _venue(db, "ebay-first")
    second = _venue(db, "whatnot-second")
    offering_writes.offer(
        db,
        item=item,
        venue=first,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("10.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()

    with pytest.raises(offering_writes.OfferRefused, match="end it first"):
        offering_writes.offer(
            db,
            item=item,
            venue=second,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
            quantity=1,
        )


def test_a_second_offer_in_the_shop_is_refused(db: Session, listing: Listing) -> None:
    """Offering in the shop what the shop already offers is a mistake, not a pause."""
    store = db.get(SalesVenue, listing.sales_venue_id)
    assert store is not None

    with pytest.raises(
        offering_writes.OfferRefused, match="already offered in the shop"
    ):
        offering_writes.offer(
            db,
            item=listing.inventory_item,
            venue=store,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
            quantity=1,
        )


def test_an_unreceived_item_cannot_be_offered(
    db: Session, make_item: ItemFactory
) -> None:
    # `build_item` fixes the status at "received"; an item still on order is
    # made by moving it afterwards.
    item = make_item()
    item.status_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()
    db.flush()
    venue = _venue(db, "ebay-unreceived")

    with pytest.raises(offering_writes.OfferRefused, match="not received"):
        offering_writes.offer(
            db,
            item=item,
            venue=venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
            quantity=1,
        )


def test_a_retired_platform_cannot_be_offered_on(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item()
    venue = _venue(db, "ebay-retired")
    venue.is_active = False
    db.flush()

    with pytest.raises(offering_writes.OfferRefused, match="retired"):
        offering_writes.offer(
            db,
            item=item,
            venue=venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
            quantity=1,
        )


def test_ending_an_offer_resumes_the_paused_store_listing(
    db: Session, listing: Listing
) -> None:
    item = listing.inventory_item
    ebay = _venue(db, "ebay-resume")
    elsewhere = offering_writes.offer(
        db,
        item=item,
        venue=ebay,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()

    offering_writes.end_offer(db, elsewhere)
    db.commit()
    db.refresh(listing)

    assert elsewhere.status is ListingStatus.ended
    assert elsewhere.ended_at is not None
    assert listing.status is ListingStatus.active
    assert listing.paused_by_listing_id is None
    assert item.disposition.code == "listed"


def test_ending_a_sold_offer_ends_the_paused_store_listing(
    db: Session, listing: Listing
) -> None:
    """A sold item must not come back into the shop."""
    item = listing.inventory_item
    ebay = _venue(db, "ebay-sold")
    elsewhere = offering_writes.offer(
        db,
        item=item,
        venue=ebay,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()

    offering_writes.end_offer(db, elsewhere, sold=True)
    db.commit()
    db.refresh(listing)

    assert listing.status is ListingStatus.ended
    assert listing.paused_by_listing_id is None
    # The sale path sets `sold`; ending the offer must not undo that.
    assert item.disposition.code != "held"


def test_ending_the_only_offer_puts_the_item_back_to_held(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item()
    venue = _venue(db, "ebay-held")
    made = offering_writes.offer(
        db,
        item=item,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("10.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()

    offering_writes.end_offer(db, made)
    db.commit()

    assert item.disposition.code == "held"
    claims = db.scalars(
        select(OfferClaim).where(OfferClaim.listing_id == made.id)
    ).all()
    assert [c.state for c in claims] == [ClaimState.released]


def test_an_item_offered_elsewhere_counts_as_for_sale(
    db: Session, listing: Listing
) -> None:
    """The edit warning must fire for a paused store listing too."""
    item = listing.inventory_item
    ebay = _venue(db, "ebay-warning")
    offering_writes.offer(
        db,
        item=item,
        venue=ebay,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()

    uses = sale_state.for_sale(db, [item.id])

    assert len(uses[item.id]) == 2  # the active offer and the paused store listing
    assert any("on Ebay-Warning" in use.text for use in uses[item.id])
    assert any("(paused)" in use.text for use in uses[item.id])


def test_an_item_claimed_by_someone_elses_listing_counts_as_for_sale(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """The claim is what the warning reads, not only the listing's own item.

    A lot's listing (phase 3) names the lot, not the pieces in it. The item
    is then held by a claim and by nothing else, and the edit warning has to
    find it there.
    """
    member = make_item()
    _claim(db, member, listing, ClaimState.active)
    db.commit()

    uses = sale_state.for_sale(db, [member.id])

    assert [use.id for use in uses[member.id]] == [listing.id]


def test_the_shop_rule_is_the_same_in_python_and_sql(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """One home means the two forms cannot drift: they are checked together."""
    ebay = _venue(db, "ebay-rule")
    elsewhere = offering_writes.offer(
        db,
        item=make_item(),
        venue=ebay,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()

    sellable = set(
        db.scalars(
            select(Listing.id).where(*offering_writes.shop_listing_filters())
        ).all()
    )

    assert listing.id in sellable
    assert elsewhere.id not in sellable
    assert offering_writes.sellable_in_shop(listing)
    assert not offering_writes.sellable_in_shop(elsewhere)
