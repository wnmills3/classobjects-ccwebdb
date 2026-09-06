"""The oversell guarantee, exercised with genuinely concurrent transactions.

These tests deliberately do *not* go through ``TestClient``: starlette funnels
its requests through a single portal, so two "concurrent" client calls actually
run one after another and would pass even with the row lock removed.

Instead the order-creation handler is invoked directly from two threads, each
with its own real session and connection, synchronised on a barrier so their
transactions genuinely overlap. Removing ``.with_for_update()`` from
``create_order`` makes these tests fail, which is the point.

Ported from the scaffold onto `listing` / `sales_order_item`. The contended
column moved from ``coins.quantity`` to ``listing.quantity_available``, and the
lock is taken on `listing` rather than on the item -- which is correct, because
availability is a property of the offer, not of the object.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Authenticity,
    Currency,
    Customer,
    Disposition,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    SalesOrder,
    SalesOrderItem,
    StorageForm,
    User,
    UserRole,
    ValuationBasis,
)
from app.routers.orders import create_order
from app.schemas import OrderCreate, OrderLineIn
from app.security import hash_password

RACE_TITLE = "RACE Contested Item"


@pytest.fixture
def committed(engine: Engine):
    """Real, committing sessions. Cleans up the rows it creates."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    yield factory

    with factory() as cleanup:
        cleanup.query(SalesOrderItem).delete()
        cleanup.query(SalesOrder).delete()
        cleanup.query(Customer).delete()
        cleanup.query(Listing).delete()
        cleanup.query(InventoryItem).filter(
            InventoryItem.title == RACE_TITLE
        ).delete(synchronize_session=False)
        cleanup.query(User).filter(User.email.like("race%@example.com")).delete(
            synchronize_session=False
        )
        cleanup.commit()


def _code_id(session: Session, model, code: str) -> int:
    return session.execute(select(model.id).where(model.code == code)).scalar_one()


def _seed(factory, *, stock: int, buyers: int) -> tuple[int, list[int]]:
    with factory() as session:
        item = InventoryItem(
            title=RACE_TITLE,
            item_kind_id=_code_id(session, ItemKind, "coin"),
            storage_form_id=_code_id(session, StorageForm, "single"),
            authenticity_id=_code_id(session, Authenticity, "unverified"),
            status_id=_code_id(session, ItemStatus, "received"),
            disposition_id=_code_id(session, Disposition, "listed"),
            valuation_basis_id=_code_id(session, ValuationBasis, "numismatic"),
        )
        session.add(item)
        session.flush()

        listing = Listing(
            inventory_item_id=item.id,
            price=Decimal("100.00"),
            currency_id=_code_id(session, Currency, "USD"),
            quantity_available=stock,
        )
        session.add(listing)

        users = [
            User(
                email=f"race{n}@example.com",
                hashed_password=hash_password("racepassword"),
                role=UserRole.customer,
            )
            for n in range(buyers)
        ]
        session.add_all(users)
        session.commit()
        return listing.id, [u.id for u in users]


def _race(factory, listing_id: int, user_ids: list[int]) -> list[int | str]:
    """Every buyer attempts one unit at the same instant. Returns outcomes."""
    barrier = threading.Barrier(len(user_ids))
    payload = OrderCreate(items=[OrderLineIn(listing_id=listing_id, quantity=1)])

    def attempt(user_id: int) -> int | str:
        session = factory()
        try:
            user = session.get(User, user_id)
            barrier.wait(timeout=10)
            create_order(payload, session, user)
            return "ok"
        except HTTPException as exc:
            session.rollback()
            return exc.status_code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=len(user_ids)) as pool:
        return sorted(pool.map(attempt, user_ids), key=str)


def _remaining(factory, listing_id: int) -> int:
    with factory() as session:
        return session.get(Listing, listing_id).quantity_available


def test_two_buyers_cannot_both_take_the_last_item(committed) -> None:
    listing_id, user_ids = _seed(committed, stock=1, buyers=2)

    outcomes = _race(committed, listing_id, user_ids)

    assert outcomes == [409, "ok"], f"expected one winner and one 409, got {outcomes}"
    assert _remaining(committed, listing_id) == 0


def test_stock_never_goes_negative_under_contention(committed) -> None:
    """Five buyers, two units: exactly two win and stock lands at zero."""
    listing_id, user_ids = _seed(committed, stock=2, buyers=5)

    outcomes = _race(committed, listing_id, user_ids)

    assert outcomes.count("ok") == 2, f"expected exactly 2 winners, got {outcomes}"
    assert outcomes.count(409) == 3

    remaining = _remaining(committed, listing_id)
    assert remaining == 0, f"stock must not go negative, got {remaining}"


def test_all_buyers_succeed_when_stock_is_sufficient(committed) -> None:
    listing_id, user_ids = _seed(committed, stock=5, buyers=3)

    outcomes = _race(committed, listing_id, user_ids)

    assert outcomes == ["ok", "ok", "ok"]
    assert _remaining(committed, listing_id) == 2


def test_each_winner_gets_exactly_one_order_line(committed) -> None:
    """The count that actually matters commercially: units sold must equal
    units gone from the catalogue."""
    listing_id, user_ids = _seed(committed, stock=3, buyers=6)

    outcomes = _race(committed, listing_id, user_ids)

    with committed() as session:
        sold = (
            session.query(SalesOrderItem)
            .filter(SalesOrderItem.listing_id == listing_id)
            .count()
        )
    assert sold == outcomes.count("ok") == 3
    assert _remaining(committed, listing_id) == 0
