"""Orders placed and revised on a customer's behalf.

See docs/specs/order-on-behalf-design.md. The engine is `app.order_writes`;
these tests go through the HTTP API wherever a caller would.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from app.buyers import venue_buyer
from app.models import (
    Customer,
    Listing,
    ListingFormat,
    ListingStatus,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesOrder,
    SalesOrderChange,
    SalesOrderChangeKind,
    SalesOrderItemShare,
    SalesOrderStatus,
    SalesVenue,
    SalesVenueKind,
    User,
)
from app.order_writes import Line, customer_for_user, place_order, revise_order
from app.sales_venues import store_venue_id
from app.sales_writes import record_sale
from fastapi import HTTPException
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from tests.conftest import item_of
from tests.test_orders import place


def test_an_order_records_its_placer_version_and_changes(
    db: Session, customer_user: User, admin_user: User
) -> None:
    customer = Customer(user_id=customer_user.id, display_name="Buyer")
    db.add(customer)
    db.flush()
    pending = db.scalar(
        select(SalesOrderStatus.id).where(SalesOrderStatus.code == "pending")
    )
    order = SalesOrder(
        customer_id=customer.id,
        sales_venue_id=store_venue_id(db),
        sales_order_status_id=pending,
        placed_by_id=admin_user.id,
    )
    db.add(order)
    db.flush()
    db.add(
        SalesOrderChange(
            sales_order_id=order.id,
            changed_by_id=admin_user.id,
            change=SalesOrderChangeKind.placed,
            to_value=admin_user.email,
        )
    )
    db.commit()
    db.refresh(order)

    assert order.version == 1
    assert order.placed_by is not None and order.placed_by.email == admin_user.email
    assert [c.change for c in order.changes] == [SalesOrderChangeKind.placed]
    assert order.total_amount == Decimal("0.00")


def test_checkout_records_the_buyer_as_placer_and_writes_placed(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    body = place(client, customer_headers, listing.id, 2).json()

    assert body["version"] == 1
    # placed_by_email is admin-only (see test_orders.py::
    # test_notes_and_placed_by_email_are_admin_only) -- a shopper's own
    # checkout response never carries it, even though the placer was
    # recorded, as the history query below confirms.
    assert body["placed_by_email"] is None
    assert body["payment_adjustment_due"] is False
    changes = db.scalars(
        select(SalesOrderChange).where(SalesOrderChange.sales_order_id == body["id"])
    ).all()
    assert [(c.change, c.to_value) for c in changes] == [
        (SalesOrderChangeKind.placed, "customer@example.com")
    ]


def _new_customer(db: Session, name: str = "Walk-in Buyer") -> Customer:
    customer = Customer(display_name=name, email=None)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def test_an_admin_places_an_order_for_a_customer_without_an_account(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)

    response = client.post(
        f"/api/customers/{buyer.id}/orders",
        json={
            "items": [
                {"listing_id": listing.id, "quantity": 2, "unit_price": "150.00"}
            ],
            "notes": "phone order",
        },
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["customer_name"] == "Walk-in Buyer"
    assert body["placed_by_email"] == "admin@example.com"
    assert body["items"][0]["unit_price"] == "150.00"
    assert body["total_amount"] == "300.00"
    assert body["notes"] == "phone order"
    db.refresh(listing)
    assert listing.quantity_available == 3


def test_a_line_without_a_price_takes_the_listing_price(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)
    body = client.post(
        f"/api/customers/{buyer.id}/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=admin_headers,
    ).json()
    assert body["items"][0]["unit_price"] == "189.00"


def test_only_an_admin_may_place_an_order_for_someone(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)
    payload = {
        "items": [{"listing_id": listing.id, "quantity": 1, "unit_price": "0.01"}]
    }
    url = f"/api/customers/{buyer.id}/orders"

    assert client.post(url, json=payload).status_code == 401
    assert client.post(url, json=payload, headers=customer_headers).status_code == 403
    assert db.scalar(select(SalesOrder.id)) is None
    db.refresh(listing)
    assert listing.quantity_available == 5


def test_placing_for_an_unknown_customer_is_a_404(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/customers/999999/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_admin_order_payloads_are_validated(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)
    url = f"/api/customers/{buyer.id}/orders"
    line = {"listing_id": listing.id, "quantity": 1}
    payloads: list[dict[str, object]] = [
        {"items": []},
        {"items": [{**line, "quantity": 0}]},
        {"items": [{**line, "unit_price": "-1.00"}]},
        {"items": [line, line]},
        {"items": [line], "customer_id": 1},
    ]
    for bad in payloads:
        assert client.post(url, json=bad, headers=admin_headers).status_code == 422, bad


def test_an_account_can_be_given_a_customer_record(
    client: TestClient, admin_headers: dict[str, str], customer_user: User, db: Session
) -> None:
    first = client.post(
        f"/api/users/{customer_user.id}/customer", headers=admin_headers
    )
    again = client.post(
        f"/api/users/{customer_user.id}/customer", headers=admin_headers
    )

    assert first.status_code == 200
    assert first.json()["user_id"] == customer_user.id
    assert again.json()["id"] == first.json()["id"]


def test_only_an_admin_may_create_a_customer_record_for_an_account(
    client: TestClient,
    customer_headers: dict[str, str],
    customer_user: User,
    db: Session,
) -> None:
    url = f"/api/users/{customer_user.id}/customer"
    assert client.post(url).status_code == 401
    assert client.post(url, headers=customer_headers).status_code == 403
    assert (
        db.scalar(select(Customer).where(Customer.user_id == customer_user.id)) is None
    )


def _place(
    client: TestClient, headers: dict[str, str], listing_id: int, qty: int
) -> dict[str, Any]:
    return client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing_id, "quantity": qty}]},
        headers=headers,
    ).json()


def _place_response(
    client: TestClient, headers: dict[str, str], listing_id: int, qty: int
) -> Response:
    return client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing_id, "quantity": qty}]},
        headers=headers,
    )


def _ebay(db: Session) -> int:
    kind = db.scalar(
        select(SalesVenueKind.id).where(SalesVenueKind.code == "marketplace")
    )
    venue = SalesVenue(code="ebay-test", name="eBay", sales_venue_kind_id=kind)
    db.add(venue)
    db.commit()
    return venue.id


def test_checkout_refuses_a_listing_on_another_platform(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    db: Session,
) -> None:
    listing = make_listing(sales_venue_id=_ebay(db))

    response = _place_response(client, customer_headers, listing.id, 1)

    assert response.status_code == 409
    assert "not sold in this shop" in response.json()["detail"]


def test_checkout_refuses_an_auction_listing(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
) -> None:
    listing = make_listing(format=ListingFormat.auction)

    response = _place_response(client, customer_headers, listing.id, 1)

    assert response.status_code == 409
    assert "not sold in this shop" in response.json()["detail"]


def test_an_outside_sale_records_its_platform_and_status(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """A sale on eBay is stored against eBay, already paid."""
    buyer = venue_buyer(db, ebay_venue, "coinfan88")
    order = place_order(
        db,
        buyer,
        [Line(listing_id=ebay_listing.id, quantity=1, unit_price=Decimal("120.00"))],
        admin_user,
        venue=ebay_venue,
        status_code="paid",
    )
    assert order.sales_venue_id == ebay_venue.id
    order_status = db.get(SalesOrderStatus, order.sales_order_status_id)
    assert order_status is not None
    assert order_status.code == "paid"


def test_an_outside_sale_gets_a_share_at_version_one(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """Recording an outside sale carries a share too, at a fresh version.

    `sale_state` (a later task) will find an order's items through shares
    no matter where the order came from, so an outside sale's line needs
    one exactly as a checkout line does. `version == 1` is pinned
    separately: it is correct by construction (a single INSERT, priced
    before it happens), but nothing else here checks it, and version is
    exactly what regressed when `place_order`'s insert/update ordering was
    wrong the first time.
    """
    buyer = venue_buyer(db, ebay_venue, "coinfan88")
    order = place_order(
        db,
        buyer,
        [Line(listing_id=ebay_listing.id, quantity=1, unit_price=Decimal("120.00"))],
        admin_user,
        venue=ebay_venue,
        status_code="paid",
    )
    assert order.version == 1
    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == order.items[0].id
        )
    ).all()
    assert [share.inventory_item_id for share in shares] == [
        ebay_listing.inventory_item_id
    ]
    assert shares[0].amount == Decimal("120.00")
    assert shares[0].fee_amount == Decimal("0.00")


def test_placing_an_order_never_consults_a_new_line_s_shares(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """A line created in this call has no shares, so nothing may look.

    Looking costs a SELECT that can only come back empty -- one per line,
    inside the `FOR UPDATE` window `offering_writes._lock_listing_rows`'
    docstring asks callers not to widen -- and leaves the collection
    **cached empty** for the rest of the
    session, because `db.add` does not invalidate a collection an earlier read
    populated. That stale cache is what forced `sales_writes` and
    `routers.offers` to read shares through their own `select()`.

    Asserted on the ORM's own bookkeeping rather than by counting statements:
    `unloaded` naming `shares` is the same fact as "no SELECT was issued and
    nothing was cached", and it cannot be satisfied by a query that merely
    looks different.
    """
    buyer = venue_buyer(db, ebay_venue, "sharespeeker")
    order = place_order(
        db,
        buyer,
        [Line(listing_id=ebay_listing.id, quantity=1, unit_price=Decimal("133.75"))],
        admin_user,
        venue=ebay_venue,
        status_code="paid",
    )
    assert "shares" in inspect(order.items[0]).unloaded
    # The share still exists -- this is about how it was written, not whether.
    written = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == order.items[0].id
        )
    ).all()
    assert [share.amount for share in written] == [Decimal("133.75")]


def test_an_outside_listing_is_not_refused_for_being_outside_the_shop(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """The shop guards are checkout's rules, not every order's."""
    buyer = venue_buyer(db, ebay_venue, "coinfan88")
    order = place_order(
        db,
        buyer,
        [Line(listing_id=ebay_listing.id, quantity=1, unit_price=Decimal("120.00"))],
        admin_user,
        venue=ebay_venue,
        status_code="paid",
    )
    assert order.id is not None


def test_checkout_still_refuses_a_listing_from_another_platform(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """Without a venue this is checkout, and checkout is the shop only.

    This is the guard that stops a shop request buying an eBay listing; the
    new keyword must not have opened it.
    """
    customer = _new_customer(db)

    with pytest.raises(HTTPException) as caught:
        place_order(
            db,
            customer,
            [Line(listing_id=ebay_listing.id, quantity=1)],
            admin_user,
        )
    assert caught.value.status_code == 409
    assert "not sold in this shop" in caught.value.detail


def _revise(
    client: TestClient,
    headers: dict[str, str],
    order: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    customer_id: int | None = None,
    notes: str | None = None,
) -> Response:
    return client.put(
        f"/api/orders/{order['id']}",
        json={
            "version": order["version"],
            "customer_id": order["customer_id"] if customer_id is None else customer_id,
            "items": items,
            "notes": order["notes"] if notes is None else notes,
        },
        headers=headers,
    )


def _changes(db: Session, order_id: int) -> list[tuple[str, str | None, str | None]]:
    db.expire_all()
    rows = db.scalars(
        select(SalesOrderChange)
        .where(SalesOrderChange.sales_order_id == order_id)
        .order_by(SalesOrderChange.id)
    ).all()
    return [(c.change.value, c.from_value, c.to_value) for c in rows]


def test_raising_a_quantity_takes_stock_and_records_it(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 2)

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 3, "unit_price": "189.00"}],
    )

    assert response.status_code == 200, response.text
    assert response.json()["total_amount"] == "567.00"
    assert response.json()["version"] == order["version"] + 1
    db.refresh(listing)
    assert listing.quantity_available == 2
    assert _changes(db, order["id"])[1:] == [
        ("quantity", "2", "3"),
        ("total", "378.00", "567.00"),
    ]


def test_revising_a_lines_quantity_and_price_brings_its_share_back_in_line(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Raising a line's quantity and price must raise its share too.

    A share is a line's money kept in another table -- otherwise the two
    would silently drift apart.
    """
    order = _place(client, customer_headers, listing.id, 2)
    original_item_id = order["items"][0]["id"]
    original_share = db.scalar(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == original_item_id
        )
    )
    assert original_share is not None
    assert original_share.amount == Decimal("378.00")  # 2 x 189.00

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 3, "unit_price": "200.00"}],
    )

    assert response.status_code == 200, response.text
    db.refresh(original_share)
    # Still the same share row (same line, same item) -- its amount, and
    # only its amount, now matches the revised line: 3 x 200.00.
    assert original_share.sales_order_item_id == original_item_id
    assert original_share.amount == Decimal("600.00")


def test_revising_in_a_new_line_gives_it_a_share(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """A line added by a revision needs a share exactly as a checkout one does.

    `_line` is the only place a `SalesOrderItem` is built; the "line added"
    branch of `revise_order` is the one path into it that a plain checkout
    never exercises, and so the one Task 5's own test missed.
    """
    kept = make_listing(title="Kept", price=Decimal("50.00"), quantity_available=5)
    added = make_listing(title="Added", price=Decimal("30.00"), quantity_available=5)
    order = _place(client, customer_headers, kept.id, 1)

    response = _revise(
        client,
        admin_headers,
        order,
        [
            {"listing_id": kept.id, "quantity": 1, "unit_price": "50.00"},
            {"listing_id": added.id, "quantity": 2, "unit_price": "30.00"},
        ],
    )

    assert response.status_code == 200, response.text
    added_item = next(
        item for item in response.json()["items"] if item["listing_id"] == added.id
    )
    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == added_item["id"]
        )
    ).all()
    assert [share.inventory_item_id for share in shares] == [added.inventory_item_id]
    assert shares[0].amount == Decimal("60.00")


def test_one_listing_swapped_for_another_at_an_agreed_price_in_one_save(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    morgan = make_listing(title="Morgan", price=Decimal("100.00"), quantity_available=5)
    dime = make_listing(title="Dime", price=Decimal("10.00"), quantity_available=5)
    order = _place(client, customer_headers, morgan.id, 2)

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": dime.id, "quantity": 1, "unit_price": "8.00"}],
    )

    assert response.status_code == 200, response.text
    db.refresh(morgan)
    db.refresh(dime)
    assert (morgan.quantity_available, dime.quantity_available) == (5, 4)
    kinds = [c[0] for c in _changes(db, order["id"])]
    assert kinds == ["placed", "line_removed", "line_added", "total"]
    assert ("line_added", None, "1 @ 8.00") in _changes(db, order["id"])
    assert morgan.id not in {line["listing_id"] for line in response.json()["items"]}


def test_an_over_request_changes_nothing(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    plenty = make_listing(title="Plenty", quantity_available=5)
    scarce = make_listing(title="Scarce", quantity_available=1)
    order = _place(client, customer_headers, plenty.id, 1)

    response = _revise(
        client,
        admin_headers,
        order,
        [
            {"listing_id": plenty.id, "quantity": 4, "unit_price": "189.00"},
            {"listing_id": scarce.id, "quantity": 2, "unit_price": "189.00"},
        ],
    )

    assert response.status_code == 409
    assert f"listing {scarce.id}" in response.json()["detail"]
    db.refresh(plenty)
    db.refresh(scarce)
    assert (plenty.quantity_available, scarce.quantity_available) == (4, 1)
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]


def test_an_edit_that_frees_the_last_unit_relists_the_item(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    only = make_listing(title="Only one", quantity_available=1)
    other = make_listing(title="Other", quantity_available=5)
    order = _place(client, customer_headers, only.id, 1)
    db.refresh(item_of(only))
    assert item_of(only).disposition.code == "sold"

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": other.id, "quantity": 1, "unit_price": "189.00"}],
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert item_of(only).disposition.code == "listed"


def test_a_no_change_save_writes_nothing(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 2)
    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}],
    )
    assert response.json()["version"] == order["version"]
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]


def test_customer_and_notes_changes_are_recorded(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    other = _new_customer(db, "Grace Hopper")

    _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 1, "unit_price": "189.00"}],
        customer_id=other.id,
        notes="deliver Friday",
    )

    assert _changes(db, order["id"])[1:] == [
        ("customer", "Test Customer", "Grace Hopper"),
        ("notes", None, "deliver Friday"),
    ]


def test_only_pending_or_paid_orders_can_be_revised(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    client.patch(
        f"/api/orders/{order['id']}", json={"status": "packed"}, headers=admin_headers
    )
    fresh = client.get(f"/api/orders/{order['id']}", headers=admin_headers).json()

    response = _revise(
        client,
        admin_headers,
        fresh,
        [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}],
    )

    assert response.status_code == 409
    assert "packed" in response.json()["detail"]


def test_a_stale_version_is_refused(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    line = [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}]
    assert _revise(client, admin_headers, order, line).status_code == 200

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 3, "unit_price": "189.00"}],
    )

    assert response.status_code == 409
    assert "reload" in response.json()["detail"].lower()


def test_only_an_admin_may_revise(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    line = [{"listing_id": listing.id, "quantity": 1, "unit_price": "0.01"}]
    assert _revise(client, {}, order, line).status_code == 401
    assert _revise(client, customer_headers, order, line).status_code == 403
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]


def test_a_price_only_change_is_recorded(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 1)

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 1, "unit_price": "150.00"}],
    )

    assert response.status_code == 200, response.text
    assert _changes(db, order["id"])[1:] == [
        ("unit_price", "189.00", "150.00"),
        ("total", "189.00", "150.00"),
    ]


def test_a_change_leaving_the_total_unchanged_still_bumps_the_version(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    a = make_listing(title="A", price=Decimal("100.00"), quantity_available=5)
    b = make_listing(title="B", price=Decimal("100.00"), quantity_available=5)
    order = client.post(
        "/api/orders",
        json={
            "items": [
                {"listing_id": a.id, "quantity": 1},
                {"listing_id": b.id, "quantity": 1},
            ]
        },
        headers=customer_headers,
    ).json()
    assert order["total_amount"] == "200.00"

    response = _revise(
        client,
        admin_headers,
        order,
        [
            {"listing_id": a.id, "quantity": 2, "unit_price": "50.00"},
            {"listing_id": b.id, "quantity": 1, "unit_price": "100.00"},
        ],
    )

    assert response.status_code == 200, response.text
    assert response.json()["total_amount"] == "200.00"
    assert response.json()["version"] == order["version"] + 1
    assert "total" not in [c[0] for c in _changes(db, order["id"])]


def test_raising_a_quantity_on_an_inactive_listing_is_refused(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    listing.status = ListingStatus.ended
    db.commit()

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}],
    )

    assert response.status_code == 409
    db.refresh(listing)
    assert listing.quantity_available == 4
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]


def test_raising_a_quantity_on_a_listing_moved_to_another_platform_is_refused(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    listing.sales_venue_id = _ebay(db)
    db.commit()

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}],
    )

    assert response.status_code == 409
    assert "not sold in this shop" in response.json()["detail"]
    db.refresh(listing)
    assert listing.quantity_available == 4
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]


def test_raising_a_quantity_on_a_listing_turned_to_auction_is_refused(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    listing.format = ListingFormat.auction
    db.commit()

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}],
    )

    assert response.status_code == 409
    assert "not sold in this shop" in response.json()["detail"]
    db.refresh(listing)
    assert listing.quantity_available == 4
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]


def test_revising_with_an_unknown_customer_is_a_404(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = _place(client, customer_headers, listing.id, 1)

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 1, "unit_price": "189.00"}],
        customer_id=999999,
    )

    assert response.status_code == 404


def test_revising_with_an_unknown_listing_is_a_404(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 1)

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": 999999, "quantity": 1, "unit_price": "189.00"}],
    )

    assert response.status_code == 404
    db.refresh(listing)
    assert listing.quantity_available == 4
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]


def test_a_status_change_is_recorded_and_bumps_the_version(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    body = client.patch(
        f"/api/orders/{order['id']}", json={"status": "paid"}, headers=admin_headers
    ).json()
    assert body["version"] == order["version"] + 1
    assert _changes(db, order["id"])[1:] == [("status", "pending", "paid")]


def test_payment_adjustment_is_due_only_after_a_paid_total_changes(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = _place(client, customer_headers, listing.id, 1)

    def line(qty: int) -> list[dict[str, Any]]:
        return [{"listing_id": listing.id, "quantity": qty, "unit_price": "189.00"}]

    order = _revise(client, admin_headers, order, line(2)).json()
    assert order["payment_adjustment_due"] is False  # still pending

    order = client.patch(
        f"/api/orders/{order['id']}", json={"status": "paid"}, headers=admin_headers
    ).json()
    assert order["payment_adjustment_due"] is False

    order = _revise(client, admin_headers, order, line(3)).json()
    assert order["payment_adjustment_due"] is True


def test_the_history_reads_newest_first_with_who_and_what(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}],
    )

    rows = client.get(
        f"/api/orders/{order['id']}/changes", headers=admin_headers
    ).json()

    assert [r["change"] for r in rows] == ["total", "quantity", "placed"]
    assert rows[1]["changed_by_email"] == "admin@example.com"
    assert rows[1]["listing_title"] == "1881-S Morgan Silver Dollar"
    assert rows[2]["changed_by_email"] == "customer@example.com"


def test_only_an_admin_may_read_the_history(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    url = f"/api/orders/{order['id']}/changes"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=customer_headers).status_code == 403


def test_cancelling_relists_an_item_that_had_sold_out(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """Fix round 1, ruling-1 behaviour: `return_stock` must relist on its own.

    `place_order` marks the item `sold` once its listing hits zero;
    `return_stock` must reverse that when a cancellation puts the last unit
    back, the same before/after-zero rule every stock-moving path uses --
    not "relist whenever the listing is active", which the inline code this
    replaced did.
    """
    order = _place(client, customer_headers, listing.id, 5)
    db.refresh(listing)
    assert listing.quantity_available == 0
    db.expire_all()
    assert item_of(listing).disposition.code == "sold"

    response = client.patch(
        f"/api/orders/{order['id']}",
        json={"status": "cancelled"},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert item_of(listing).disposition.code == "listed"


# --- a lot listing's line ----------------------------------------------------------


def _sell_the_lot(db: Session, listing: Listing, admin_user: User) -> SalesOrder:
    """Record the lot listing as sold for 1,000.00, the way an operator would."""
    return record_sale(
        db,
        listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )


def test_revising_a_lot_line_redistributes_every_share(
    db: Session, offered_lot_listing: Listing, admin_user: User
) -> None:
    """The update branch had no lot case at all, because the guard returned first.

    `_sync_shares` returned immediately when `listing.inventory_item_id is
    None`, so a revised lot line's money would move while its shares stayed
    where they were -- silently, since nothing sums them back. The mutation
    that proves this test: restore the early return and confirm it goes red.

    The redistribution is read off the shares that exist, not off the lot's
    current members: `record_sale` ends the offer, which releases every
    membership, so by the time a revision arrives the lot has no open
    members left to ask.
    """
    order = _sell_the_lot(db, offered_lot_listing, admin_user)
    line = order.items[0]

    revise_order(
        db,
        order,
        customer=order.customer,
        lines=[
            Line(
                listing_id=line.listing_id,
                quantity=1,
                unit_price=Decimal("700.00"),
            )
        ],
        notes=None,
        version=order.version,
        by=admin_user,
    )

    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == line.id
        )
    ).all()
    assert len(shares) == 3
    assert sum(share.amount for share in shares) == Decimal("700.00")
    # Still weighted 500 / 300 / 200, not moved into one row.
    assert sorted(share.amount for share in shares) == [
        Decimal("140.00"),
        Decimal("210.00"),
        Decimal("350.00"),
    ]


def test_the_history_of_a_lot_line_is_titled_by_the_lot(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
    admin_user: User,
) -> None:
    """`order_changes` read `row.listing.inventory_item.source_title` too.

    A third crash site of the same shape as `_sold_as`, on a page an
    operator reaches from the order itself: a lot listing has no
    `inventory_item`, so the history 500ed rather than naming the lot.
    """
    order = _sell_the_lot(db, offered_lot_listing, admin_user)
    revise_order(
        db,
        order,
        customer=order.customer,
        lines=[
            Line(
                listing_id=order.items[0].listing_id,
                quantity=1,
                unit_price=Decimal("700.00"),
            )
        ],
        notes=None,
        version=order.version,
        by=admin_user,
    )
    db.commit()

    response = client.get(f"/api/orders/{order.id}/changes", headers=admin_headers)

    assert response.status_code == 200, response.text
    priced = next(row for row in response.json() if row["change"] == "unit_price")
    assert priced["listing_title"] == "Three Morgan Dollars"


def test_a_revision_that_removes_a_lot_line_is_refused(
    db: Session,
    store_lot_listing: Listing,
    customer_user: User,
    admin_user: User,
) -> None:
    """The other way to hand a line's stock back, refused for the same reason.

    Cancelling such an order is refused in `routers.orders`; removing the
    line is the same act one screen along. The stock would go back to a
    listing this module ended when the lot was bought, where
    `_after_stock_change` cannot move the coins off `sold` -- so they would
    be stranded exactly as a cancellation would strand them.
    """
    customer = customer_for_user(db, customer_user)
    order = place_order(db, customer, [Line(store_lot_listing.id, 1)], admin_user)

    with pytest.raises(HTTPException) as refused:
        revise_order(
            db,
            order,
            customer=customer,
            lines=[],
            notes=None,
            version=order.version,
            by=admin_user,
        )

    assert refused.value.status_code == 409
    assert "has ended" in str(refused.value.detail)


def test_a_revision_that_adds_a_lot_line_ends_the_lot_sold(
    db: Session,
    make_listing: Callable[..., Listing],
    store_lot_listing: Listing,
    customer_user: User,
    admin_user: User,
) -> None:
    """The same ending a checkout gets, on the other path that takes the stock.

    An administrator adding a lot to an existing order buys it exactly as a
    shopper does -- one unit, the only one there is -- so leaving the lot
    `offered` here would be the same false statement, and would leave the
    same membership rows open against coins that are already sold.
    """
    lot = store_lot_listing.sales_lot
    assert lot is not None
    lot_id = lot.id
    item_listing = make_listing()
    customer = customer_for_user(db, customer_user)
    order = place_order(db, customer, [Line(item_listing.id, 1)], admin_user)

    revise_order(
        db,
        order,
        customer=customer,
        lines=[Line(item_listing.id, 1), Line(store_lot_listing.id, 1)],
        notes=None,
        version=order.version,
        by=admin_user,
    )

    sold_lot = db.get(SalesLot, lot_id)
    assert sold_lot is not None
    assert sold_lot.status is SalesLotStatus.sold
    open_rows = db.scalars(
        select(SalesLotItem).where(
            SalesLotItem.sales_lot_id == lot_id, SalesLotItem.released_at.is_(None)
        )
    ).all()
    assert not open_rows
