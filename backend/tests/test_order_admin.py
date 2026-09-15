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

from tests.test_orders import foreign_order, place


def _set(
    client: TestClient, headers: dict[str, str], order_id: int, status: str
) -> Response:
    return client.patch(
        f"/api/orders/{order_id}", json={"status": status}, headers=headers
    )


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
    assert order["items"][0]["title"] == listing.inventory_item.source_title


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
