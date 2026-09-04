"""Purchase flow: totals, inventory, visibility and status transitions."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Coin, Order, OrderStatus, User
from app.security import hash_password


def place(client: TestClient, headers: dict[str, str], coin_id: int, quantity: int):
    return client.post(
        "/api/orders",
        json={"items": [{"coin_id": coin_id, "quantity": quantity}]},
        headers=headers,
    )


# --------------------------------------------------------------------------
# Placing orders
# --------------------------------------------------------------------------


def test_order_requires_authentication(client: TestClient, coin: Coin) -> None:
    response = client.post(
        "/api/orders", json={"items": [{"coin_id": coin.id, "quantity": 1}]}
    )
    assert response.status_code == 401


def test_place_order_computes_exact_total(
    client: TestClient, coin: Coin, customer_headers: dict[str, str]
) -> None:
    response = place(client, customer_headers, coin.id, 2)
    assert response.status_code == 201
    body = response.json()
    # 2 x 189.00 -- exact decimal arithmetic, no float rounding
    assert body["total_amount"] == "378.00"
    assert body["status"] == "pending"
    assert len(body["items"]) == 1


def test_order_across_multiple_items_sums_correctly(
    client: TestClient, make_coin, customer_headers: dict[str, str]
) -> None:
    a = make_coin(n=1, sku="TEST-A", price=Decimal("19.99"), quantity=10)
    b = make_coin(n=2, sku="TEST-B", price=Decimal("0.01"), quantity=10)
    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"coin_id": a.id, "quantity": 3},
                {"coin_id": b.id, "quantity": 7},
            ]
        },
        headers=customer_headers,
    )
    assert response.status_code == 201
    # 3 x 19.99 = 59.97, plus 7 x 0.01 = 0.07 -> 60.04
    assert response.json()["total_amount"] == "60.04"


def test_order_decrements_inventory(
    client: TestClient, coin: Coin, customer_headers: dict[str, str], db: Session
) -> None:
    assert coin.quantity == 5
    place(client, customer_headers, coin.id, 2)
    db.refresh(coin)
    assert coin.quantity == 3


def test_cannot_order_more_than_available(
    client: TestClient, coin: Coin, customer_headers: dict[str, str], db: Session
) -> None:
    response = place(client, customer_headers, coin.id, 6)
    assert response.status_code == 409
    db.refresh(coin)
    assert coin.quantity == 5, "a rejected order must not change stock"


def test_cannot_order_sold_out_item(
    client: TestClient, make_coin, customer_headers: dict[str, str]
) -> None:
    sold_out = make_coin(n=1, sku="TEST-SOLDOUT", quantity=0)
    assert place(client, customer_headers, sold_out.id, 1).status_code == 409


def test_cannot_order_inactive_item(
    client: TestClient, make_coin, customer_headers: dict[str, str]
) -> None:
    withdrawn = make_coin(n=1, sku="TEST-WITHDRAWN", is_active=False, quantity=5)
    response = place(client, customer_headers, withdrawn.id, 1)
    assert response.status_code == 409
    assert "not currently for sale" in response.json()["detail"]


def test_unknown_coin_rejected(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    assert place(client, customer_headers, 999999, 1).status_code == 404


def test_duplicate_line_rejected(
    client: TestClient, coin: Coin, customer_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"coin_id": coin.id, "quantity": 1},
                {"coin_id": coin.id, "quantity": 1},
            ]
        },
        headers=customer_headers,
    )
    assert response.status_code == 422


def test_empty_order_rejected(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    response = client.post("/api/orders", json={"items": []}, headers=customer_headers)
    assert response.status_code == 422


def test_zero_quantity_rejected(
    client: TestClient, coin: Coin, customer_headers: dict[str, str]
) -> None:
    assert place(client, customer_headers, coin.id, 0).status_code == 422


def test_partially_unavailable_order_is_all_or_nothing(
    client: TestClient, make_coin, customer_headers: dict[str, str], db: Session
) -> None:
    """If one line fails, no line may be committed."""
    available = make_coin(n=1, sku="TEST-OK", quantity=5)
    scarce = make_coin(n=2, sku="TEST-SCARCE", quantity=1)

    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"coin_id": available.id, "quantity": 1},
                {"coin_id": scarce.id, "quantity": 99},
            ]
        },
        headers=customer_headers,
    )
    assert response.status_code == 409

    db.refresh(available)
    db.refresh(scarce)
    assert available.quantity == 5, "stock rolled back for the succeeding line"
    assert scarce.quantity == 1


def test_unit_price_is_frozen_at_purchase_time(
    client: TestClient,
    coin: Coin,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """Editing the catalogue must not rewrite order history."""
    order = place(client, customer_headers, coin.id, 1).json()
    assert order["items"][0]["unit_price"] == "189.00"

    client.patch(
        f"/api/coins/{coin.id}", json={"price": "999.00"}, headers=admin_headers
    )

    after = client.get("/api/orders", headers=customer_headers).json()
    assert after[0]["items"][0]["unit_price"] == "189.00"
    assert after[0]["total_amount"] == "189.00"


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------


def test_customer_sees_only_own_orders(
    client: TestClient, coin: Coin, customer_headers: dict[str, str], db: Session
) -> None:
    place(client, customer_headers, coin.id, 1)

    other = User(
        email="other@example.com",
        hashed_password=hash_password("otherpassword"),
    )
    db.add(other)
    db.commit()
    db.refresh(other)
    db.add(Order(user_id=other.id, total_amount=Decimal("1.00")))
    db.commit()

    mine = client.get("/api/orders", headers=customer_headers).json()
    assert len(mine) == 1


def test_admin_sees_all_orders(
    client: TestClient,
    coin: Coin,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    place(client, customer_headers, coin.id, 1)
    assert len(client.get("/api/orders", headers=admin_headers).json()) == 1


def test_other_customers_order_returns_404_not_403(
    client: TestClient, coin: Coin, customer_headers: dict[str, str], db: Session
) -> None:
    """A 403 would confirm the order exists; 404 leaks nothing."""
    other = User(
        email="other2@example.com", hashed_password=hash_password("otherpassword")
    )
    db.add(other)
    db.commit()
    db.refresh(other)
    foreign = Order(user_id=other.id, total_amount=Decimal("1.00"))
    db.add(foreign)
    db.commit()
    db.refresh(foreign)

    response = client.get(f"/api/orders/{foreign.id}", headers=customer_headers)
    assert response.status_code == 404


# --------------------------------------------------------------------------
# Status transitions
# --------------------------------------------------------------------------


def test_customer_cannot_change_status(
    client: TestClient, coin: Coin, customer_headers: dict[str, str]
) -> None:
    order = place(client, customer_headers, coin.id, 1).json()
    response = client.patch(
        f"/api/orders/{order['id']}", json={"status": "paid"}, headers=customer_headers
    )
    assert response.status_code == 403


def test_admin_can_advance_status(
    client: TestClient,
    coin: Coin,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = place(client, customer_headers, coin.id, 1).json()
    response = client.patch(
        f"/api/orders/{order['id']}", json={"status": "paid"}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "paid"


def test_invalid_status_rejected(
    client: TestClient,
    coin: Coin,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = place(client, customer_headers, coin.id, 1).json()
    response = client.patch(
        f"/api/orders/{order['id']}",
        json={"status": "refunded"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_cancelling_restores_stock(
    client: TestClient,
    coin: Coin,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = place(client, customer_headers, coin.id, 2).json()
    db.refresh(coin)
    assert coin.quantity == 3

    client.patch(
        f"/api/orders/{order['id']}",
        json={"status": "cancelled"},
        headers=admin_headers,
    )
    db.refresh(coin)
    assert coin.quantity == 5


def test_cancelling_twice_does_not_double_restore(
    client: TestClient,
    coin: Coin,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Re-sending 'cancelled' must be idempotent for stock."""
    order = place(client, customer_headers, coin.id, 2).json()
    for _ in range(2):
        client.patch(
            f"/api/orders/{order['id']}",
            json={"status": "cancelled"},
            headers=admin_headers,
        )
    db.refresh(coin)
    assert coin.quantity == 5, "stock restored once, not twice"
