"""The warning before a change to an item that is for sale.

One file for the whole policy: `app.sale_state.guard` and each endpoint that
calls it. The existing coverage of the two original call sites lives in
`test_sale_snapshots.py`.
"""

from __future__ import annotations

import pytest
from app import sale_state
from app.models import InventoryItem, Listing, ListingStatus
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_the_guard_passes_an_item_that_is_not_for_sale(
    db: Session, listing: Listing
) -> None:
    other = db.get(InventoryItem, listing.inventory_item_id)
    assert other is not None
    # Ended, so nothing offers it. Assigned directly because this is a test
    # fixture being put into a state, not the application ending an offer --
    # `offering_writes` remains the only writer in app code.
    listing.status = ListingStatus.ended
    db.commit()
    sale_state.guard(db, [other], acknowledged=False)


def test_the_guard_refuses_a_listed_item_and_names_it(
    db: Session, listing: Listing
) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    with pytest.raises(HTTPException) as raised:
        sale_state.guard(db, [item], acknowledged=False)
    assert raised.value.status_code == 409
    assert item.item_code in raised.value.detail
    assert f"listing #{listing.id}" in raised.value.detail


def test_an_acknowledged_caller_is_let_through(db: Session, listing: Listing) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    sale_state.guard(db, [item], acknowledged=True)


def test_kinds_narrows_which_reasons_count(db: Session, listing: Listing) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    # The only reason this item is for sale is a listing, so asking about
    # orders alone finds nothing to refuse.
    sale_state.guard(db, [item], acknowledged=False, kinds={"order"})
    with pytest.raises(HTTPException):
        sale_state.guard(db, [item], acknowledged=False, kinds={"listing"})


def test_no_items_is_not_a_refusal(db: Session) -> None:
    sale_state.guard(db, [], acknowledged=False)


def test_errors_on_a_listed_item_are_refused_until_acknowledged(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item_id = listing.inventory_item_id
    body = {"errors": [{"error_type": "off_center", "details": None}]}

    refused = client.put(
        f"/api/inventory/{item_id}/errors", json=body, headers=admin_headers
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]
    unchanged = client.get(
        f"/api/inventory/{item_id}/errors", headers=admin_headers
    ).json()
    assert unchanged["errors"] == []

    made = client.put(
        f"/api/inventory/{item_id}/errors",
        json={**body, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert made.status_code == 200, made.text
    assert [e["error_type"] for e in made.json()["errors"]] == ["off_center"]


def _two_pieces() -> list[dict[str, object]]:
    return [
        {"source_title": "Piece one", "piece_count": 1},
        {"source_title": "Piece two", "piece_count": 1},
    ]


def test_splitting_a_listed_lot_is_refused_until_acknowledged(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item_id = listing.inventory_item_id
    body = {"mode": "equal", "pieces": _two_pieces()}

    refused = client.post(
        f"/api/inventory/{item_id}/split", json=body, headers=admin_headers
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    made = client.post(
        f"/api/inventory/{item_id}/split",
        json={**body, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert made.status_code == 200, made.text


def test_an_order_refuses_a_split_that_cannot_be_acknowledged(
    client: TestClient,
    db: Session,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    ordered = client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=customer_headers,
    )
    assert ordered.status_code == 201, ordered.text

    # Acknowledging is not a way past an order: `app.splitting` refuses it
    # outright, and the guard deliberately does not offer to override that.
    refused = client.post(
        f"/api/inventory/{listing.inventory_item_id}/split",
        json={
            "mode": "equal",
            "pieces": _two_pieces(),
            "acknowledge_for_sale": True,
        },
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert "appears in an order" in refused.json()["detail"]


def test_kinds_listing_lets_an_ordered_but_unlisted_split_reach_split_item(
    client: TestClient,
    db: Session,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """`kinds={"listing"}` is load-bearing on its own, not just alongside a save.

    Order every unit the listing has: `sale_state.for_sale`'s listing branch
    requires `quantity_available > 0`, so at zero the listing stops counting
    as a listing-use while the still-open order keeps counting as an
    order-use. The guard, narrowed to `{"listing"}`, must find nothing here
    and let the (unacknowledged) request through silently -- so the 409 that
    follows comes from `split_item`'s own unconditional order check, not
    from `sale_state.refusal()`. If the `kinds` filter were ever dropped, the
    guard would refuse first with its own "For sale" message instead.
    """
    ordered = client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 5}]},
        headers=customer_headers,
    )
    assert ordered.status_code == 201, ordered.text
    db.expire_all()
    assert db.get(Listing, listing.id).quantity_available == 0

    refused = client.post(
        f"/api/inventory/{listing.inventory_item_id}/split",
        json={"mode": "equal", "pieces": _two_pieces()},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "appears in an order" in detail
    assert "For sale" not in detail
