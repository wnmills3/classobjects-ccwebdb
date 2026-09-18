"""The warning before a change to an item that is for sale.

One file for the whole policy: `app.sale_state.guard` and each endpoint that
calls it. The existing coverage of the two original call sites lives in
`test_sale_snapshots.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app import offering_writes, sale_state
from app.models import (
    ClaimState,
    InventoryItem,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    OfferClaim,
    SalesVenue,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item
from tests.test_images import make_jpeg


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


def test_marking_a_listed_item_missing_is_refused_until_acknowledged(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item_id = listing.inventory_item_id
    body = {"item_ids": [item_id], "outcome": "missing"}

    refused = client.post("/api/inventory/receive", json=body, headers=admin_headers)
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]
    item = db.get(InventoryItem, item_id)
    assert item is not None
    db.refresh(item)
    assert item.status.code == "received"


def test_receiving_an_item_that_is_not_for_sale_asks_nothing(
    client: TestClient,
    db: Session,
    make_item: Callable[..., InventoryItem],
    admin_headers: dict[str, str],
) -> None:
    # `make_item` (tests.conftest.build_item) starts an item at
    # `received`, same as after a real receipt -- so a second "received"
    # outcome here would collide with the pre-existing "already received"
    # 409, which has nothing to do with the for-sale guard this test covers.
    # Moved to `ordered`, matching how `test_receiving.py` starts every one
    # of its own receiving tests.
    item = make_item()
    item.status_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()
    db.commit()
    made = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received"},
        headers=admin_headers,
    )
    assert made.status_code == 200, made.text


def test_an_acknowledged_missing_ends_the_listing_and_releases_the_claim(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    # Built through `offering_writes.offer` rather than `build_listing`,
    # because the fixture makes a CLAIMLESS listing: it would prove the
    # listing ended while saying nothing about the claim.
    item = build_item(db)
    venue = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).first()
    assert venue is not None
    made = offering_writes.offer(
        db,
        item=item,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("50.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()

    response = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "missing",
            "acknowledge_for_sale": True,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    db.expire_all()
    ended = db.get(Listing, made.id)
    assert ended is not None
    assert ended.status is ListingStatus.ended
    # `offering_writes.claims_for` filters through `_holding_claims()`, whose
    # WHERE joins on `Listing.status.in_(ON_OFFER)` -- once the listing above
    # is `ended`, its claim drops out of that query regardless of the claim's
    # own state, so asking `claims_for` here would prove nothing beyond what
    # `ended.status` already showed. Read the `OfferClaim` row directly.
    claim = db.scalars(select(OfferClaim).where(OfferClaim.listing_id == made.id)).one()
    assert claim.state is ClaimState.released
    refreshed = db.get(InventoryItem, item.id)
    assert refreshed is not None
    assert refreshed.status.code == "missing"


def test_an_acknowledged_missing_persists_the_status_without_autoflush(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    """The receipt's own status change survives the listing being ended.

    Deliberately run the way production runs. `SessionLocal`
    (app/database.py) sets `autoflush=False`; the test session above does
    not, so it flushes on every query. That difference is the whole test:
    `receive_items` assigns `item.status_id` and then ends the listing
    through `offering_writes.end_offer`, which locks the same rows with
    `populate_existing=True`. With autoflush on, the lock query flushes the
    assignment first and it survives; with autoflush off -- production --
    the re-read overwrites the pending value and clears its dirty flag, and
    the commit writes the history row and the ended listing while leaving
    the item still `received`. `receive_items` flushes before ending the
    offer to close that, and this test fails without that flush.
    """
    item_id = listing.inventory_item_id

    db.autoflush = False
    try:
        response = client.post(
            "/api/inventory/receive",
            json={
                "item_ids": [item_id],
                "outcome": "missing",
                "acknowledge_for_sale": True,
            },
            headers=admin_headers,
        )
    finally:
        db.autoflush = True
    assert response.status_code == 200, response.text

    # Read the row back rather than trusting the in-memory object: the bug
    # this covers is a value that never reaches the database, which an
    # unexpired identity-map entry would happily keep reporting as changed.
    db.expire_all()
    stored = db.get(InventoryItem, item_id)
    assert stored is not None
    assert stored.status.code == "missing"

    ended = db.get(Listing, listing.id)
    assert ended is not None
    assert ended.status is ListingStatus.ended


def test_attaching_a_photograph_to_a_listed_item_is_refused(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    files = {"file": ("coin.jpg", make_jpeg(), "image/jpeg")}
    refused = client.post(
        "/api/images",
        files=files,
        data={"inventory_item_id": str(listing.inventory_item_id)},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    made = client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg(), "image/jpeg")},
        data={
            "inventory_item_id": str(listing.inventory_item_id),
            "acknowledge_for_sale": "true",
        },
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text


def test_an_unattached_photograph_is_never_refused(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    made = client.post(
        "/api/images",
        files={"file": ("loose.jpg", make_jpeg(), "image/jpeg")},
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text


def test_deleting_a_photograph_of_a_listed_item_is_refused(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    attached = client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg(), "image/jpeg")},
        data={
            "inventory_item_id": str(listing.inventory_item_id),
            "acknowledge_for_sale": "true",
        },
        headers=admin_headers,
    )
    assert attached.status_code == 201, attached.text
    image_id = attached.json()["id"]

    refused = client.delete(f"/api/images/{image_id}", headers=admin_headers)
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    gone = client.delete(
        f"/api/images/{image_id}?acknowledge_for_sale=true", headers=admin_headers
    )
    assert gone.status_code == 204, gone.text


def test_a_merge_reports_the_items_for_sale_then_refuses(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    # `grade` codes are the bare Sheldon number ("64"), not the compound
    # form a collector writes ("MS64") -- app.grades.split is what turns one
    # into the other, and this table's own values are already numeric.
    code = item.grade.code
    into = "63" if code != "63" else "62"

    preview = client.post(
        f"/api/reference/grade/{code}/merge",
        json={"into": into, "dry_run": True},
        headers=admin_headers,
    )
    assert preview.status_code == 200, preview.text
    assert item.item_code in preview.json()["for_sale"]
    assert preview.json()["for_sale_count"] >= 1

    refused = client.post(
        f"/api/reference/grade/{code}/merge",
        json={"into": into, "dry_run": False},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    made = client.post(
        f"/api/reference/grade/{code}/merge",
        json={"into": into, "dry_run": False, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert made.status_code == 200, made.text
