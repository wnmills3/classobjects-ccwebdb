"""Offer claims and the offering writer (selling design, phase 2)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from decimal import Decimal

import pytest
from app import lot_writes, offering_writes, order_writes, sale_state
from app.models import (
    ClaimState,
    Disposition,
    InventoryItem,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    OfferClaim,
    SalesLot,
    SalesLotStatus,
    SalesOrder,
    SalesVenue,
    SalesVenueKind,
    User,
    utcnow,
)
from app.offering_writes import OfferRefused
from app.sales_venues import store_venue_id
from sqlalchemy import event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import item_of

ItemFactory = Callable[..., InventoryItem]
ListingFactory = Callable[..., Listing]


def _claim(
    db: Session, item: InventoryItem, listing: Listing, state: ClaimState
) -> OfferClaim:
    """Build a claim directly, for a shape no current write path produces.

    A real `offer()` call also moves the claimed item's disposition to
    `listed` (`offering_writes.py:669-678`). `make_item` (`conftest.py`)
    defaults a fresh item's disposition to `held`, so a `HELD_BY` claim
    built here without the same side effect would disagree with
    `check_disposition_invariant` (`tests/conftest.py`) the moment the
    autouse fixture checks it -- not because of a bug in `offering_writes`,
    but because this helper skipped a step the real writer always takes.
    Mirroring that one side effect keeps the constructed state plausible
    without touching the invariant itself.
    """
    if state in offering_writes.HELD_BY:
        _set_disposition(db, item, "listed")
    claim = OfferClaim(inventory_item_id=item.id, listing_id=listing.id, state=state)
    db.add(claim)
    db.flush()
    return claim


def test_one_item_can_have_only_one_active_claim(
    db: Session, listing: Listing, make_listing: ListingFactory
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
    db: Session, listing: Listing, make_listing: ListingFactory
) -> None:
    item_id = listing.inventory_item_id
    second = make_listing(inventory_item_id=item_id)
    # Paused, to match the claim below: the suite-wide claim invariant
    # (conftest.py) expects a listing's own claim to agree with its status.
    listing.status = ListingStatus.paused
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
    db: Session, listing: Listing, make_listing: ListingFactory
) -> None:
    item_id = listing.inventory_item_id
    second = make_listing(inventory_item_id=item_id)
    # Ended, to match the claim below: the suite-wide claim invariant
    # (conftest.py) expects a listing's own claim to agree with its status.
    listing.status = ListingStatus.ended
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
    db: Session, listing: Listing, make_listing: ListingFactory
) -> None:
    """Settlement needs to know which offer to resume a store listing for."""
    elsewhere = make_listing(inventory_item_id=listing.inventory_item_id)
    listing.status = ListingStatus.paused
    listing.paused_by_listing_id = elsewhere.id
    db.commit()
    db.refresh(listing)

    assert listing.paused_by is not None
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


@contextmanager
def _captured_locks(db: Session, table: str) -> Iterator[list[dict[str, object]]]:
    """Record the bound parameters of each `FOR UPDATE` on one table.

    The only way, without a second connection, to measure *which rows a call
    asks to lock and in what order*. A real deadlock needs two transactions
    racing, and nothing in this suite may open a second session against the
    shared test database, so this measures the acquisition rather than the
    collision it would cause.
    """
    seen: list[dict[str, object]] = []

    def _record(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        """Keep the parameters of each locking select against `table`."""
        if f"FROM {table}" in statement and "FOR UPDATE" in statement:
            seen.append(dict(parameters) if isinstance(parameters, dict) else {})

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", _record)
    try:
        yield seen
    finally:
        event.remove(bind, "before_cursor_execute", _record)


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
    """An order with one line on this listing, in the state the code names.

    Built through `order_writes.place_order`, not by hand: that is the one
    place a line's share is created, and a fixture that built its own share
    would state that invariant a second time -- silently out of step the
    moment phase 3 changes it to one share per lot member, dividing the
    line's amount rather than repeating it whole. Checkout's own guards
    apply (`venue=None`): the `listing` fixture is the store's, active, and
    holds 5, so taking 1 passes `sellable_in_shop`/`is_active` and does not
    cross the stock to zero -- the item's disposition stays `listed`, which
    matters because `_refuse_sold` has a disposition branch and an order
    branch, and these two callers are testing the order branch specifically.
    """
    customer = order_writes.customer_for_user(db, customer_user)
    return order_writes.place_order(
        db,
        customer,
        [
            order_writes.Line(
                listing_id=listing.id, quantity=1, unit_price=Decimal("99.00")
            )
        ],
        admin_user,
        status_code=status_code,
    )


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
    item = item_of(listing)
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
    item = item_of(listing)
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
    item = item_of(listing)
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
    item = item_of(listing)
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
    item = item_of(listing)
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
    # The listing has to be paused for resuming it to mean anything. Read into
    # a local first: asserting on the attribute would narrow it for the rest of
    # the test, and the checker cannot see `db.refresh` reload it below.
    status_before = listing.status
    assert status_before is ListingStatus.paused

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
    item = item_of(listing)
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
    item = item_of(listing)
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
    _offer_on(db, item_of(listing), ebay)
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

    No `claim_invariant_waiver` here even though `listing.status` is set to
    `ended` directly below, bypassing `offering_writes`, mid-test: the claim
    it leaves behind reads `paused` against an `ended` listing for exactly
    the middle of this test, but `end_offer` (below) now releases that stray
    claim itself once it decides nothing holds the item any more -- see the
    comment beside `stray_claims` in `offering_writes.end_offer`. So by the
    time the autouse invariant checks run, at teardown, nothing disagrees;
    a waiver would be stale the moment it was written.
    """
    item = item_of(listing)
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
    stale_claim = db.scalars(
        select(OfferClaim).where(OfferClaim.listing_id == listing.id)
    ).one()
    # The stray claim `listing.status = ended` (above) left disagreeing with
    # its own listing is released, not left `paused` forever -- the fix this
    # test's docstring names.
    assert stale_claim.state is ClaimState.released


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


@pytest.mark.claim_invariant_waiver(
    reason=(
        "attaches gone's claim to listing.id for an item other than "
        "listing's own -- the lot-member shape which offering_writes.offer "
        "now writes for a real lot, though not with this test's "
        "deliberately mismatched state -- specifically to prove a released "
        "claim from a different member is not revived by pausing the "
        "listing for a current one"
    )
)
def test_a_released_claim_is_not_revived_by_pausing_a_listing(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """Pausing moves what a listing still holds, not what it has let go."""
    gone = make_item()
    _claim(db, gone, listing, ClaimState.released)
    db.commit()

    ebay = _venue(db, "ebay-released")
    _offer_on(db, item_of(listing), ebay)
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
    item = item_of(listing)
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


# --------------------------------------------------------------------------
# "Holds this item" is two halves, and both readers must ask both
# --------------------------------------------------------------------------


def test_offers_holding_finds_a_listing_that_only_claims_the_item(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """A piece of a lot is offered by the lot's listing, not by one of its own.

    No write path creates this state yet -- `offer` takes a single item, and
    lot offers are phase 3 -- so the claim is constructed directly. It is
    still the state every other reader in this module is careful to handle
    (`_locked_offers`, `_still_offered`, `_affected_items`), and a reader
    that asks only `Listing.inventory_item_id` silently returns nothing for
    exactly the case the claim table exists for.
    """
    piece = make_item()
    _claim(db, piece, listing, ClaimState.active)
    db.flush()

    # The listing is written against a different item entirely.
    assert listing.inventory_item_id != piece.id

    found = offering_writes.offers_holding(db, [piece.id])
    assert [held.id for held in found] == [listing.id]


@pytest.mark.claim_invariant_waiver(
    reason=(
        "attaches piece's claim to listing.id for an item other than "
        "listing's own -- the lot-member shape phase 3 introduced -- "
        "specifically to isolate offers_holding's HELD_BY filter from its "
        "ON_OFFER filter (test_offers_holding_ignores_an_ended_listing is "
        "the other half); listing.status is left at its real active value "
        "on purpose, so this claim's released state disagrees with it"
    )
)
def test_offers_holding_ignores_a_released_claim(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """A released claim holds nothing, so the listing does not hold the item.

    The counterpart to the test above: without it, `offers_holding` could
    return every listing that had ever claimed the item and still pass.
    """
    piece = make_item()
    _claim(db, piece, listing, ClaimState.released)
    db.flush()

    assert offering_writes.offers_holding(db, [piece.id]) == []


@pytest.mark.claim_invariant_waiver(
    reason=(
        "attaches piece's claim to ended.id for an item other than "
        "ended's own -- the lot-member shape phase 3 introduced -- "
        "specifically to isolate offers_holding's ON_OFFER filter from its "
        "HELD_BY filter (test_offers_holding_ignores_a_released_claim is "
        "the other half); the claim is left active on purpose, so it "
        "disagrees with the listing's real ended status"
    )
)
def test_offers_holding_ignores_an_ended_listing(
    db: Session, make_listing: ListingFactory, make_item: ItemFactory
) -> None:
    """Only live offers. An ended listing holds nothing whatever its claims say."""
    ended = make_listing(is_active=False)
    piece = make_item()
    _claim(db, piece, ended, ClaimState.active)
    db.flush()

    assert offering_writes.offers_holding(db, [piece.id]) == []


# --- lots ------------------------------------------------------------------


def test_offering_a_lot_claims_every_member(
    db: Session, lot_of_three: SalesLot, ebay_venue: SalesVenue
) -> None:
    """One claim per member: the one-offer guarantee is per item, not per listing."""
    listing = offering_writes.offer(
        db,
        lot=lot_of_three,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("900.00"),
        title="Three Morgan Dollars",
        description="",
        external_id=None,
    )
    claims = db.scalars(
        select(OfferClaim).where(OfferClaim.listing_id == listing.id)
    ).all()
    member_ids = {
        row.inventory_item_id for row in lot_writes.open_members(db, lot_of_three)
    }
    assert {claim.inventory_item_id for claim in claims} == member_ids
    assert all(claim.state is ClaimState.active for claim in claims)
    assert listing.inventory_item_id is None
    assert listing.quantity_available == 1
    assert lot_of_three.status is SalesLotStatus.offered


def test_offering_a_lot_pauses_each_member_s_store_listing(
    db: Session, make_item: ItemFactory, ebay_venue: SalesVenue
) -> None:
    """Shop to eBay is one step, for every coin in the group."""
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    items = [make_item(title=f"Stored {n}") for n in range(2)]
    store_listings = [_offer_on(db, item, store) for item in items]
    lot = lot_writes.create_lot(db, title="Two stored coins", description="")
    for item in items:
        lot_writes.add_member(db, lot, item)

    offering_writes.offer(
        db,
        lot=lot,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("200.00"),
        title="Two stored coins",
        description="",
        external_id=None,
    )

    for store_listing in store_listings:
        db.refresh(store_listing)
        assert store_listing.status is ListingStatus.paused
        assert store_listing.paused_by_listing_id is not None
    for item in items:
        assert _claim_states(db, item)[store_listings[items.index(item)].id] is (
            ClaimState.paused
        )


def test_every_listing_a_lot_offer_locks_is_taken_in_one_pass(
    db: Session, make_item: ItemFactory, ebay_venue: SalesVenue
) -> None:
    """N sorted statements are not a sorted acquisition, and that deadlocks.

    `_locked_offers` runs once per member, so before `_lock_offers` the
    listing locks were ordered *within* each call and unordered across the
    loop. A lot of two whose shop listings are #9 and #4 took #9 then #4,
    while a two-line `place_order` (`order_writes._lock_listings`) takes #4
    then #9: each transaction holds what the other waits for. The item lock
    does not save the pair, because `place_order` takes no item lock at all.

    The assertion is that the **first** listing lock covers every member, not
    just the first one -- which is exactly what one ascending statement means
    and what a per-member first lock cannot satisfy. It measures the
    acquisition, not the collision: a real deadlock needs two connections and
    this suite may not open one.

    The members' shop listings are built in reverse item order on purpose, so
    listing ids descend as item ids ascend. That is the arrangement in which
    an unsorted union actually inverts; built in order, a broken
    implementation would take them ascending by luck and the test would pass.
    """
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    items = [make_item(title=f"Locked {n}") for n in range(2)]
    for item in reversed(items):
        _offer_on(db, item, store)
    lot = lot_writes.create_lot(db, title="Two locked coins", description="")
    for item in items:
        lot_writes.add_member(db, lot, item)

    with _captured_locks(db, "listing") as locks:
        offering_writes.offer(
            db,
            lot=lot,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("300.00"),
            title="Two locked coins",
            description="",
            external_id=None,
        )

    assert locks, "no listing was locked at all"
    first = set(locks[0].values())
    assert {item.id for item in items} <= first


def test_offering_a_lot_twice_is_refused_by_name(
    db: Session,
    lot_of_three: SalesLot,
    ebay_venue: SalesVenue,
    whatnot_venue: SalesVenue,
) -> None:
    """The frozen-lot guard, which nothing else in the suite reaches.

    Without it the second offer does not sail through -- the members' own
    claims make `_locked_offers` fire `elsewhere` -- but it fails naming an
    *item* with an `OfferRefused`, losing both the lot in the message and the
    409-vs-422 mapping the `LotRefused`/`EmptyLot` split exists for. So the
    exception type and the message are what is asserted, not merely that
    something was refused.
    """
    offering_writes.offer(
        db,
        lot=lot_of_three,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("900.00"),
        title="Three Morgan Dollars",
        description="",
        external_id=None,
    )

    with pytest.raises(lot_writes.LotRefused, match="cannot be offered") as excinfo:
        offering_writes.offer(
            db,
            lot=lot_of_three,
            venue=whatnot_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("900.00"),
            title="Three Morgan Dollars",
            description="",
            external_id=None,
        )
    assert f"lot #{lot_of_three.id}" in str(excinfo.value)
    assert not isinstance(excinfo.value, OfferRefused)


def test_a_member_of_an_offered_lot_cannot_be_offered_on_its_own(
    db: Session, offered_lot_listing: Listing, whatnot_venue: SalesVenue
) -> None:
    """Once offered the group is frozen, and a coin leaving it changes what it is.

    Not the shop-to-eBay one-step case: that rule is about an item's own
    store listing, not about a group a buyer is being shown. The owner ends
    or dissolves the lot first.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member = db.get(InventoryItem, lot.members[0].inventory_item_id)
    assert member is not None

    with pytest.raises(OfferRefused) as excinfo:
        _offer_on(db, member, whatnot_venue)
    assert excinfo.value.item_code == member.item_code
    assert f"lot #{lot.id}" in excinfo.value.reason


def test_dissolving_the_lot_frees_a_member_to_be_offered_on_its_own(
    db: Session, offered_lot_listing: Listing, whatnot_venue: SalesVenue
) -> None:
    """The remedy the refusal above names has to actually work.

    Otherwise the message sends the owner somewhere that does not help, and
    the refusal is a dead end rather than one deliberate extra step.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member = db.get(InventoryItem, lot.members[0].inventory_item_id)
    assert member is not None
    offering_writes.end_offer(db, offered_lot_listing)

    listing = _offer_on(db, member, whatnot_venue)

    assert listing.status is ListingStatus.active
    assert _claim_states(db, member)[listing.id] is ClaimState.active


def test_offering_a_lot_in_the_shop_pauses_a_member_s_shop_listing(
    db: Session, make_item: ItemFactory
) -> None:
    """The one-line refusal is for a duplicate *item* listing, not for a lot.

    The spec's exception -- "offering an *item* on the store while it is
    already active on the store is simply refused" -- is about a second
    listing of the same thing. A lot is not a duplicate of its member's
    listing: it is the coin moving from being sold on its own to being sold
    as part of a group, which is the same one-step transition shop -> eBay
    already is. Without this case the `lot is None` half of that refusal is
    never exercised: `test_offering_a_lot_pauses_each_member_s_store_listing`
    offers on eBay, where `venue.is_own_store` is false and the refusal
    cannot fire whatever the lot half says.
    """
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    item = make_item(title="Already in the shop")
    alone = _offer_on(db, item, store)
    lot = lot_writes.create_lot(db, title="Grouped instead", description="")
    lot_writes.add_member(db, lot, item)

    grouped = offering_writes.offer(
        db,
        lot=lot,
        venue=store,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("150.00"),
        title="Grouped instead",
        description="",
        external_id=None,
    )

    db.refresh(alone)
    assert alone.status is ListingStatus.paused
    assert alone.paused_by_listing_id == grouped.id
    assert _claim_states(db, item) == {
        alone.id: ClaimState.paused,
        grouped.id: ClaimState.active,
    }
    assert lot.status is SalesLotStatus.offered


def test_dissolving_a_lot_resumes_a_member_s_shop_listing(
    db: Session, make_item: ItemFactory
) -> None:
    """The other exit from the pause above, and the reason it is a pause.

    An unsold grouping leaves the coin back where the owner had it -- on its
    own listing, at the price it had -- rather than withdrawn. A refusal at
    offer time would have made that transition two manual steps.
    """
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    item = make_item(title="Back on its own")
    alone = _offer_on(db, item, store)
    lot = lot_writes.create_lot(db, title="Grouped briefly", description="")
    lot_writes.add_member(db, lot, item)
    grouped = offering_writes.offer(
        db,
        lot=lot,
        venue=store,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("150.00"),
        title="Grouped briefly",
        description="",
        external_id=None,
    )

    offering_writes.end_offer(db, grouped)

    db.refresh(alone)
    db.refresh(item)
    assert alone.status is ListingStatus.active
    assert alone.paused_by_listing_id is None
    assert _claim_states(db, item) == {
        alone.id: ClaimState.active,
        grouped.id: ClaimState.released,
    }
    assert item.disposition.code == "listed"
    assert lot.status is SalesLotStatus.dissolved


def test_a_member_offered_elsewhere_refuses_the_whole_lot(
    db: Session,
    make_item: ItemFactory,
    ebay_venue: SalesVenue,
    whatnot_venue: SalesVenue,
) -> None:
    """Named refusal, and nothing written: all or nothing, as for a batch."""
    free, busy = make_item(title="Free"), make_item(title="Busy")
    elsewhere = _offer_on(db, busy, whatnot_venue)
    lot = lot_writes.create_lot(db, title="One busy member", description="")
    lot_writes.add_member(db, lot, free)
    lot_writes.add_member(db, lot, busy)

    with pytest.raises(OfferRefused) as excinfo:
        offering_writes.offer(
            db,
            lot=lot,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("50.00"),
            title="One busy member",
            description="",
            external_id=None,
        )
    assert excinfo.value.item_code == busy.item_code
    assert f"listing #{elsewhere.id}" in excinfo.value.reason
    assert "Whatnot" in excinfo.value.reason
    # Nothing written for the member that could have been offered.
    assert _claim_states(db, free) == {}
    assert lot.status is SalesLotStatus.assembling
    # And no listing either: a claim assertion alone would not notice a
    # `Listing` row flushed before the refusal, which is the shape an
    # all-or-nothing test exists to catch.
    assert db.scalars(select(Listing).where(Listing.sales_lot_id == lot.id)).all() == []


def test_offering_an_empty_lot_is_bad_input(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """The spec's *Errors* list says 422 for an empty lot, not 409."""
    empty = lot_writes.create_lot(db, title="Nothing in it", description="")
    with pytest.raises(lot_writes.EmptyLot):
        offering_writes.offer(
            db,
            lot=empty,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="Nothing in it",
            description="",
            external_id=None,
        )


def test_offering_neither_an_item_nor_a_lot_is_a_programming_error(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """`ValueError`, not `OfferRefused`: there is no item code to name.

    `OfferRefused.__init__` requires an `item_code` and `routers/offers.py`
    reads it to build `OfferRefusalOut`, so the shape is load-bearing at the
    HTTP boundary. A caller that passed neither has a bug; the request
    schema (`OfferIn`) makes it unreachable from outside.
    """
    with pytest.raises(ValueError, match="exactly one"):
        offering_writes.offer(
            db,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
        )


def test_offering_both_an_item_and_a_lot_is_a_programming_error(
    db: Session,
    received_item: InventoryItem,
    lot_of_three: SalesLot,
    ebay_venue: SalesVenue,
) -> None:
    """Same reason, the other way round."""
    with pytest.raises(ValueError, match="exactly one"):
        offering_writes.offer(
            db,
            item=received_item,
            lot=lot_of_three,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
        )


def test_offered_items_of_an_item_listing_is_that_one_item(
    db: Session, ebay_listing: Listing
) -> None:
    """The narrow question, answered in one place for both shapes of listing."""
    assert [item.id for item in offering_writes.offered_items(db, ebay_listing)] == [
        ebay_listing.inventory_item_id
    ]


def test_offered_items_of_a_lot_listing_is_its_members_in_id_order(
    db: Session, offered_lot_listing: Listing
) -> None:
    """Every member, in `open_members`' order -- the one order downstream shares.

    Narrower than `_affected_items` on purpose: this is what a *sale* divides
    between, and it must never reach the items of listings this one paused.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    expected = sorted(member.inventory_item_id for member in lot.members)
    assert [
        item.id for item in offering_writes.offered_items(db, offered_lot_listing)
    ] == expected


def test_offered_items_of_a_lot_listing_excludes_a_paused_member_s_own_listing(
    db: Session, make_item: ItemFactory, ebay_venue: SalesVenue
) -> None:
    """`_affected_items` carries the paused listing's item; this must not.

    They coincide for a lot member -- the paused store listing offers the
    same coin -- so the two are told apart with a *second*, unrelated item
    whose store listing this offer also pauses. `_affected_items` reaches it
    through `paused_by_listing_id` and `offered_items` must not, because
    moving a paused listing's item is an ending's job and never a sale's.
    """
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    member, bystander = make_item(title="Member"), make_item(title="Bystander")
    bystander_listing = _offer_on(db, bystander, store)
    lot = lot_writes.create_lot(db, title="One member", description="")
    lot_writes.add_member(db, lot, member)
    lot_listing = offering_writes.offer(
        db,
        lot=lot,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("75.00"),
        title="One member",
        description="",
        external_id=None,
    )
    # Not something `offer` would do -- it pauses only listings holding a
    # member -- so the pointer is set directly, which is the whole point:
    # `_affected_items` follows it and `offered_items` must not.
    bystander_listing.status = ListingStatus.paused
    bystander_listing.paused_by_listing_id = lot_listing.id
    offering_writes._move_claims(db, bystander_listing, ClaimState.paused)
    db.flush()

    assert [item.id for item in offering_writes.offered_items(db, lot_listing)] == [
        member.id
    ]
    assert offering_writes._affected_items(db, lot_listing) == sorted(
        [member.id, bystander.id]
    )


def test_ending_a_lot_listing_dissolves_the_lot(
    db: Session, offered_lot_listing: Listing
) -> None:
    """Unsold means the group is not a thing any more; the coins are free."""
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    offering_writes.end_offer(db, offered_lot_listing)
    db.refresh(lot)
    assert lot.status is SalesLotStatus.dissolved
    assert all(member.released_at is not None for member in lot.members)


def test_ending_a_lot_listing_does_not_trip_over_its_null_item(
    db: Session, offered_lot_listing: Listing
) -> None:
    """`_affected_items` used to put NULL in a set it then sorted.

    A lot listing's `inventory_item_id` is NULL, so the first of
    `_affected_items`' two queries yields `None`, and `sorted({None, 12, 13})`
    raises `TypeError: '<' not supported between instances of 'int' and
    'NoneType'` -- a crash, not a silent skip. `_lock_items` has the same
    shape. This test is the regression: it fails with that `TypeError`, not
    with an assertion, if the NULL filter is removed.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    expected = sorted(member.inventory_item_id for member in lot.members)
    assert offering_writes._affected_items(db, offered_lot_listing) == expected


def test_a_sold_lot_is_sold_not_dissolved(
    db: Session, offered_lot_listing: Listing
) -> None:
    """`sold` and `dissolved` are different histories and must stay apart."""
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    offering_writes.end_offer(db, offered_lot_listing, sold=True)
    db.refresh(lot)
    assert lot.status is SalesLotStatus.sold
    assert all(member.released_at is not None for member in lot.members)


def test_members_with_no_remaining_claim_go_held(
    db: Session, offered_lot_listing: Listing
) -> None:
    """Back in the drawer, not still marked as listed.

    Only `listed` moves back to `held` -- `end_offer`'s own rule, unchanged
    here: a member a sale had already moved past `listed` is not this
    function's to undo.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_ids = [member.inventory_item_id for member in lot.members]
    offering_writes.end_offer(db, offered_lot_listing)
    for item_id in member_ids:
        item = db.get(InventoryItem, item_id)
        assert item is not None
        assert item.disposition.code == "held"
