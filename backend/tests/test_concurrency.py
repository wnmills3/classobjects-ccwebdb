"""The oversell guarantee, exercised with genuinely concurrent transactions.

These tests deliberately do *not* go through ``TestClient``: starlette funnels
its requests through a single portal, so two "concurrent" client calls actually
run one after another and would pass even with the row lock removed.

Instead the order-creation handler is invoked directly from two threads, each
with its own real session and connection, synchronised on a barrier so their
transactions genuinely overlap. Removing ``.with_for_update()`` from
``create_order`` makes these tests fail, which is the point.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.models import Coin, Order, OrderItem, User, UserRole
from app.routers.orders import create_order
from app.schemas import OrderCreate, OrderLineIn
from app.security import hash_password


@pytest.fixture
def committed(engine: Engine):
    """Real, committing sessions. Cleans up the rows it creates."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    yield factory

    with factory() as cleanup:
        cleanup.query(OrderItem).delete()
        cleanup.query(Order).delete()
        cleanup.query(Coin).filter(Coin.sku.like("RACE-%")).delete(
            synchronize_session=False
        )
        cleanup.query(User).filter(User.email.like("race%@example.com")).delete(
            synchronize_session=False
        )
        cleanup.commit()


def _seed(factory, *, stock: int, buyers: int) -> tuple[int, list[int]]:
    with factory() as session:
        item = Coin(
            sku="RACE-ITEM",
            title="Contested Item",
            price=Decimal("100.00"),
            quantity=stock,
        )
        session.add(item)
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
        return item.id, [u.id for u in users]


def _race(factory, coin_id: int, user_ids: list[int]) -> list[int | str]:
    """Every buyer attempts one unit at the same instant. Returns outcomes."""
    barrier = threading.Barrier(len(user_ids))
    payload = OrderCreate(items=[OrderLineIn(coin_id=coin_id, quantity=1)])

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


def test_two_buyers_cannot_both_take_the_last_item(committed) -> None:
    coin_id, user_ids = _seed(committed, stock=1, buyers=2)

    outcomes = _race(committed, coin_id, user_ids)

    assert outcomes == [409, "ok"], f"expected one winner and one 409, got {outcomes}"
    with committed() as session:
        assert session.get(Coin, coin_id).quantity == 0


def test_stock_never_goes_negative_under_contention(committed) -> None:
    """Five buyers, two units: exactly two win and stock lands at zero."""
    coin_id, user_ids = _seed(committed, stock=2, buyers=5)

    outcomes = _race(committed, coin_id, user_ids)

    assert outcomes.count("ok") == 2, f"expected exactly 2 winners, got {outcomes}"
    assert outcomes.count(409) == 3

    with committed() as session:
        remaining = session.get(Coin, coin_id).quantity
    assert remaining == 0, f"stock must not go negative, got {remaining}"


def test_all_buyers_succeed_when_stock_is_sufficient(committed) -> None:
    coin_id, user_ids = _seed(committed, stock=5, buyers=3)

    outcomes = _race(committed, coin_id, user_ids)

    assert outcomes == ["ok", "ok", "ok"]
    with committed() as session:
        assert session.get(Coin, coin_id).quantity == 2
