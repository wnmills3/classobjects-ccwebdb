"""Offer claims and the offering writer (selling design, phase 2)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app import offering_writes, sale_state
from app.models import (
    ClaimState,
    Customer,
    Disposition,
    InventoryItem,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    OfferClaim,
    SalesOrder,
    SalesOrderItem,
    SalesOrderStatus,
    SalesVenue,
    SalesVenueKind,
    User,
    utcnow,
)
from sqlalchemy import select, update
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


def _offer_on(db: Session, item: InventoryItem, venue: SalesVenue) -> Listing:
    """Offer one item on one platform, with the fields no test cares about."""
    return offering_writes.offer(
        db,
        item=item,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )


def _claim_states(db: Session, item: InventoryItem) -> dict[int, ClaimState]:
    """Every claim on one item, by the listing it holds it for."""
    return {
        claim.listing_id: claim.state
        for claim in db.scalars(
            select(OfferClaim).where(OfferClaim.inventory_item_id == item.id)
        )
    }


def _order_holding(
    db: Session,
    listing: Listing,
    customer_user: User,
    admin_user: User,
    status_code: str = "pending",
) -> SalesOrder:
    """An order with one line on this listing, in the state the code names."""
    customer = Customer(user_id=customer_user.id, display_name="Buyer")
    db.add(customer)
    db.flush()
    order = SalesOrder(
        customer_id=customer.id,
        sales_venue_id=listing.sales_venue_id,
        sales_order_status_id=db.scalars(
            select(SalesOrderStatus.id).where(SalesOrderStatus.code == status_code)
        ).one(),
        placed_by_id=admin_user.id,
    )
    db.add(order)
    db.flush()
    db.add(
        SalesOrderItem(
            sales_order_id=order.id,
            listing_id=listing.id,
            quantity=1,
            unit_price=Decimal("99.00"),
        )
    )
    db.flush()
    return order


def _set_disposition(db: Session, item: InventoryItem, code: str) -> None:
    """Move an item's disposition the way another write path would."""
    item.disposition_id = db.scalars(
        select(Disposition.id).where(Disposition.code == code)
    ).one()
    db.flush()


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


def test_a_sold_item_cannot_be_offered(db: Session, listing: Listing) -> None:
    """The other half of the asymmetry `end_offer` already keeps.

    A shop sale leaves the store listing active at zero stock and the item
    `sold` (`order_writes._after_stock_change`), and `end_offer` is careful
    never to file that item back as `held`. Without the same care here,
    offering it on a platform pauses the store listing and rewrites `sold`
    back to `listed` -- putting the buyer's coin up for sale a second time.

    Nothing may be written either: every refusal is decided before the first
    write, which is what makes a batch of offers all or nothing.
    """
    item = listing.inventory_item
    listing.quantity_available = 0
    _set_disposition(db, item, "sold")
    db.commit()
    ebay = _venue(db, "ebay-already-sold")

    with pytest.raises(offering_writes.OfferRefused) as refused:
        _offer_on(db, item, ebay)

    assert refused.value.item_code == item.item_code
    assert "sold" in refused.value.reason
    assert str(refused.value).startswith(f"{item.item_code}: ")
    assert listing.status is ListingStatus.active
    assert item.disposition.code == "sold"
    assert _claim_states(db, item) == {}


def test_an_item_an_unshipped_order_holds_cannot_be_offered(
    db: Session, listing: Listing, customer_user: User, admin_user: User
) -> None:
    """A buyer's unshipped order is a claim on the coin, not just on stock.

    `app.sale_state` already counts a pending, paid or packed order as the
    item being spoken for -- it is why the console refuses a silent edit.
    The same order is a reason not to offer the coin somewhere else, and
    asking `sale_state` keeps one definition of "spoken for" rather than two
    that can drift.
    """
    item = listing.inventory_item
    order = _order_holding(db, listing, customer_user, admin_user)
    db.commit()
    ebay = _venue(db, "ebay-on-order")

    with pytest.raises(offering_writes.OfferRefused) as refused:
        _offer_on(db, item, ebay)

    assert refused.value.item_code == item.item_code
    assert f"order #{order.id}" in refused.value.reason
    assert listing.status is ListingStatus.active
    assert _claim_states(db, item) == {}


def test_an_item_whose_order_has_shipped_can_be_offered_again(
    db: Session, listing: Listing, customer_user: User, admin_user: User
) -> None:
    """Only an *unshipped* order holds the item; a returned coin is offerable.

    `sale_state` stops counting an order once it ships, and a coin the buyer
    sent back is `returned_by_buyer` -- in hand, and offering it again is
    exactly what happens next. Refusing either would be a dead end, since
    nothing moves a disposition backwards.
    """
    item = listing.inventory_item
    _order_holding(db, listing, customer_user, admin_user, status_code="shipped")
    _set_disposition(db, item, "returned_by_buyer")
    db.commit()
    ebay = _venue(db, "ebay-returned")

    made = _offer_on(db, item, ebay)
    db.commit()

    assert made.status is ListingStatus.active
    assert item.disposition.code == "listed"


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
    db.refresh(listing)
    # The listing has to be paused for resuming it to mean anything.
    assert listing.status is ListingStatus.paused

    offering_writes.end_offer(db, elsewhere)
    db.commit()
    db.refresh(listing)

    assert elsewhere.status is ListingStatus.ended
    assert elsewhere.ended_at is not None
    assert listing.status is ListingStatus.active
    assert listing.paused_by_listing_id is None
    assert item.disposition.code == "listed"
    assert _claim_states(db, item) == {
        listing.id: ClaimState.active,
        elsewhere.id: ClaimState.released,
    }


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
    # What the sale path does before it settles the listing.
    _set_disposition(db, item, "sold")
    db.commit()

    offering_writes.end_offer(db, elsewhere, sold=True)
    db.commit()
    db.refresh(listing)

    assert listing.status is ListingStatus.ended
    assert listing.paused_by_listing_id is None
    # The sale path sets `sold`; ending the offer must not undo that.
    assert item.disposition.code == "sold"
    assert _claim_states(db, item) == {
        listing.id: ClaimState.released,
        elsewhere.id: ClaimState.released,
    }


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
    elsewhere = _offer_on(db, item, ebay)
    db.commit()

    uses = sale_state.for_sale(db, [item.id])
    texts = {use.id: use.text for use in uses[item.id]}

    # The active offer and the store listing it set aside -- named, not
    # counted: a count of two is also what an unpaused store listing gives.
    assert set(texts) == {listing.id, elsewhere.id}
    assert texts[listing.id] == f"listing #{listing.id} at 189.00 (paused)"
    assert texts[elsewhere.id] == f"listing #{elsewhere.id} at 99.00 on Ebay-Warning"


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


def test_a_paused_store_listing_is_the_shops_but_not_sellable(
    db: Session, listing: Listing
) -> None:
    """`active_only=False` is "is this the shop's at all", and a paused one is."""
    ebay = _venue(db, "ebay-half-rule")
    _offer_on(db, listing.inventory_item, ebay)
    db.commit()
    db.refresh(listing)

    assert listing.status is ListingStatus.paused
    assert not offering_writes.sellable_in_shop(listing)
    assert offering_writes.sellable_in_shop(listing, active_only=False)

    def shop_ids(*, active_only: bool) -> set[int]:
        return set(
            db.scalars(
                select(Listing.id).where(
                    *offering_writes.shop_listing_filters(active_only=active_only)
                )
            ).all()
        )

    assert listing.id not in shop_ids(active_only=True)
    assert listing.id in shop_ids(active_only=False)


# --- what must not happen --------------------------------------------------


def test_a_withdrawn_store_listing_is_not_resurrected(
    db: Session, listing: Listing
) -> None:
    """Ending the offer must not undo an administrator's withdrawal.

    The catalogue API's retired `PATCH .../is_active` withdrew a listing
    without clearing `paused_by_listing_id`, so the pointer outlived the
    pause -- and old rows can still carry that shape. Resuming on the pointer
    alone would put a listing someone deliberately took down back in the
    public shop, claiming the item again with it.
    """
    item = listing.inventory_item
    ebay = _venue(db, "ebay-withdrawn")
    elsewhere = _offer_on(db, item, ebay)
    db.commit()

    # Exactly what the catalogue API's retired PATCH wrote for is_active=False.
    listing.status = ListingStatus.ended
    listing.ended_at = utcnow()
    db.commit()

    offering_writes.end_offer(db, elsewhere)
    db.commit()
    db.refresh(listing)

    assert listing.status is ListingStatus.ended
    assert listing.paused_by_listing_id == elsewhere.id  # untouched, not resumed
    assert item.disposition.code == "held"


def test_a_sold_item_is_not_put_back_to_held(
    db: Session, make_item: ItemFactory
) -> None:
    """Only a listed item goes back to held; `sold` is not this code's to undo.

    A shop sale marks the item `sold` while its listing stays active at zero
    stock (`order_writes._after_stock_change`). Ending that listing afterwards
    -- which Task 4's end endpoint does -- must not report it as held again.
    """
    item = make_item()
    ebay = _venue(db, "ebay-sold-item")
    made = _offer_on(db, item, ebay)
    _set_disposition(db, item, "sold")
    db.commit()

    offering_writes.end_offer(db, made)
    db.commit()

    assert item.disposition.code == "sold"


def test_an_item_released_from_this_listing_earlier_is_left_alone(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """A released claim is history: ending the listing must not re-decide it.

    The item below was let go by this listing and is listed somewhere else
    now. Counting released claims among the items an ending touches would
    file it as held while it is still on offer.
    """
    gone = make_item()
    _set_disposition(db, gone, "listed")
    _claim(db, gone, listing, ClaimState.released)
    db.commit()

    offering_writes.end_offer(db, listing)
    db.commit()

    assert gone.disposition.code == "listed"


def test_ending_a_listing_releases_the_items_it_claimed(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """An item held only by a claim is released and filed with the rest."""
    member = make_item()
    _set_disposition(db, member, "listed")
    _claim(db, member, listing, ClaimState.active)
    db.commit()

    offering_writes.end_offer(db, listing)
    db.commit()

    assert _claim_states(db, member) == {listing.id: ClaimState.released}
    assert member.disposition.code == "held"


def test_a_released_claim_is_not_revived_by_pausing_a_listing(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """Pausing moves what a listing still holds, not what it has let go."""
    gone = make_item()
    _claim(db, gone, listing, ClaimState.released)
    db.commit()

    ebay = _venue(db, "ebay-released")
    _offer_on(db, listing.inventory_item, ebay)
    db.commit()
    db.refresh(listing)

    assert listing.status is ListingStatus.paused
    assert _claim_states(db, gone) == {listing.id: ClaimState.released}


def test_a_paused_store_listing_is_still_seen_when_offering_again(
    db: Session, listing: Listing
) -> None:
    """A paused store listing means the same thing everywhere it is asked about.

    A store listing left paused -- by an offer that ended outside this module,
    say -- is invisible to a refusal path that only looks at active rows, and
    its claim would then block the item from ever going back to `held`.
    """
    item = listing.inventory_item
    listing.status = ListingStatus.paused
    _claim(db, item, listing, ClaimState.paused)
    db.commit()

    ebay = _venue(db, "ebay-still-paused")
    elsewhere = _offer_on(db, item, ebay)
    db.commit()
    db.refresh(listing)

    assert listing.paused_by_listing_id == elsewhere.id
    assert _claim_states(db, item) == {
        listing.id: ClaimState.paused,
        elsewhere.id: ClaimState.active,
    }


def test_the_item_lock_re_reads_the_row_it_locked(
    db: Session, make_item: ItemFactory
) -> None:
    """The decision is made on the locked row, not on what was read before it.

    The update below is the shape of a concurrent write: the row changes while
    this session still holds the values it loaded earlier. A lock that does not
    re-read would offer an item that is no longer receivable.
    """
    item = make_item()
    ordered = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()
    db.execute(
        update(InventoryItem)
        .where(InventoryItem.id == item.id)
        .values(status_id=ordered)
        .execution_options(synchronize_session=False)
    )
    venue = _venue(db, "ebay-stale")

    with pytest.raises(offering_writes.OfferRefused, match="not received"):
        _offer_on(db, item, venue)
