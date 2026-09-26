"""The item as sold, and the warning before changing an item that is for sale.

app.sale_snapshot and app.sale_state.
"""

from __future__ import annotations

from decimal import Decimal

from app import sale_snapshot
from app.models import Listing, SalesOrderItem, User
from app.sales_writes import record_sale
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, ListingFactory
from tests.conftest import item_id_of


def _order(
    client: TestClient, headers: dict[str, str], listing: Listing, quantity: int = 1
) -> dict:
    response = client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing.id, "quantity": quantity}]},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _status(
    client: TestClient, headers: dict[str, str], order_id: int, status: str
) -> None:
    response = client.patch(
        f"/api/orders/{order_id}", json={"status": status}, headers=headers
    )
    assert response.status_code == 200, response.text


def _edit(
    client: TestClient, headers: dict[str, str], item_id: int, **changes: object
) -> Response:
    return client.patch(f"/api/inventory/{item_id}", json=changes, headers=headers)


def _admin_order(client: TestClient, headers: dict[str, str], order_id: int) -> dict:
    return client.get(f"/api/orders/{order_id}", headers=headers).json()


# --- the snapshot ------------------------------------------------------------------


def test_a_sale_keeps_the_item_as_it_was_sold(
    client: TestClient,
    make_listing: ListingFactory,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    listing = make_listing(
        title="1881-S Morgan",
        description="Blast white",
        grade="MS64",
        price="189.00",
        quantity_available=1,
    )
    item_id = item_id_of(listing)
    order = _order(client, customer_headers, listing)

    # Shipped: no longer for sale, so an ordinary edit.
    _status(client, admin_headers, order["id"], "shipped")
    assert (
        _edit(
            client,
            admin_headers,
            item_id,
            source_title="1881-S Morgan (corrected)",
            grade="MS63",
            description="Toned",
        ).status_code
        == 200
    )

    line = _admin_order(client, admin_headers, order["id"])["items"][0]
    snapshot = line["snapshot"]
    assert line["title"] == "1881-S Morgan"
    assert snapshot["snapshot_version"] == 2
    assert snapshot["item"]["source_title"] == "1881-S Morgan"
    assert snapshot["item"]["description"] == "Blast white"
    assert snapshot["item"]["grade_display"] == "MS64"
    assert snapshot["item"]["item_code"]
    assert snapshot["listing"]["price"] == "189.00"
    assert snapshot["listing"]["currency"] == "USD"
    # What describes the editing is left out.
    assert "reviewed" not in snapshot["item"]
    assert "sale_state" not in snapshot["item"]


def test_a_shopper_sees_what_they_bought_but_not_the_snapshot(
    client: TestClient,
    listing: Listing,
    customer_headers: dict[str, str],
) -> None:
    order = _order(client, customer_headers, listing)
    mine = client.get(f"/api/orders/{order['id']}", headers=customer_headers).json()
    assert mine["items"][0]["snapshot"] is None
    assert mine["items"][0]["title"] == "1881-S Morgan Silver Dollar"


def test_a_returned_item_sold_again_keeps_both_sales(
    client: TestClient,
    db: Session,
    make_listing: ListingFactory,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    listing = make_listing(grade="MS64", quantity_available=1)
    item_id = item_id_of(listing)
    first = _order(client, customer_headers, listing)
    _status(client, admin_headers, first["id"], "shipped")

    # Back from the buyer, re-graded, and offered again.
    assert _edit(client, admin_headers, item_id, grade="MS62").status_code == 200
    db.refresh(listing)
    listing.quantity_available = 1
    db.commit()
    second = _order(client, customer_headers, listing)

    sales = client.get(f"/api/inventory/{item_id}/sales", headers=admin_headers)
    assert sales.status_code == 200
    body = sales.json()
    assert [s["order_id"] for s in body] == [second["id"], first["id"]]
    assert [s["snapshot"]["item"]["grade_display"] for s in body] == ["MS62", "MS64"]
    assert body[1]["status"] == "shipped"


def test_a_revision_snapshots_a_new_line_and_keeps_an_old_one(
    client: TestClient,
    db: Session,
    make_listing: ListingFactory,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    kept = make_listing(title="Kept")
    added = make_listing(title="Added")
    order = _order(client, customer_headers, kept)
    before = db.scalar(
        select(SalesOrderItem.item_snapshot).where(SalesOrderItem.listing_id == kept.id)
    )

    body = _admin_order(client, admin_headers, order["id"])
    response = client.put(
        f"/api/orders/{order['id']}",
        json={
            "version": body["version"],
            "customer_id": body["customer_id"],
            "items": [
                {"listing_id": kept.id, "quantity": 2, "unit_price": "189.00"},
                {"listing_id": added.id, "quantity": 1, "unit_price": "10.00"},
            ],
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    db.expire_all()
    lines = {
        line.listing_id: line
        for line in db.scalars(
            select(SalesOrderItem).where(SalesOrderItem.sales_order_id == order["id"])
        )
    }
    assert lines[kept.id].item_snapshot == before
    added_snapshot = lines[added.id].item_snapshot
    assert added_snapshot is not None
    assert added_snapshot["item"]["source_title"] == "Added"


def test_a_revision_snapshots_an_added_line_as_it_was_offered(
    client: TestClient,
    db: Session,
    make_listing: ListingFactory,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """The copy is taken before the stock change, as at checkout.

    Taken after it, an added line's item already reads as sold -- by the
    very sale the copy is meant to describe the item before.
    """
    kept = make_listing(title="Kept", quantity_available=1)
    added = make_listing(title="Added", quantity_available=1)
    order = _order(client, customer_headers, kept)
    placed = db.scalar(
        select(SalesOrderItem.item_snapshot).where(SalesOrderItem.listing_id == kept.id)
    )
    assert placed is not None

    body = _admin_order(client, admin_headers, order["id"])
    response = client.put(
        f"/api/orders/{order['id']}",
        json={
            "version": body["version"],
            "customer_id": body["customer_id"],
            "items": [
                {"listing_id": kept.id, "quantity": 1, "unit_price": "189.00"},
                {"listing_id": added.id, "quantity": 1, "unit_price": "10.00"},
            ],
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    db.expire_all()
    added_snapshot = db.scalar(
        select(SalesOrderItem.item_snapshot).where(
            SalesOrderItem.listing_id == added.id
        )
    )
    assert added_snapshot is not None
    assert added_snapshot["item"]["disposition"] != "sold"
    assert added_snapshot["item"]["disposition"] == placed["item"]["disposition"]


def test_a_snapshot_keeps_the_item_not_who_edited_it(
    client: TestClient,
    make_listing: ListingFactory,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """The editor's `last_changes` names people; a sale's copy has no use for it.

    Mint, variety and certificates are the editor's own values, under the
    snapshot's keys.
    """
    listing = make_listing(quantity_available=1)
    item_id = item_id_of(listing)
    edited = _edit(
        client,
        admin_headers,
        item_id,
        description="Edited before the sale",
        cert_numbers=["12345678"],
        acknowledge_for_sale=True,
    )
    assert edited.status_code == 200, edited.text
    detail = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    assert detail["last_changes"]
    order = _order(client, customer_headers, listing)

    item = _admin_order(client, admin_headers, order["id"])["items"][0]["snapshot"][
        "item"
    ]
    assert "last_changes" not in item
    assert item["description"] == "Edited before the sale"
    assert item["certificates"] == ["12345678"]
    assert item["cert_numbers"] == ["12345678"]
    assert item["mint"] == detail["mint"]
    assert item["variety"] == detail["variety"]


def test_a_lot_listing_snapshots_every_member(
    db: Session, offered_lot_listing: Listing
) -> None:
    """`take` read `listing.inventory_item` and would crash on None.

    The snapshot is what the order keeps forever, so a lot's must name every
    coin that was in it -- the membership rows are released at sale, and the
    group can be reconstructed from nothing but this.
    """
    snapshot = sale_snapshot.take(db, offered_lot_listing)
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    assert snapshot["snapshot_version"] == 2
    assert "item" not in snapshot
    assert snapshot["lot"]["title"] == lot.title
    assert len(snapshot["items"]) == 3
    assert [entry["item_code"] for entry in snapshot["items"]] == sorted(
        entry["item_code"] for entry in snapshot["items"]
    )


def test_an_item_listing_s_snapshot_keeps_its_shape(
    db: Session, ebay_listing: Listing
) -> None:
    """Widening must not move the single-item keys every reader already uses.

    `snapshot_version` rises to 2 because the shape *set* changed -- a
    snapshot may now lack `item` entirely -- and a reader has to be able to
    tell which shapes it may meet. The keys themselves do not move.
    """
    snapshot = sale_snapshot.take(db, ebay_listing)
    assert snapshot["snapshot_version"] == 2
    assert "lot" not in snapshot
    assert snapshot["item"]["item_code"]
    assert snapshot["listing"]["price"] == "120.00"


def test_an_order_line_for_a_lot_is_titled_by_the_lot(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
    admin_user: User,
) -> None:
    """`_sold_as` fell back to `listing.inventory_item.source_title`.

    That is `None` for a lot listing, so the Sales page would raise
    `AttributeError` -- a 500 on every page that includes the order -- rather
    than showing the lot's title. Reached through the API, not by calling
    `_sold_as`, because the 500 is what an operator actually meets.
    """
    order = record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    db.commit()
    body = client.get(f"/api/orders/{order.id}", headers=admin_headers).json()
    assert body["items"][0]["title"] == "Three Morgan Dollars"


def test_a_coin_sold_inside_a_lot_shows_that_sale(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
    admin_user: User,
) -> None:
    """`GET /api/inventory/{id}/sales` filtered on `listing.inventory_item_id`.

    That column is NULL on a lot listing, so a coin sold inside a group had
    no sale history at all -- an empty list where the endpoint's own
    docstring promises every sale of the item, on the one screen an owner
    uses to answer "what happened to this coin". The lot's line is reached
    through the `sales_order_item_share` rows the sale writes, one per
    member.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_ids = [row.inventory_item_id for row in lot.members]
    assert len(member_ids) == 3
    order = record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-LOT-1",
        fees=[],
        recorded_by=admin_user,
    )
    db.commit()

    # The fixture's members cost 500, 300 and 200 in item-id order and the lot
    # sold for 1,000.00, so a cost-weighted division is exactly those figures.
    expected_shares = dict(
        zip(sorted(member_ids), ["500.00", "300.00", "200.00"], strict=True)
    )
    for member_id in member_ids:
        response = client.get(
            f"/api/inventory/{member_id}/sales", headers=admin_headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert [row["order_id"] for row in body] == [order.id], member_id
        # The snapshot is the lot's, so it names every coin rather than one.
        assert len(body[0]["snapshot"]["items"]) == 3
        # The row must say it was a group sale and credit this coin with its
        # own share. `unit_price` is the *lot's* 1,000.00 and is left alone;
        # showing that against one coin is the defect this pins.
        assert body[0]["sales_lot_id"] == lot.id
        assert body[0]["unit_price"] == "1000.00"
        assert body[0]["share_amount"] == expected_shares[member_id], member_id
        assert isinstance(body[0]["share_amount"], str)


def test_a_coin_sold_on_its_own_still_shows_one_row(
    client: TestClient,
    db: Session,
    make_listing: ListingFactory,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """The widening must not duplicate an ordinary sale.

    An item listing has both a share row and its own `inventory_item_id`, so
    a join to `sales_order_item_share` instead of a subquery would match the
    same line twice and report one sale as two.
    """
    listing = make_listing(quantity_available=1)
    order = _order(client, customer_headers, listing)
    body = client.get(
        f"/api/inventory/{listing.inventory_item_id}/sales", headers=admin_headers
    ).json()
    assert [row["order_id"] for row in body] == [order["id"]]
    # The shape an item sale already had, unchanged: the line's own figures,
    # and nothing claiming it was part of a group. `share_amount` equals the
    # line total here, because the line covers exactly this one coin.
    assert body[0]["sales_lot_id"] is None
    assert body[0]["quantity"] == 1
    assert body[0]["unit_price"] == "189.00"
    assert body[0]["share_amount"] == "189.00"


# --- for sale ----------------------------------------------------------------------


def test_a_listed_item_is_not_changed_without_saying_so(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item_id = item_id_of(listing)
    detail = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    assert detail["sale_state"] == [
        {
            "kind": "listing",
            "id": listing.id,
            "text": f"listing #{listing.id} at 189.00",
        }
    ]

    refused = _edit(client, admin_headers, item_id, description="Changed")
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]
    assert f"listing #{listing.id}" in refused.json()["detail"]
    unchanged = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    assert unchanged["description"] == "Test fixture item."

    made = _edit(
        client, admin_headers, item_id, description="Changed", acknowledge_for_sale=True
    )
    assert made.status_code == 200
    # Attributes are a change too.
    assert _edit(client, admin_headers, item_id, attributes=["cac"]).status_code == 409


def test_an_empty_save_of_a_listed_item_needs_nothing(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item_id = item_id_of(listing)
    version = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()[
        "version"
    ]
    assert _edit(client, admin_headers, item_id, version=version).status_code == 200


def test_an_item_in_an_unshipped_order_is_for_sale_until_it_ships(
    client: TestClient,
    make_listing: ListingFactory,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    listing = make_listing(quantity_available=1)
    item_id = item_id_of(listing)
    order = _order(client, customer_headers, listing)

    detail = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    # The listing has no stock left; the order still holds the item.
    assert detail["sale_state"] == [
        {"kind": "order", "id": order["id"], "text": f"order #{order['id']} (pending)"}
    ]
    assert _edit(client, admin_headers, item_id, description="x").status_code == 409

    _status(client, admin_headers, order["id"], "paid")
    _status(client, admin_headers, order["id"], "packed")
    assert _edit(client, admin_headers, item_id, description="x").status_code == 409

    _status(client, admin_headers, order["id"], "shipped")
    assert _edit(client, admin_headers, item_id, description="x").status_code == 200


def test_an_ended_listing_is_not_for_sale(
    client: TestClient, make_listing: ListingFactory, admin_headers: dict[str, str]
) -> None:
    listing = make_listing(is_active=False)
    item_id = item_id_of(listing)
    assert _edit(client, admin_headers, item_id, description="x").status_code == 200


def test_bulk_edit_names_the_items_for_sale(
    client: TestClient,
    listing: Listing,
    make_item: ItemFactory,
    admin_headers: dict[str, str],
) -> None:
    held = make_item()
    ids = [listing.inventory_item_id, held.id]
    code = client.get(
        f"/api/inventory/{listing.inventory_item_id}", headers=admin_headers
    ).json()["item_code"]

    refused = client.post(
        "/api/inventory/bulk",
        json={"ids": ids, "changes": {"description": "bulk"}},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert code in refused.json()["detail"]
    assert held.item_code not in refused.json()["detail"]

    made = client.post(
        "/api/inventory/bulk",
        json={
            "ids": ids,
            "changes": {"description": "bulk", "acknowledge_for_sale": True},
        },
        headers=admin_headers,
    )
    assert made.status_code == 200
