"""Purchase flow: totals, inventory, visibility and status transitions."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app import offering_writes
from app.models import (
    Listing,
    ListingStatus,
    SalesOrderItemShare,
    SalesVenue,
    User,
)
from app.order_writes import MAX_PENDING_ORDERS
from app.sales_venues import store_venue_id
from app.sales_writes import record_sale
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from tests.builders import foreign_order, post_order
from tests.conftest import item_of

# --------------------------------------------------------------------------
# Placing orders
# --------------------------------------------------------------------------


def test_order_requires_authentication(client: TestClient, listing: Listing) -> None:
    response = client.post(
        "/api/orders", json={"items": [{"listing_id": listing.id, "quantity": 1}]}
    )
    assert response.status_code == 401


def test_place_order_computes_exact_total(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    response = post_order(client, customer_headers, listing.id, 2)
    assert response.status_code == 201
    body = response.json()
    # 2 x 189.00 -- exact decimal arithmetic, no float rounding
    assert body["total_amount"] == "378.00"
    assert body["status"] == "pending"
    assert len(body["items"]) == 1


def test_order_across_multiple_items_sums_correctly(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
) -> None:
    a = make_listing(price=Decimal("19.99"), quantity_available=10)
    b = make_listing(price=Decimal("0.01"), quantity_available=10)
    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"listing_id": a.id, "quantity": 3},
                {"listing_id": b.id, "quantity": 7},
            ]
        },
        headers=customer_headers,
    )
    assert response.status_code == 201
    # 3 x 19.99 = 59.97, plus 7 x 0.01 = 0.07 -> 60.04
    assert response.json()["total_amount"] == "60.04"


def test_a_checkout_line_carries_a_share(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    """One share for one item: the permanent order-to-item link.

    Without this, `sale_state` would find store orders through shares and
    outside orders not at all -- two query shapes and one of them wrong.
    """
    response = post_order(client, customer_headers, listing.id, 1)
    assert response.status_code == 201
    order_item_id = response.json()["items"][0]["id"]

    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == order_item_id
        )
    ).all()
    assert [share.inventory_item_id for share in shares] == [listing.inventory_item_id]
    assert shares[0].amount == listing.price
    assert shares[0].fee_amount == Decimal("0.00")


def test_a_two_line_order_attributes_each_share_to_its_own_line(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    db: Session,
) -> None:
    """Two lines, two shares -- each naming its own item and its own money.

    A single-line order cannot catch a transposition: swapped amounts, or
    both shares pointing at the same item, would still pass a one-row test.
    Two distinct listings with two distinct prices make either mistake show.
    """
    first = make_listing(price=Decimal("50.00"), quantity_available=5)
    second = make_listing(price=Decimal("75.00"), quantity_available=5)
    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"listing_id": first.id, "quantity": 2},
                {"listing_id": second.id, "quantity": 3},
            ]
        },
        headers=customer_headers,
    )
    assert response.status_code == 201
    item_id_by_listing = {
        item["listing_id"]: item["id"] for item in response.json()["items"]
    }

    for listing, quantity in ((first, 2), (second, 3)):
        shares = db.scalars(
            select(SalesOrderItemShare).where(
                SalesOrderItemShare.sales_order_item_id
                == item_id_by_listing[listing.id]
            )
        ).all()
        assert [share.inventory_item_id for share in shares] == [
            listing.inventory_item_id
        ], f"listing {listing.id}"
        assert shares[0].amount == listing.price * quantity, f"listing {listing.id}"


def test_order_decrements_availability(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    assert listing.quantity_available == 5
    post_order(client, customer_headers, listing.id, 2)
    db.refresh(listing)
    assert listing.quantity_available == 3


def test_selling_the_last_unit_marks_the_item_sold(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    db: Session,
) -> None:
    """Disposition is the sales axis, independent of how the item came in."""
    only_one = make_listing(quantity_available=1)
    post_order(client, customer_headers, only_one.id, 1)

    db.expire_all()
    refreshed = db.get(Listing, only_one.id)
    # Expired above, not deleted: selling the last unit must leave the listing
    # in place, so a None here is itself a failure worth naming.
    assert refreshed is not None
    assert refreshed.quantity_available == 0
    assert item_of(refreshed).disposition.code == "sold"
    # How it was acquired is untouched by selling it.
    assert item_of(refreshed).status.code == "received"


def test_cannot_order_more_than_available(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    response = post_order(client, customer_headers, listing.id, 6)
    assert response.status_code == 409
    db.refresh(listing)
    assert listing.quantity_available == 5, "a rejected order must not change stock"


def test_cannot_order_sold_out_item(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
) -> None:
    sold_out = make_listing(quantity_available=0)
    assert post_order(client, customer_headers, sold_out.id, 1).status_code == 409


def test_cannot_order_withdrawn_listing(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
) -> None:
    withdrawn = make_listing(is_active=False, quantity_available=5)
    response = post_order(client, customer_headers, withdrawn.id, 1)
    assert response.status_code == 409
    assert "not currently for sale" in response.json()["detail"]


def test_unknown_listing_rejected(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    assert post_order(client, customer_headers, 999999, 1).status_code == 422


def test_duplicate_line_rejected(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"listing_id": listing.id, "quantity": 1},
                {"listing_id": listing.id, "quantity": 1},
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
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    assert post_order(client, customer_headers, listing.id, 0).status_code == 422


def test_partially_unavailable_order_is_all_or_nothing(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    db: Session,
) -> None:
    """If one line fails, no line may be committed."""
    available = make_listing(quantity_available=5)
    scarce = make_listing(quantity_available=1)

    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"listing_id": available.id, "quantity": 1},
                {"listing_id": scarce.id, "quantity": 99},
            ]
        },
        headers=customer_headers,
    )
    assert response.status_code == 409

    db.refresh(available)
    db.refresh(scarce)
    assert available.quantity_available == 5, "stock rolled back for the ok line"
    assert scarce.quantity_available == 1


def test_unit_price_is_frozen_at_purchase_time(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Editing the catalog must not rewrite order history."""
    order = post_order(client, customer_headers, listing.id, 1).json()
    assert order["items"][0]["unit_price"] == "189.00"

    # The sale moved the listing's version; the price change is sent at the
    # current one and must land, or the assertions below hold whatever an
    # order line reads its price from.
    db.refresh(listing)
    repriced = client.patch(
        f"/api/listings/{listing.id}",
        json={"price": "999.00", "version": listing.version},
        headers=admin_headers,
    )
    assert repriced.status_code == 200, repriced.text
    assert repriced.json()["price"] == "999.00"

    after = client.get("/api/orders", headers=customer_headers).json()
    assert after[0]["items"][0]["unit_price"] == "189.00"
    assert after[0]["total_amount"] == "189.00"


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------


def test_customer_sees_only_own_orders(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    post_order(client, customer_headers, listing.id, 1)
    foreign_order(db, "other@example.com")

    mine = client.get("/api/orders", headers=customer_headers).json()
    assert len(mine) == 1


def test_admin_sees_all_orders(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    post_order(client, customer_headers, listing.id, 1)
    foreign_order(db, "other3@example.com")
    assert len(client.get("/api/orders", headers=admin_headers).json()) == 2


def test_notes_and_placed_by_email_are_admin_only(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """Notes are an internal remark and `placed_by` is a staff address.

    Both are written and read in the console only -- a shopper's own view of
    an order an admin has annotated must show neither, even though the
    fields stay in the response shape for everyone.
    """
    order = post_order(client, customer_headers, listing.id, 1).json()
    assert order["placed_by_email"] is None
    revised = client.put(
        f"/api/orders/{order['id']}",
        json={
            "version": order["version"],
            "customer_id": order["customer_id"],
            "items": [
                {"listing_id": listing.id, "quantity": 1, "unit_price": "189.00"}
            ],
            "notes": "phone order",
        },
        headers=admin_headers,
    ).json()
    assert revised["notes"] == "phone order"
    assert revised["placed_by_email"] == "customer@example.com"

    mine = client.get(
        "/api/orders", params={"mine": "true"}, headers=customer_headers
    ).json()
    assert mine[0]["notes"] is None
    assert mine[0]["placed_by_email"] is None

    detail = client.get(f"/api/orders/{order['id']}", headers=customer_headers).json()
    assert detail["notes"] is None
    assert detail["placed_by_email"] is None

    admin_list = client.get("/api/orders", headers=admin_headers).json()
    admin_order = next(o for o in admin_list if o["id"] == order["id"])
    assert admin_order["notes"] == "phone order"
    assert admin_order["placed_by_email"] == "customer@example.com"


def test_other_customers_order_returns_404_not_403(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    """A 403 would confirm the order exists; 404 leaks nothing."""
    foreign = foreign_order(db, "other2@example.com")
    response = client.get(f"/api/orders/{foreign.id}", headers=customer_headers)
    assert response.status_code == 404


def test_an_order_says_which_platform_it_was_sold_on(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    ebay_listing: Listing,
    admin_user: User,
) -> None:
    """The Sales page cannot tell a shop sale from an outside one without it.

    Its status dropdown offers `cancelled` behind a confirmation promising
    the stock goes back on sale -- which `routers/orders.py` then refuses
    with a 409. A confusing prompt, not a broken action, and the platform is
    what lets the page give the reason itself instead.
    """
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id="EB-2",
        fees=[],
        recorded_by=admin_user,
    )
    db.commit()
    body = client.get(f"/api/orders/{order.id}", headers=admin_headers).json()
    assert body["sales_venue_code"] == "ebay"
    assert body["sales_venue_name"] == "eBay"


# --------------------------------------------------------------------------
# Status transitions
# --------------------------------------------------------------------------


def _set_status(
    client: TestClient, headers: dict[str, str], order_id: int, status: str
) -> Response:
    """Move an order to `status`, from the version it holds this moment."""
    version = client.get(f"/api/orders/{order_id}", headers=headers).json()["version"]
    return client.patch(
        f"/api/orders/{order_id}",
        json={"status": status, "version": version},
        headers=headers,
    )


def test_customer_cannot_change_status(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    order = post_order(client, customer_headers, listing.id, 1).json()
    response = _set_status(client, customer_headers, order["id"], "paid")
    assert response.status_code == 403


def test_changing_status_requires_signing_in(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    order = post_order(client, customer_headers, listing.id, 1).json()

    response = client.patch(
        f"/api/orders/{order['id']}",
        json={"status": "cancelled", "version": order["version"]},
    )

    assert response.status_code == 401
    current = client.get(f"/api/orders/{order['id']}", headers=customer_headers).json()
    assert current["status"] == "pending"


def test_admin_can_advance_status(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = post_order(client, customer_headers, listing.id, 1).json()
    response = _set_status(client, admin_headers, order["id"], "paid")
    assert response.status_code == 200
    assert response.json()["status"] == "paid"


def test_invalid_status_rejected(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = post_order(client, customer_headers, listing.id, 1).json()
    response = _set_status(client, admin_headers, order["id"], "not-a-real-status")
    assert response.status_code == 422
    assert "not-a-real-status" in response.json()["detail"]


def test_a_status_change_without_a_version_is_refused(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """A status is chosen from a page read earlier, and must say which one."""
    order = post_order(client, customer_headers, listing.id, 1).json()

    response = client.patch(
        f"/api/orders/{order['id']}", json={"status": "paid"}, headers=admin_headers
    )

    assert response.status_code == 422, response.text
    current = client.get(f"/api/orders/{order['id']}", headers=admin_headers).json()
    assert current["status"] == "pending"
    assert current["version"] == order["version"]


def test_a_status_chosen_from_a_stale_page_is_refused(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """A cancellation picked before someone else shipped the order is a 409.

    Taken at face value it would record a refund nobody made, on an order
    whose cancelled status can never be moved again -- and the confirmation
    the page showed promised stock back that a shipped order does not return.
    """
    order = post_order(client, customer_headers, listing.id, 2).json()
    shipped = client.patch(
        f"/api/orders/{order['id']}",
        json={"status": "shipped", "version": order["version"]},
        headers=admin_headers,
    )
    assert shipped.status_code == 200, shipped.text

    stale = client.patch(
        f"/api/orders/{order['id']}",
        json={"status": "cancelled", "version": order["version"]},
        headers=admin_headers,
    )

    assert stale.status_code == 409, stale.text
    assert "reload" in stale.json()["detail"].lower()
    current = client.get(f"/api/orders/{order['id']}", headers=admin_headers).json()
    assert current["status"] == "shipped"
    assert current["version"] == shipped.json()["version"]
    db.refresh(listing)
    assert listing.quantity_available == 3


def test_cancelling_restores_stock(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = post_order(client, customer_headers, listing.id, 2).json()
    db.refresh(listing)
    assert listing.quantity_available == 3

    cancelled = _set_status(client, admin_headers, order["id"], "cancelled")
    assert cancelled.status_code == 200, cancelled.text
    db.refresh(listing)
    assert listing.quantity_available == 5


def test_cancelling_with_no_store_platform_is_a_500_naming_the_fix(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """The live `routers/orders.py` case of the missing-reference-data handler.

    `update_order_status` -> `_no_stock_to_return` -> `store_venue_id` is a
    **live** path: any admin cancelling an unshipped store order runs it,
    with no code change needed to reach it, unlike `app.auctions.consign`
    which needs an auction house auction first. `app.main`'s handler for
    `errors.ReferenceDataMissing` -- the one that serves `consign` --
    answers it with a 500 whose body names the fix.

    Simulated by flipping the seeded store platform's `is_own_store` rather
    than deleting the row: `sales_venue.id` is referenced by the listing and
    the order this test itself builds, so a delete would fail on its own
    foreign keys before the scenario this test wants ever arose. Flipping the
    one column `store_venue_id` actually asks about reproduces "no web store
    platform" without disturbing anything that references the row.
    """
    order = post_order(client, customer_headers, listing.id, 1).json()
    db.execute(
        update(SalesVenue)
        .where(SalesVenue.id == store_venue_id(db))
        .values(is_own_store=False)
    )
    db.commit()

    response = _set_status(client, admin_headers, order["id"], "cancelled")
    assert response.status_code == 500, response.text
    assert "alembic upgrade head" in response.json()["detail"]


def test_cancelling_twice_does_not_double_restore(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Re-sending 'cancelled' must be idempotent for stock."""
    order = post_order(client, customer_headers, listing.id, 2).json()
    for _ in range(2):
        resent = _set_status(client, admin_headers, order["id"], "cancelled")
        assert resent.status_code == 200, resent.text
    db.refresh(listing)
    assert listing.quantity_available == 5, "stock restored once, not twice"


def test_cancelling_restores_stock_for_every_line(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """A two-line order's cancellation must restock every line, not just one."""
    first = make_listing(title="First", quantity_available=5)
    second = make_listing(title="Second", quantity_available=5)
    order = client.post(
        "/api/orders",
        json={
            "items": [
                {"listing_id": first.id, "quantity": 2},
                {"listing_id": second.id, "quantity": 3},
            ]
        },
        headers=customer_headers,
    ).json()
    db.refresh(first)
    db.refresh(second)
    assert (first.quantity_available, second.quantity_available) == (3, 2)

    response = _set_status(client, admin_headers, order["id"], "cancelled")
    assert response.status_code == 200, response.text

    db.refresh(first)
    db.refresh(second)
    assert (first.quantity_available, second.quantity_available) == (5, 5)


def test_cancelling_after_shipping_does_not_return_stock(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Cancelling after shipping does not return stock.

    Stock that has already been posted is gone; returning it to the
    catalog would oversell the next buyer.
    """
    order = post_order(client, customer_headers, listing.id, 2).json()
    _walk(client, admin_headers, order["id"], "paid", "shipped", "cancelled")
    db.refresh(listing)
    assert listing.quantity_available == 3


def _walk(
    client: TestClient, headers: dict[str, str], order_id: int, *states: str
) -> None:
    """Move an order through `states`, each of which must be accepted."""
    for state in states:
        response = _set_status(client, headers, order_id, state)
        assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("path", "available"),
    [
        # Refunded before it shipped: the coins never left, so they go back.
        (("refunded",), 5),
        (("paid", "refunded"), 5),
        # Refunded after: they left, and nothing comes back.
        (("shipped", "refunded"), 3),
        (("paid", "packed", "refunded"), 3),
    ],
)
def test_a_refund_returns_stock_only_before_shipping(
    path: tuple[str, ...],
    available: int,
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """A refund moves stock exactly as a cancellation does.

    Before shipping, a refunded order that kept its stock held the coins for
    ever: nothing could release them, and they could never be sold again.
    """
    order = post_order(client, customer_headers, listing.id, 2).json()
    _walk(client, admin_headers, order["id"], *path)
    db.refresh(listing)
    assert listing.quantity_available == available


@pytest.mark.parametrize(
    ("path", "available"),
    [(("refunded",), 5), (("shipped", "refunded"), 3)],
)
def test_cancelling_a_refunded_order_moves_no_more_stock(
    path: tuple[str, ...],
    available: int,
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """The refund already settled the stock; cancelling after it moves none.

    Returned once already, or gone with the parcel: a second return would
    offer the next buyer coins that are not there.
    """
    order = post_order(client, customer_headers, listing.id, 2).json()
    _walk(client, admin_headers, order["id"], *path, "cancelled")
    db.refresh(listing)
    assert listing.quantity_available == available


def test_an_order_refunded_before_shipping_cannot_be_moved_on(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Its stock is back on sale, so shipping it would sell the coins twice."""
    order = post_order(client, customer_headers, listing.id, 2).json()
    _walk(client, admin_headers, order["id"], "refunded")

    refused = _set_status(client, admin_headers, order["id"], "shipped")

    assert refused.status_code == 409, refused.text
    assert "refunded before it shipped" in refused.json()["detail"]
    db.refresh(listing)
    assert listing.quantity_available == 5
    # Re-sending the refund stays harmless.
    _walk(client, admin_headers, order["id"], "refunded")
    db.refresh(listing)
    assert listing.quantity_available == 5


@pytest.mark.parametrize(
    "path",
    [
        # Walked back to an unshipped status, then ended.
        ("shipped", "pending", "cancelled"),
        ("paid", "packed", "paid", "cancelled"),
        ("delivered", "pending", "refunded"),
        # A refund after shipping, taken back, then cancelled.
        ("shipped", "refunded", "paid", "cancelled"),
    ],
)
def test_an_order_that_ever_shipped_returns_no_stock_however_it_is_walked_back(
    path: tuple[str, ...],
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Whether the coins left is a fact about the order's past, not its status.

    A status set back to `pending` or `paid` by mistake does not bring the
    parcel home. Deciding from the status of the moment alone would put the
    stock back on sale at the next cancellation, for coins already posted.
    """
    order = post_order(client, customer_headers, listing.id, 2).json()
    _walk(client, admin_headers, order["id"], *path)
    db.refresh(listing)
    assert listing.quantity_available == 3


def test_an_order_refunded_after_shipping_can_still_be_moved_on(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """No stock moved, so a refund recorded by mistake can be taken back."""
    order = post_order(client, customer_headers, listing.id, 2).json()
    _walk(client, admin_headers, order["id"], "shipped", "refunded", "delivered")
    db.refresh(listing)
    assert listing.quantity_available == 3


# --------------------------------------------------------------------------
# Limits on what one checkout may ask for
# --------------------------------------------------------------------------


def test_an_order_of_more_than_fifty_lines_is_refused(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    """Checkout locks every listing it names, so one request names at most 50.

    Refused by the schema, before any listing is looked up: the ids need not
    exist, and the 422 must come from the line count, not an unknown id.
    """
    lines = [{"listing_id": n, "quantity": 1} for n in range(1, 52)]
    response = client.post(
        "/api/orders", json={"items": lines}, headers=customer_headers
    )
    assert response.status_code == 422, response.text
    assert any(error["loc"][-1] == "items" for error in response.json()["detail"])


def test_a_customer_holding_the_most_pending_orders_is_refused_another(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Unpaid orders hold stock, so one account may hold only so many.

    The listing has stock for every attempt, so the refusal can only be the
    count. Paying one frees a place: it is pending orders that are counted.
    """
    plenty = make_listing(quantity_available=MAX_PENDING_ORDERS + 3)
    placed = [
        post_order(client, customer_headers, plenty.id, 1)
        for _ in range(MAX_PENDING_ORDERS)
    ]
    assert [response.status_code for response in placed] == [201] * len(placed)

    refused = post_order(client, customer_headers, plenty.id, 1)
    assert refused.status_code == 409, refused.text
    assert "waiting for payment" in refused.json()["detail"]
    db.refresh(plenty)
    assert plenty.quantity_available == 3, "a refused order takes no stock"

    _walk(client, admin_headers, placed[0].json()["id"], "paid")
    assert post_order(client, customer_headers, plenty.id, 1).status_code == 201


def test_an_order_placed_on_a_customers_behalf_is_not_capped(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """The cap is the shop's: an order entered in the console is not counted out.

    The person placing it there is the one who would cancel it.
    """
    plenty = make_listing(quantity_available=MAX_PENDING_ORDERS + 1)
    customer_id = 0
    for _ in range(MAX_PENDING_ORDERS):
        placed = post_order(client, customer_headers, plenty.id, 1)
        assert placed.status_code == 201, placed.text
        customer_id = placed.json()["customer_id"]
    assert post_order(client, customer_headers, plenty.id, 1).status_code == 409

    on_behalf = client.post(
        f"/api/customers/{customer_id}/orders",
        json={"items": [{"listing_id": plenty.id, "quantity": 1}]},
        headers=admin_headers,
    )
    assert on_behalf.status_code == 201, on_behalf.text
    assert on_behalf.json()["status"] == "pending"


def test_an_order_at_the_price_the_buyer_saw_is_placed(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"listing_id": listing.id, "quantity": 1, "expected_price": "189.00"}
            ]
        },
        headers=customer_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["total_amount"] == "189.00"


def test_an_order_at_a_price_changed_since_the_buyer_saw_it_is_refused(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    db: Session,
) -> None:
    """A repriced listing is not charged at a price the buyer never saw."""
    response = client.post(
        "/api/orders",
        json={
            "items": [
                {"listing_id": listing.id, "quantity": 1, "expected_price": "150.00"}
            ]
        },
        headers=customer_headers,
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert f"listing {listing.id}" in detail
    assert "189.00" in detail
    db.refresh(listing)
    assert listing.quantity_available == 5, "a refused order takes no stock"


# --------------------------------------------------------------------------
# The order list
# --------------------------------------------------------------------------


def test_the_order_list_is_paged_newest_first_with_a_total(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    ids = [
        post_order(client, customer_headers, listing.id, 1).json()["id"]
        for _ in range(3)
    ]

    first = client.get("/api/orders", params={"limit": 2}, headers=admin_headers)
    assert first.status_code == 200, first.text
    assert first.headers["X-Total-Count"] == "3"
    assert [order["id"] for order in first.json()] == sorted(ids, reverse=True)[:2]

    rest = client.get(
        "/api/orders", params={"limit": 2, "offset": 2}, headers=admin_headers
    )
    assert rest.headers["X-Total-Count"] == "3"
    assert [order["id"] for order in rest.json()] == [min(ids)]


@pytest.mark.parametrize("limit", [0, 1001])
def test_the_order_list_refuses_a_page_size_out_of_range(
    limit: int, client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.get("/api/orders", params={"limit": limit}, headers=admin_headers)
    assert response.status_code == 422, response.text


def test_the_order_list_is_filtered_by_status(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    paid = post_order(client, customer_headers, listing.id, 1).json()
    post_order(client, customer_headers, listing.id, 1)
    _walk(client, admin_headers, paid["id"], "paid")

    response = client.get(
        "/api/orders", params={"status": "paid"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert [order["id"] for order in response.json()] == [paid["id"]]
    assert response.headers["X-Total-Count"] == "1"

    unknown = client.get(
        "/api/orders", params={"status": "lost"}, headers=admin_headers
    )
    assert unknown.status_code == 422, unknown.text
    assert "Unknown status" in unknown.json()["detail"]


def test_a_line_s_snapshot_is_in_the_order_but_not_in_the_list(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """The snapshot is the item as sold; a page of orders does not carry one per line.

    Read as an administrator, the one caller the detail gives it to, so its
    absence from the list is the list's doing and not the caller's role.
    """
    order = post_order(client, customer_headers, listing.id, 1).json()

    detail = client.get(f"/api/orders/{order['id']}", headers=admin_headers).json()
    assert detail["items"][0]["snapshot"] is not None

    listed = client.get("/api/orders", headers=admin_headers).json()
    assert [line["snapshot"] for line in listed[0]["items"]] == [None]
    # The title is still there: it is read from the snapshot, not sent with it.
    assert listed[0]["items"][0]["title"] == detail["items"][0]["title"]


# --------------------------------------------------------------------------
# Ending a listing an order holds
# --------------------------------------------------------------------------


def test_a_listing_an_open_order_holds_cannot_be_ended_until_the_order_is_cancelled(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Ending it first would leave the order nothing to return its stock to.

    The order would then be refused cancellation for good
    (`routers.orders._no_stock_to_return`). So End waits for the order.
    """
    only_one = make_listing(quantity_available=1)
    order = post_order(client, customer_headers, only_one.id, 1).json()

    refused = client.post(f"/api/listings/{only_one.id}/end", headers=admin_headers)
    assert refused.status_code == 409, refused.text
    assert f"order #{order['id']}" in refused.json()["detail"]
    db.refresh(only_one)
    assert only_one.status is ListingStatus.active, "a refused End ends nothing"

    _walk(client, admin_headers, order["id"], "cancelled")
    ended = client.post(f"/api/listings/{only_one.id}/end", headers=admin_headers)
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "ended"


def test_a_shipped_order_does_not_hold_a_listing_open(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """Shipped stock has left, and a cancellation returns none of it."""
    order = post_order(client, customer_headers, listing.id, 1).json()
    _walk(client, admin_headers, order["id"], "paid", "shipped")

    ended = client.post(f"/api/listings/{listing.id}/end", headers=admin_headers)
    assert ended.status_code == 200, ended.text


def test_an_ended_item_listing_s_refusal_does_not_blame_a_lot(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """The lot explanation is for a lot; an item listing's refusal leaves it out.

    The listing is ended through the writer, past End's own guard, which is
    the shape a path that ends offers without asking about orders leaves.
    """
    order = post_order(client, customer_headers, listing.id, 1).json()
    db.refresh(listing)
    offering_writes.end_offer(db, listing, sold=False)
    db.commit()

    refused = _set_status(client, admin_headers, order["id"], "cancelled")
    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert f"holds listing #{listing.id}, which has ended, so its stock" in detail
    assert "lot" not in detail
