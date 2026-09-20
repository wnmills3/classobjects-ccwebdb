"""The fee, share and platform-buyer schema.

These assert the *database* guarantees rather than the Python models: a
uniqueness rule that lives only in application code is one concurrent
request away from being untrue.
"""

from __future__ import annotations

import pytest
from app.models import Customer, SalesFeeKind, SalesVenue
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_fee_kinds_are_seeded(db: Session) -> None:
    """The six fee kinds the spec names are present and active."""
    codes = set(
        db.scalars(select(SalesFeeKind.code).where(SalesFeeKind.is_active)).all()
    )
    assert codes == {
        "commission",
        "processing",
        "listing",
        "shipping_label",
        "promotion",
        "other",
    }


def test_one_buyer_per_platform_username(db: Session, ebay_venue: SalesVenue) -> None:
    """The same username on the same platform cannot be stored twice."""
    db.add(
        Customer(
            display_name="coinfan88",
            sales_venue_id=ebay_venue.id,
            venue_username="coinfan88",
        )
    )
    db.flush()
    db.add(
        Customer(
            display_name="coinfan88 again",
            sales_venue_id=ebay_venue.id,
            venue_username="coinfan88",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_the_same_username_differently_cased_still_collides(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """The uniqueness rule is on the lowered username, not the raw column.

    A platform displays one account's name inconsistently ("CoinFan88" one
    order, "coinfan88" the next); this is the case the index exists for, and
    `test_one_buyer_per_platform_username` -- same case both times -- cannot
    tell an index on `venue_username` from one on `lower(venue_username)`.
    """
    db.add(
        Customer(
            display_name="coinfan88",
            sales_venue_id=ebay_venue.id,
            venue_username="coinfan88",
        )
    )
    db.flush()
    db.add(
        Customer(
            display_name="CoinFan88 again",
            sales_venue_id=ebay_venue.id,
            venue_username="CoinFan88",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_one_undisclosed_buyer_per_platform(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """A platform gets exactly one buyer with no username."""
    db.add(
        Customer(display_name="Undisclosed buyer (eBay)", sales_venue_id=ebay_venue.id)
    )
    db.flush()
    db.add(
        Customer(
            display_name="Undisclosed buyer (eBay) 2", sales_venue_id=ebay_venue.id
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_store_customers_are_unaffected(db: Session) -> None:
    """Any number of store customers have neither column set."""
    db.add_all([Customer(display_name="A"), Customer(display_name="B")])
    db.flush()  # must not raise


def test_fee_and_share_amounts_are_exact(db: Session) -> None:
    """Money columns are numeric(12,2), so a cent is a cent."""

    def scale(table: str, column: str) -> int | None:
        return db.scalar(
            text(
                "SELECT numeric_scale FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )

    assert scale("sales_order_fee", "amount") == 2
    assert scale("sales_order_item_share", "amount") == 2
    assert scale("sales_order_item_share", "fee_amount") == 2
