"""Database guarantees on item codes, money floors and the pause pointer.

Asserted against the database rather than the models: the point of each is a
writer that goes around the application. A refused row is written in raw SQL
inside a savepoint, so the failure rolls back that statement alone and the
suite's teardown invariants still read a live transaction; each `match` names
the constraint, because an invented foreign key would raise an
`IntegrityError` of its own.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.models import InventoryItem, Listing
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.builders import ItemFactory


def test_an_item_code_keeps_every_digit(db: Session) -> None:
    """At least six digits, and never fewer than the number has."""
    codes = db.execute(
        text(
            "SELECT item_code_format(42), item_code_format(999999), "
            "item_code_format(1000000), item_code_format(123456789)"
        )
    ).one()
    assert tuple(codes) == ("CC-000042", "CC-999999", "CC-1000000", "CC-123456789")


@pytest.fixture
def sequence_near_a_million(engine: Engine) -> Iterator[None]:
    """`item_code_seq` set so its next value is 999999, and restored after.

    A sequence is not rolled back with the test's transaction, so it is set
    and restored on a connection of its own, exactly as it stood.
    """
    with engine.connect() as conn:
        last_value, is_called = conn.execute(
            text("SELECT last_value, is_called FROM item_code_seq")
        ).one()
        conn.execute(text("SELECT setval('item_code_seq', 999998)"))
        conn.commit()
    try:
        yield
    finally:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT setval('item_code_seq', :v, :c)"),
                {"v": last_value, "c": is_called},
            )
            conn.commit()


@pytest.mark.usefixtures("sequence_near_a_million")
def test_the_millionth_item_code_is_not_cut_to_six_digits(
    make_item: ItemFactory,
) -> None:
    """The default issues CC-999999, then CC-1000000 -- not CC-100000 again."""
    first = make_item()
    second = make_item()
    assert (first.item_code, second.item_code) == ("CC-999999", "CC-1000000")


def _refused(db: Session, constraint: str, statement: str) -> None:
    """`statement` fails on `constraint`, and only that statement rolls back."""
    with pytest.raises(IntegrityError, match=constraint), db.begin_nested():
        db.execute(text(statement))


def test_a_share_fee_is_never_negative(db: Session) -> None:
    """A share's part of the fees has the floor the fees themselves have."""
    _refused(
        db,
        "ck_sales_order_item_share_fee_non_negative",
        "INSERT INTO sales_order_item_share "
        "(sales_order_item_id, inventory_item_id, amount, fee_amount) "
        "VALUES (1, 1, 0, -0.01)",
    )


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("reserve", "ck_auction_lot_reserve_non_negative"),
        ("hammer_price", "ck_auction_lot_hammer_price_non_negative"),
    ],
)
def test_an_auction_lot_amount_is_never_negative(
    db: Session, column: str, constraint: str
) -> None:
    """A reserve and a hammer price are empty or at least zero."""
    _refused(
        db,
        constraint,
        f"INSERT INTO auction_lot (auction_id, listing_id, lot_number, {column}) "
        "VALUES (1, 1, '1', -0.01)",
    )


def test_a_numismatic_value_is_never_negative(
    db: Session, make_item: ItemFactory
) -> None:
    """Empty, zero or more: a collector value below nothing is a typing slip."""
    item: InventoryItem = make_item()
    db.execute(
        text("UPDATE inventory_item SET numismatic_value = 0 WHERE id = :i"),
        {"i": item.id},
    )
    _refused(
        db,
        "ck_inventory_item_numismatic_value_non_negative",
        f"UPDATE inventory_item SET numismatic_value = -0.01 WHERE id = {item.id}",
    )


def test_only_a_paused_listing_names_what_paused_it(
    db: Session, stored_then_ebay: tuple[Listing, Listing]
) -> None:
    """A pointer on an active listing is refused; a paused one keeps its own.

    `stored_then_ebay` pauses a store listing through `offering_writes`, so
    the accepted half is the state the application really writes.
    """
    paused, by = stored_then_ebay
    assert paused.paused_by_listing_id == by.id
    _refused(
        db,
        "ck_listing_paused_by_only_when_paused",
        f"UPDATE listing SET paused_by_listing_id = {by.id} WHERE id = {by.id}",
    )
