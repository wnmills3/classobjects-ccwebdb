"""The item as sold, and the warning before changing an item that is for sale.

app.sale_snapshot and app.sale_state.
"""

from __future__ import annotations

from collections.abc import Callable

from app.models import InventoryItem, Listing, SalesOrderItem
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]
ListingFactory = Callable[..., Listing]


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
    item_id = listing.inventory_item_id
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
    assert snapshot["snapshot_version"] == 1
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
    item_id = listing.inventory_item_id
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


# --- for sale ----------------------------------------------------------------------


def test_a_listed_item_is_not_changed_without_saying_so(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item_id = listing.inventory_item_id
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
    item_id = listing.inventory_item_id
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
    item_id = listing.inventory_item_id
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
    item_id = listing.inventory_item_id
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
