"""Recording a sale that happened on an outside platform."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app import offering_writes
from app.allocation import allocate
from app.models import (
    Listing,
    ListingStatus,
    SalesOrder,
    SalesOrderItemShare,
    SalesOrderStatus,
    SalesVenue,
    User,
)
from app.sales_writes import FeeLine, SaleRefused, record_sale
from sqlalchemy import select
from sqlalchemy.orm import Session


def _status_code(db: Session, order: SalesOrder) -> str:
    """An order's status code, read the way `order_writes` itself reads it.

    `SalesOrder` carries `sales_order_status_id`, not a `status` relationship
    -- there is nothing on the model spelled `order.status`.
    """
    row = db.get(SalesOrderStatus, order.sales_order_status_id)
    assert row is not None
    return row.code


def test_the_sale_ends_the_listing_and_releases_its_claim(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """A sold listing is over: ended, claims released, item sold."""
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


def test_fees_are_stored_as_given(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """The platform's own figures, not an estimate from the venue's rates."""
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
    assert sorted(fee.amount for fee in order.fees) == [
        Decimal("5.35"),
        Decimal("15.90"),
    ]


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
