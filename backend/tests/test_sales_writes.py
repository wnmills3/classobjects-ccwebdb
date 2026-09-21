"""Recording a sale that happened on an outside platform."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app import offering_writes, order_writes
from app.allocation import allocate
from app.models import (
    ClaimState,
    Customer,
    InventoryItem,
    Listing,
    ListingFormat,
    ListingStatus,
    OfferClaim,
    SalesOrder,
    SalesOrderFee,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesOrderStatus,
    SalesVenue,
    SalesVenueKind,
    User,
)
from app.models.base import utcnow
from app.sales_writes import FeeLine, SaleRefused, ShareMissing, record_sale
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session


def _status_code(db: Session, order: SalesOrder) -> str:
    """An order's status code, read the way `order_writes` itself reads it.

    `SalesOrder` carries `sales_order_status_id`, not a `status` relationship
    -- there is nothing on the model spelled `order.status`.
    """
    row = db.get(SalesOrderStatus, order.sales_order_status_id)
    assert row is not None
    return row.code


def _customer_count(db: Session, sales_venue_id: int) -> int:
    """How many customers exist for a venue, to prove a refusal wrote none.

    `venue_buyer`'s find-or-create is the first write in `record_sale`'s
    body; a refusal decided above it must leave this at zero.
    """
    return (
        db.scalar(
            select(func.count())
            .select_from(Customer)
            .where(Customer.sales_venue_id == sales_venue_id)
        )
        or 0
    )


def test_the_sale_ends_the_listing_and_releases_its_claim(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """A sold listing is over: ended, claims released, item sold."""
    ebay_listing_id = ebay_listing.id
    record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id="04-12345-67890",
        fees=[FeeLine("commission", Decimal("15.90"))],
        recorded_by=admin_user,
    )
    assert ebay_listing.status is ListingStatus.ended
    assert ebay_listing.inventory_item.disposition.code == "sold"

    claim = db.scalar(
        select(OfferClaim).where(OfferClaim.listing_id == ebay_listing_id)
    )
    assert claim is not None
    assert claim.state is ClaimState.released


def test_fees_are_stored_as_given(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """The platform's own figures, not an estimate from the venue's rates.

    Two fee lines, not one: the only way to catch `total_fees` accumulating
    wrong (for example the last fee winning instead of the sum) is a case
    where the sum differs from either individual amount. Read with a fresh
    `select()`, not `order.fees` or a line's `.shares` -- both are
    relationships this same session could have already cached empty before
    the row existed, the same shape of bug `_sync_shares` has upstream.
    """
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[
            FeeLine("commission", Decimal("15.90")),
            FeeLine("shipping_label", Decimal("5.35"), note="USPS Ground"),
        ],
        recorded_by=admin_user,
    )
    fee_rows = db.scalars(
        select(SalesOrderFee).where(SalesOrderFee.sales_order_id == order.id)
    ).all()
    assert sorted(fee.amount for fee in fee_rows) == [
        Decimal("5.35"),
        Decimal("15.90"),
    ]

    share = db.scalar(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == order.items[0].id
        )
    )
    assert share is not None
    assert share.fee_amount == Decimal("21.25")


def test_a_single_item_sale_still_gets_a_share(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """One row carrying the whole line: the permanent item-to-order link."""
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[FeeLine("commission", Decimal("15.90"))],
        recorded_by=admin_user,
    )
    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == order.items[0].id
        )
    ).all()
    assert len(shares) == 1
    assert shares[0].inventory_item_id == ebay_listing.inventory_item_id
    assert shares[0].amount == Decimal("120.00")
    assert shares[0].fee_amount == Decimal("15.90")


def test_the_buyer_is_recorded_on_the_platform(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """The order's customer is the eBay username, not a store login."""
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    assert order.customer.venue_username == "coinfan88"
    assert order.customer.sales_venue_id == ebay_venue.id


def test_an_auction_house_sale_is_delivered_not_paid(
    db: Session, heritage_listing: Listing, admin_user: User
) -> None:
    """The house held and shipped the coin; there is nothing left to do."""
    order = record_sale(
        db,
        heritage_listing,
        price=Decimal("500.00"),
        buyer_username=None,
        external_order_id=None,
        fees=[FeeLine("commission", Decimal("75.00"))],
        recorded_by=admin_user,
    )
    assert _status_code(db, order) == "delivered"


def test_a_marketplace_sale_is_paid_not_delivered(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """The platform collected the money; the owner still has to ship it."""
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    assert _status_code(db, order) == "paid"


def test_a_negative_fee_is_refused(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """A refund is not a negative fee; it is a separate thing not built yet."""
    with pytest.raises(SaleRefused, match="negative"):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("120.00"),
            buyer_username="coinfan88",
            external_order_id=None,
            fees=[FeeLine("commission", Decimal("-1.00"))],
            recorded_by=admin_user,
        )


def test_a_negative_price_is_refused(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """The API's schema refuses this first, but `record_sale` guards it too.

    The library-level guard exists for phase-4 auction settlement, a future
    caller of `record_sale` that will not pass through the API's schema at
    all -- so this check has to hold on its own, not merely agree with a
    guard that happens to sit in front of it today.
    """
    with pytest.raises(SaleRefused, match="negative"):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("-1.00"),
            buyer_username="coinfan88",
            external_order_id=None,
            fees=[],
            recorded_by=admin_user,
        )


def test_a_sub_cent_fee_is_refused(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """A fraction of a cent cannot reconcile against a `Numeric(12, 2)` row.

    PostgreSQL rounds 15.905 half away from zero, to 15.91; `allocate`
    quantizes the same figure half to even, to 15.90. Refusing sub-cent
    precision up front is what keeps the stored fee and its shares from
    ever disagreeing by that cent.
    """
    with pytest.raises(SaleRefused, match="cent"):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("120.00"),
            buyer_username="coinfan88",
            external_order_id=None,
            fees=[FeeLine("commission", Decimal("15.905"))],
            recorded_by=admin_user,
        )
    assert ebay_listing.status is ListingStatus.active


def test_a_sub_cent_price_is_refused(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """The same disagreement `price` will hit the day `allocate` divides it.

    `_sync_shares` assigns a single item's share `amount` by straight
    assignment today, so a sub-cent `price` happens to round the same way
    in PostgreSQL and in `allocate` right now -- but a lot listing's line
    (phase 3) is divided among its members *through* `allocate`, which
    reintroduces exactly the fee disagreement for `price` too. Refusing it
    now costs a line; finding it later costs a debugging session on money
    that will not reconcile. Same value shape as the fee test (120.005:
    PostgreSQL rounds half away from zero to 120.01, `allocate` quantizes
    half to even to 120.00), and a message distinguishable from the fee
    one so a caller knows which field is wrong.
    """
    with pytest.raises(SaleRefused, match="Price"):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("120.005"),
            buyer_username="coinfan88",
            external_order_id=None,
            fees=[],
            recorded_by=admin_user,
        )
    assert ebay_listing.status is ListingStatus.active


def test_an_unmapped_venue_kind_is_refused(
    db: Session, received_item: InventoryItem, admin_user: User
) -> None:
    """A venue kind with no default status must be named, not guessed as paid.

    Nothing in the seeded vocabulary lacks a default today, so this proves
    the guard with a kind manufactured for the test -- the day a real one
    (a consignment shop, a dealer-to-dealer venue) is added, this is exactly
    the failure it must hit instead of silently recording it as `paid`.
    """
    kind = SalesVenueKind(code="dealer_network", label="Dealer network", sort_order=99)
    db.add(kind)
    db.flush()
    venue = SalesVenue(
        code="some-dealer",
        name="Some Dealer",
        sales_venue_kind_id=kind.id,
    )
    db.add(venue)
    db.flush()
    listing = offering_writes.offer(
        db,
        item=received_item,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("50.00"),
        title="",
        description="",
        external_id=None,
    )

    with pytest.raises(SaleRefused, match="dealer_network"):
        record_sale(
            db,
            listing,
            price=Decimal("50.00"),
            buyer_username=None,
            external_order_id=None,
            fees=[],
            recorded_by=admin_user,
        )
    assert listing.status is ListingStatus.active
    # Proves the refusal happened *before* `venue_buyer`, not merely that it
    # happened: this would also pass if the mapping lookup moved back below
    # `venue_buyer`, since that write only touches the store's own customer
    # rows -- catching that requires counting rows on this new venue.
    assert _customer_count(db, venue.id) == 0


def test_an_unknown_explicit_status_code_is_refused_before_writing(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """A bad explicit `status_code` must fail before `place_order` ever runs.

    `pytest.raises(HTTPException)` alone proves too little: `place_order`
    raises the identical exception from its own `require_code` call, after
    `venue_buyer` has already flushed a `Customer` row -- so a version of
    `record_sale` with no pre-write validation of its own would pass this
    test just as well. Counting customers on the venue is what actually
    distinguishes "refused before anything is written" from "refused
    eventually, by someone else, after a write already happened."
    """
    with pytest.raises(HTTPException):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("120.00"),
            buyer_username="coinfan88",
            external_order_id=None,
            fees=[],
            recorded_by=admin_user,
            status_code="not-a-real-status",
        )
    assert ebay_listing.status is ListingStatus.active
    assert _customer_count(db, ebay_venue.id) == 0


def test_an_already_ended_listing_cannot_be_sold(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """Recording the same sale twice would sell one coin twice."""
    offering_writes.end_offer(db, ebay_listing)
    with pytest.raises(SaleRefused, match="not on offer"):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("120.00"),
            buyer_username="coinfan88",
            external_order_id=None,
            fees=[],
            recorded_by=admin_user,
        )


def test_a_second_sale_of_the_same_listing_is_refused_as_not_on_offer(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """The loser of a race is told the offer is over, not that stock ran out.

    The winner's transaction is simulated rather than threaded, and the shape
    is the one that matters: the row says `ended` while the caller's instance
    still says `active`. `synchronize_session=False` is what keeps the
    instance stale -- with the default, the UPDATE would refresh the identity
    map and there would be no stale read left to catch. That is exactly the
    caller `routers.offers` hands `record_sale`: a plain `db.get` taken before
    any lock was held.

    `quantity_available` is raised to 3 on purpose. At 1, `place_order`'s
    stock check refuses the second sale anyway -- for the wrong reason, with a
    message about how many remain -- so a test at 1 would pass with or without
    the locked re-read. Above 1 nothing else stands in the way, which is the
    state `app/seed.py`'s demo listings (5 and 20 against one item) are
    actually in.

    The claim is released alongside the listing because that is what the
    winner's `end_offer` would have done, and the suite's claim invariant
    checks the pair after every test -- an ended listing still holding an
    active claim would fail here for a reason this test is not about.
    """
    listing_id = ebay_listing.id
    ebay_listing.quantity_available = 3
    db.flush()
    db.execute(
        update(Listing)
        .where(Listing.id == listing_id)
        .values(status=ListingStatus.ended, ended_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(OfferClaim)
        .where(OfferClaim.listing_id == listing_id)
        .values(state=ClaimState.released)
        .execution_options(synchronize_session=False)
    )
    # The stale read the old code decided on: still `active` in Python.
    assert ebay_listing.status is ListingStatus.active

    with pytest.raises(SaleRefused, match="not on offer"):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("131.50"),
            buyer_username="secondbidder",
            external_order_id="04-99999-11111",
            fees=[FeeLine("commission", Decimal("17.40"))],
            recorded_by=admin_user,
        )
    # Refused before the first write, so the race's loser left nothing behind.
    assert _customer_count(db, ebay_venue.id) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(SalesOrder)
            .where(SalesOrder.sales_venue_id == ebay_venue.id)
        )
        == 0
    )


def test_a_refusal_names_the_platform_as_well_as_the_listing(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """The spec's *Errors* section asks for the listing **and** its platform.

    An owner with the same coin offered in two places cannot act on "Listing
    41 is not on offer" alone.
    """
    offering_writes.end_offer(db, ebay_listing)
    with pytest.raises(SaleRefused, match=r"Listing \d+ on eBay is not on offer"):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("142.25"),
            buyer_username="latecomer",
            external_order_id=None,
            fees=[],
            recorded_by=admin_user,
        )


def test_a_sold_item_s_paused_store_listing_ends_rather_than_resuming(
    db: Session, stored_then_ebay: tuple[Listing, Listing], admin_user: User
) -> None:
    """The coin is gone; putting it back in the shop would sell it twice."""
    store_listing, ebay_listing_ = stored_then_ebay
    assert store_listing.status is ListingStatus.paused

    record_sale(
        db,
        ebay_listing_,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )

    ended = db.scalar(select(Listing.status).where(Listing.id == store_listing.id))
    assert ended is ListingStatus.ended


def test_an_outside_sale_writes_its_order_once(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """`external_order_id` must not bump `version` with a second UPDATE.

    Set at construction inside `place_order`, the same way `total_amount`
    is -- a later assignment on an already-inserted row is exactly the bug
    that regressed the shares task in the other direction (a value assigned
    after the flush that inserts the row, rather than before it).
    """
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id="04-12345-67890",
        fees=[],
        recorded_by=admin_user,
    )
    assert order.external_order_id == "04-12345-67890"
    assert order.version == 1


def test_shares_of_an_indivisible_fee_still_sum_to_it(
    three_item_costs: list[Decimal],
) -> None:
    """$100.00 three ways is 33.33, 33.33, 33.34 -- never 99.99.

    `record_sale` cannot yet exercise this itself: a listing names exactly
    one item until lot listings (phase 3) exist, so today `_weights` always
    hands `allocate` a single-item list and there is nothing to divide. This
    proves the division `record_sale`'s fee split delegates to -- the one
    that will matter the day a lot sells -- actually holds the invariant.
    """
    amounts = allocate(Decimal("100.00"), three_item_costs)
    assert sum(amounts) == Decimal("100.00")
    assert amounts == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]


# --- selling a lot -----------------------------------------------------------------


def test_selling_a_lot_divides_the_price_among_its_members(
    db: Session, offered_lot_listing: Listing, admin_user: User
) -> None:
    """One line, one share per member, summing to the line exactly."""
    order = record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == order.items[0].id
        )
    ).all()
    assert len(shares) == 3
    assert sum(share.amount for share in shares) == Decimal("1000.00")


def test_shares_are_weighted_by_cost_basis_by_default(
    db: Session, offered_lot_listing: Listing, admin_user: User
) -> None:
    """A $500 coin and a $200 coin do not each take a third of the price.

    The fixture's members cost 500, 300 and 200 with `tax_rate=0`, so
    `total_cost` equals `item_cost` exactly and 1,000.00 divides as
    500 / 300 / 200. An equal split would be 333.34 / 333.33 / 333.33, so
    this assertion fails if the weighting is dropped -- which is the mutation
    that proves it: divide the line equally and confirm it goes red.
    """
    order = record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    by_cost = {
        share.inventory_item_id: share.amount
        for share in db.scalars(
            select(SalesOrderItemShare).where(
                SalesOrderItemShare.sales_order_item_id == order.items[0].id
            )
        )
    }
    costs = {
        item.id: item.total_cost
        for item in db.scalars(
            select(InventoryItem).where(InventoryItem.id.in_(by_cost))
        )
    }
    assert {costs[item_id]: amount for item_id, amount in by_cost.items()} == {
        Decimal("500.00"): Decimal("500.00"),
        Decimal("300.00"): Decimal("300.00"),
        Decimal("200.00"): Decimal("200.00"),
    }


def test_a_lot_s_members_all_become_sold(
    db: Session, offered_lot_listing: Listing, admin_user: User
) -> None:
    """`_after_stock_change` returned early on a null item and skipped them.

    Verified rather than assumed: `db.get(InventoryItem, None)` does *not*
    raise in SQLAlchemy 2.0.52 -- it runs `SELECT ... WHERE id = NULL`,
    returns `None`, and the function returned silently. So a lot's members
    would stay `listed` forever with no error anywhere.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_ids = [member.inventory_item_id for member in lot.members]
    record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    for item_id in member_ids:
        item = db.get(InventoryItem, item_id)
        assert item is not None
        assert item.disposition.code == "sold"


def test_a_missing_share_is_an_internal_error_not_a_refusal(
    db: Session,
    offered_lot_listing: Listing,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carried finding (c): a bare `KeyError` was an unhandled 500 with no name.

    A share missing for one of a listing's items is an invariant violation
    inside this codebase, not a conflict a caller can retry past -- so it is
    `ShareMissing`, unmapped in `routers/offers.py` and therefore still a 500,
    but one whose message names the item and the listing. Deliberately *not*
    `SaleRefused`, which maps to 409 and would tell the caller to try again.

    The scaffolding removes one share between `place_order` and the lookup,
    which is the only way to reach the branch: nothing in production writes
    a line with a member missing.
    """
    original = order_writes._sync_shares

    def _drop_one(
        db_: Session,
        line: SalesOrderItem,
        listing: Listing,
        amount: Decimal,
        *,
        new_line: bool = False,
    ) -> None:
        """Write the shares the real way, then delete one to make the gap."""
        original(db_, line, listing, amount, new_line=new_line)
        db_.flush()
        victim = db_.scalars(
            select(SalesOrderItemShare)
            .where(SalesOrderItemShare.sales_order_item_id == line.id)
            .order_by(SalesOrderItemShare.inventory_item_id)
            .limit(1)
        ).one()
        db_.delete(victim)
        db_.flush()

    monkeypatch.setattr(order_writes, "_sync_shares", _drop_one)
    with pytest.raises(ShareMissing) as excinfo:
        record_sale(
            db,
            offered_lot_listing,
            price=Decimal("1000.00"),
            buyer_username="coinfan88",
            external_order_id="EB-1",
            fees=[FeeLine("commission", Decimal("10.00"))],
            recorded_by=admin_user,
        )
    assert f"listing {offered_lot_listing.id}" in str(excinfo.value)
    assert "CC-" in str(excinfo.value)
