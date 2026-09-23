"""Orders as the owner console works with them.

Two things the console's Orders page needs that the shop never did: to say who
placed an order and what is in it, and to be safe to drive from a dropdown
that offers every status.
"""

from __future__ import annotations

from app.models import Listing
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from tests.conftest import item_of
from tests.test_orders import foreign_order, place


def _set(
    client: TestClient, headers: dict[str, str], order_id: int, status: str
) -> Response:
    return client.patch(
        f"/api/orders/{order_id}", json={"status": status}, headers=headers
    )


def test_an_ordinary_shop_order_s_listing_has_not_ended(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    """The negative half: a coin bought with stock left keeps its listing live.

    Without this, a regression that always answered `listing_ended: true`
    would grey out Cancel on every shop order and pass the suite.
    """
    placed = place(client, customer_headers, listing.id, 1)
    assert placed.status_code == 201, placed.text
    assert placed.json()["items"][0]["listing_ended"] is False


def test_cancelling_a_shop_order_that_bought_a_lot_is_refused(
    client: TestClient,
    db: Session,
    store_lot_listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """Returning the stock would strand the coins, so the transition is refused.

    A lot is sold as one group, so buying it ends its listing (the lot has to
    end `sold` with its members released, not stay `offered` for ever).
    `return_stock` would then add the unit back to an **ended** listing, and
    `_after_stock_change` moves items off `sold` only while the listing is
    active -- leaving phantom stock on a listing nobody can see and three
    coins `offering_writes._refuse_sold` will never let anyone offer again.
    That is the same state an outside-platform order is refused for; the
    refusal is the remedy, because the console has none.
    """
    listing_id = store_lot_listing.id
    lot = store_lot_listing.sales_lot
    assert lot is not None
    placed = place(client, customer_headers, listing_id, 1)
    assert placed.status_code == 201, placed.text
    order_id = placed.json()["id"]
    # What the console reads to grey Cancel out rather than offer it.
    assert placed.json()["items"][0]["listing_ended"] is True

    refused = _set(client, admin_headers, order_id, "cancelled")

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "cannot be cancelled" in detail
    assert f"#{listing_id}" in detail
    # Nothing moved: no phantom stock, and the order still stands.
    db.expire_all()
    still = db.get(Listing, listing_id)
    assert still is not None
    assert still.quantity_available == 0
    assert still.status.value == "ended"
    current = client.get(f"/api/orders/{order_id}", headers=admin_headers).json()
    assert current["status"] == "pending"


def test_a_shipped_lot_order_can_still_be_cancelled_as_a_refund(
    client: TestClient,
    db: Session,
    store_lot_listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """The refusal must match the danger, and a shipped order has none.

    Cancelling returns stock only when the order has not shipped
    (`SHIPPED_STATUSES`), so cancelling a shipped one moves nothing that
    could be stranded -- it is how a refund is recorded once the coins have
    left. Refusing it bought no safety and closed a real workflow, which is
    what the first version of this refusal did by asking about the listing
    whatever the order's status.
    """
    listing_id = store_lot_listing.id
    placed = place(client, customer_headers, listing_id, 1)
    assert placed.status_code == 201, placed.text
    order_id = placed.json()["id"]
    assert _set(client, admin_headers, order_id, "shipped").status_code == 200

    cancelled = _set(client, admin_headers, order_id, "cancelled")

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    # And no stock came back: a shipped order returns none, which is exactly
    # why this was safe to allow.
    db.expire_all()
    still = db.get(Listing, listing_id)
    assert still is not None
    assert still.quantity_available == 0


def test_an_order_names_its_customer_and_what_was_bought(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    place(client, customer_headers, listing.id, 2)

    order = client.get("/api/orders", headers=admin_headers).json()[0]

    assert order["customer_name"] == "Test Customer"
    assert order["customer_email"] == "customer@example.com"
    assert order["items"][0]["title"] == item_of(listing).source_title


def test_a_customer_still_sees_only_their_own_orders_with_the_new_fields(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    db: Session,
) -> None:
    """The added names must not open a window onto anyone else's order."""
    place(client, customer_headers, listing.id, 1)
    foreign_order(db, "somebody.else@example.com")

    mine = client.get("/api/orders", headers=customer_headers).json()

    assert len(mine) == 1
    assert {o["customer_email"] for o in mine} == {"customer@example.com"}


def test_mine_is_only_the_callers_own_orders_even_for_an_administrator(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """The shop's "Your orders" asks for mine; for an admin it listed everyone's."""
    place(client, customer_headers, listing.id, 1)
    place(client, admin_headers, listing.id, 1)

    everything = client.get("/api/orders", headers=admin_headers).json()
    admins_own = client.get("/api/orders?mine=true", headers=admin_headers).json()
    customers_own = client.get("/api/orders?mine=true", headers=customer_headers).json()

    assert len(everything) == 2
    assert [o["customer_email"] for o in admins_own] == ["admin@example.com"]
    assert [o["customer_email"] for o in customers_own] == ["customer@example.com"]


def test_a_cancelled_order_cannot_be_revived_after_its_stock_returned(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Cancelling returned the stock; moving the order on again would sell it twice.

    5 on hand, 2 ordered leaves 3; cancelling puts back 2 for 5. Setting the
    order to paid afterwards used to succeed with the 5 still listed -- an
    order for 2 standing on stock already offered to the next buyer.
    """
    order = place(client, customer_headers, listing.id, 2).json()
    assert _set(client, admin_headers, order["id"], "cancelled").status_code == 200

    for status in ("pending", "paid", "shipped"):
        response = _set(client, admin_headers, order["id"], status)
        assert response.status_code == 409, status

    db.refresh(listing)
    assert listing.quantity_available == 5
    current = client.get(f"/api/orders/{order['id']}", headers=admin_headers).json()
    assert current["status"] == "cancelled"
