"""Orders placed and revised on a customer's behalf.

See docs/specs/order-on-behalf-design.md. The engine is `app.order_writes`;
these tests go through the HTTP API wherever a caller would.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from app.models import (
    Customer,
    Listing,
    ListingFormat,
    ListingStatus,
    SalesOrder,
    SalesOrderChange,
    SalesOrderChangeKind,
    SalesOrderStatus,
    SalesVenue,
    SalesVenueKind,
    User,
)
from app.sales_venues import store_venue_id
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

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
    for bad in (
        {"items": []},
        {"items": [{**line, "quantity": 0}]},
        {"items": [{**line, "unit_price": "-1.00"}]},
        {"items": [line, line]},
        {"items": [line], "customer_id": 1},
    ):
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


def test_a_listing_swapped_out_of_an_order_cannot_be_deleted(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
    db: Session,
) -> None:
    """A listing kept alive only by order history must not be deletable.

    `revise_order`'s line-removed branch drops the `sales_order_item` row but
    keeps the listing id in `sales_order_change`, and that foreign key is
    `ON DELETE RESTRICT` -- history must keep resolving to what was actually
    bought. A guard that only counted `SalesOrderItem` would see nothing left
    once the swap above has run, let the delete through, and hit that
    RESTRICT as an unhandled `IntegrityError`.
    """
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

    delete = client.delete(f"/api/catalog/{morgan.id}", headers=admin_headers)

    assert delete.status_code == 409
    assert "is_active" in delete.json()["detail"]
    db.expire_all()
    assert db.get(Listing, morgan.id) is not None


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
    db.refresh(only.inventory_item)
    assert only.inventory_item.disposition.code == "sold"

    response = _revise(
        client,
        admin_headers,
        order,
        [{"listing_id": other.id, "quantity": 1, "unit_price": "189.00"}],
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert only.inventory_item.disposition.code == "listed"


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
    assert listing.inventory_item.disposition.code == "sold"

    response = client.patch(
        f"/api/orders/{order['id']}",
        json={"status": "cancelled"},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert listing.inventory_item.disposition.code == "listed"
