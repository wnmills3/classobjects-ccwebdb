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


def test_both_money_tables_refuse_a_negative_amount(db: Session) -> None:
    """A share's `amount` has the same floor a fee's does.

    Asserted against the database rather than the model: a share is written by
    `order_writes._sync_shares` today, and the point of the constraint is the
    writer that does not exist yet. The two named constraints are checked
    together because the fee's floor was there from the start and the share's
    was not -- a test naming only one could pass while the other was missing.
    """
    names = set(
        db.scalars(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' "
                "AND conrelid::regclass::text IN "
                "('sales_order_fee', 'sales_order_item_share')"
            )
        ).all()
    )
    assert "ck_sales_order_fee_non_negative" in names
    assert "ck_sales_order_item_share_non_negative" in names

    # `match` names the constraint: both foreign keys on this table are
    # RESTRICT, so an unmatched `IntegrityError` would also be raised by the
    # invented ids below and this test would pass without the check existing.
    # Inside a savepoint (`begin_nested`), not bare: the INSERT is raw SQL, so
    # the failure aborts the transaction without deactivating the *session* the
    # way a failed ORM flush would -- and the autouse claim-invariant fixture
    # would then try to query an aborted transaction in teardown and report an
    # error on this test that has nothing to do with it. The savepoint rolls
    # back just the failed statement.
    with (
        pytest.raises(IntegrityError, match="ck_sales_order_item_share_non_negative"),
        db.begin_nested(),
    ):
        db.execute(
            text(
                "INSERT INTO sales_order_item_share "
                "(sales_order_item_id, inventory_item_id, amount, fee_amount) "
                "VALUES (1, 1, -0.01, 0)"
            )
        )
