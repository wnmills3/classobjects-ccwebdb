"""The warning before a change to an item that is for sale.

One file for the whole policy: `app.sale_state.guard` and each endpoint that
calls it. The existing coverage of the two original call sites lives in
`test_sale_snapshots.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app import offering_writes, order_writes, sale_state
from app.models import (
    ClaimState,
    Customer,
    InventoryItem,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    OfferClaim,
    SalesOrderItemShare,
    SalesOrderStatus,
    SalesVenue,
    User,
)
from app.sales_writes import record_sale
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item, item_id_of
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
    assert db.get_one(Listing, listing.id).quantity_available == 0

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


def _offered_item(db: Session) -> tuple[InventoryItem, Listing]:
    """A received item offered in the shop, with its claim, committed."""
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
    return item, made


def test_editing_the_status_of_an_offered_item_is_refused_until_acknowledged(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    item, made = _offered_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}", json={"status": "missing"}, headers=admin_headers
    )
    assert response.status_code == 409, response.text
    assert _listing_status(db, made.id) is ListingStatus.active


def _listing_status(db: Session, listing_id: int) -> ListingStatus:
    db.expire_all()
    listing = db.get(Listing, listing_id)
    assert listing is not None
    return listing.status


@pytest.mark.parametrize("autoflush", [True, False])
def test_an_acknowledged_status_edit_ends_the_offer(
    client: TestClient, db: Session, admin_headers: dict[str, str], autoflush: bool
) -> None:
    """Marking an offered coin missing in the editor takes it off sale.

    It used to change the status and leave the listing active, so the shop
    went on selling a coin marked missing (code review, 2026-09-23). Run
    both ways: production's session does not autoflush, and ending an offer
    re-reads the item, which once threw a pending status change away.
    """
    item, made = _offered_item(db)
    db.autoflush = autoflush
    try:
        response = client.patch(
            f"/api/inventory/{item.id}",
            json={"status": "missing", "acknowledge_for_sale": True},
            headers=admin_headers,
        )
    finally:
        db.autoflush = True
    assert response.status_code == 200, response.text

    db.expire_all()
    ended = db.get(Listing, made.id)
    assert ended is not None
    assert ended.status is ListingStatus.ended
    claim = db.scalars(select(OfferClaim).where(OfferClaim.listing_id == made.id)).one()
    assert claim.state is ClaimState.released
    refreshed = db.get(InventoryItem, item.id)
    assert refreshed is not None
    assert refreshed.status.code == "missing"


def test_an_acknowledged_edit_that_leaves_status_alone_keeps_the_offer(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """Only a change of status or disposition ends an offer, not any edit."""
    item, made = _offered_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={
            "description": "A sharper strike than most",
            "status": "received",  # sent, but unchanged
            "acknowledge_for_sale": True,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert _listing_status(db, made.id) is ListingStatus.active


def test_an_acknowledged_bulk_status_edit_ends_each_offer(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """The bulk bar changes status for many items: the same rule, per item."""
    first, first_listing = _offered_item(db)
    second, second_listing = _offered_item(db)
    response = client.post(
        "/api/inventory/bulk",
        json={
            "ids": [first.id, second.id],
            "changes": {"status": "missing", "acknowledge_for_sale": True},
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert _listing_status(db, first_listing.id) is ListingStatus.ended
    assert _listing_status(db, second_listing.id) is ListingStatus.ended


def test_an_acknowledged_disposition_edit_ends_the_offer_too(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """A listed coin set back to held would leave a live claim on a held item."""
    item, made = _offered_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"disposition": "held", "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert _listing_status(db, made.id) is ListingStatus.ended


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
    assert item.grade is not None
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


def test_a_missing_piece_ends_the_lot_listing_that_held_it(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """A piece has no listing of its own; the lot's listing is what holds it.

    `receive_items` asked only for listings whose `inventory_item_id` is one
    of the received items, so a piece going missing left the lot still on
    sale -- offering something that can no longer be delivered, which is the
    exact thing the comment above that query promises not to do.

    No write path builds this state yet (lot offers are phase 3), so the
    claim is made directly. `offering_writes` already handles it everywhere
    else, which is why the inconsistency is worth closing before the write
    path arrives rather than after.
    """
    lot = build_item(db)
    piece = build_item(db)
    venue = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).first()
    assert venue is not None
    made = offering_writes.offer(
        db,
        item=lot,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("120.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    # The lot's listing also holds the piece.
    db.add(
        OfferClaim(
            inventory_item_id=piece.id, listing_id=made.id, state=ClaimState.active
        )
    )
    db.commit()

    response = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [piece.id],
            "outcome": "missing",
            "acknowledge_for_sale": True,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    db.expire_all()
    ended = db.get_one(Listing, made.id)
    assert ended.status is ListingStatus.ended, "the lot must come off sale"


def test_an_item_sold_through_a_finished_sale_still_warns(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """A sold coin's record is what a buyer was shown; editing it needs care.

    Pins `record_sale`'s end-to-end path, through a claim that is `released`
    and a listing that is `ended`: an `"order"` use is still reachable for
    the item afterwards. **Not the mutation-discriminating case** -- the old
    direct-link query carried no listing-status filter and read
    `Listing.inventory_item_id`, which an ended listing still has, so it
    already found this item too; only a lot's member, which the direct link
    can never name, tells the two queries apart
    (`test_a_lot_pieces_share_reaches_the_piece_the_direct_link_cannot`).
    """
    item_id = item_id_of(ebay_listing)
    record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    uses = sale_state.for_sale(db, [item_id])
    assert any(use.kind == "order" for use in uses[item_id])


def test_a_delivered_auction_house_sale_does_not_warn(
    db: Session, heritage_listing: Listing, admin_user: User
) -> None:
    """Delivered is deliberately excluded, the same as shipped already is.

    `OPEN_ORDER_STATUSES` is `{pending, paid, packed}` -- this module's own
    docstring says an order that has shipped stops warning, because its line
    keeps a snapshot of the item as sold and the live record is no longer
    what a buyer is looking at. An auction house has already shipped for us
    by the time its sale is recorded (`sales_writes._STATUS_BY_VENUE_KIND`
    starts the order at `delivered`, past every status in between), so this
    is that same rule reached one step further along, not a new exception
    carved out for it.

    A bare `item_id not in uses` would also pass if the order half returned
    nothing at all -- indistinguishable from a broken join -- so the order
    and its share are read back directly first, to show the mechanism did
    run and produce real rows; only the status filter is what then excludes
    them from `for_sale`.
    """
    item_id = heritage_listing.inventory_item_id
    order = record_sale(
        db,
        heritage_listing,
        price=Decimal("500.00"),
        buyer_username=None,
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    status = db.get(SalesOrderStatus, order.sales_order_status_id)
    assert status is not None
    assert status.code == "delivered"
    share = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == order.items[0].id
        )
    ).one()
    assert share.inventory_item_id == item_id

    uses = sale_state.for_sale(db, [item_id])
    assert item_id not in uses


def test_a_lot_pieces_share_reaches_the_piece_the_direct_link_cannot(
    db: Session, admin_user: User
) -> None:
    """The one case that actually tells the two queries apart.

    Every write path today (`order_writes._sync_shares`) keys a line's share
    by `listing.inventory_item_id`, so a single-item listing's order is found
    exactly as well by the old direct-link query as by the new share query --
    neither `test_an_item_sold_through_a_finished_sale_still_warns` above nor
    a delivered auction sale distinguishes them. Only a lot's member does: the
    listing's own `inventory_item_id` names the lot, never a piece, so the
    piece is findable only through its share. No write path divides a lot's
    line among its members yet (phase 3), so `place_order`'s own
    lot-shaped share (naming the lot, per today's one-item-per-listing rule)
    is repointed to the piece directly here, the same reason
    `test_a_missing_piece_ends_the_lot_listing_that_held_it` above adds its
    claim directly.
    """
    lot = build_item(db)
    piece = build_item(db)
    venue = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).first()
    assert venue is not None
    made = offering_writes.offer(
        db,
        item=lot,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("200.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.flush()
    buyer = Customer(display_name="Walk-in Buyer", email=None)
    db.add(buyer)
    db.flush()
    order = order_writes.place_order(
        db,
        buyer,
        [
            order_writes.Line(
                listing_id=made.id, quantity=1, unit_price=Decimal("200.00")
            )
        ],
        admin_user,
    )
    line = order.items[0]
    # Repointed, not added alongside: phase 3 divides a line's money among
    # its members with no share left for the lot itself, and a second share
    # here would leave the line's shares summing to 400.00 against its own
    # 200.00 (`SalesOrderItemShare`'s own docstring: "shares sum to their
    # line exactly"). Same discriminating shape -- a piece findable only
    # through a share -- without manufacturing a state the invariant forbids.
    lot_share = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == line.id
        )
    ).one()
    lot_share.inventory_item_id = piece.id
    db.commit()

    uses = sale_state.for_sale(db, [piece.id])
    assert any(use.kind == "order" for use in uses[piece.id])


def test_an_offered_lot_s_member_warns_like_a_listed_item(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """Spec: an item in an offered lot warns like a listed one.

    Reached through the API, because the warning is a 409 an operator meets
    on save, not a function's return value. The mutation that proves it:
    delete the `claims_for` half of `sale_state._offering` and confirm this
    goes red -- the direct half never matches a lot listing, whose
    `inventory_item_id` is NULL.

    The route is `PATCH /api/inventory/{id}`: `routers.inventory` declares
    `@router.patch("/{item_id}")` on a router whose prefix is already
    `/inventory`, so `/api/inventory/items/{id}` is not a route at all and
    would answer 404 -- a status-only assertion there would say nothing
    about the guard.
    """
    db.commit()
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_id = lot.members[0].inventory_item_id

    response = client.patch(
        f"/api/inventory/{member_id}",
        headers=admin_headers,
        json={"source_title": "Renamed while offered"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith("For sale")
    assert f"listing #{offered_lot_listing.id}" in detail


def test_a_sold_lot_s_member_still_warns_while_the_order_is_open(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
    admin_user: User,
) -> None:
    """After the sale the claim is released, so only the share can find it.

    This is the rule 2R decided and `sale_state`'s order half implements
    (`SalesOrderItemShare.inventory_item_id`). A lot is the case that half
    exists for, and nothing tested it with a real lot until now. The mutation
    that proves it: join the order half through `listing.inventory_item_id`
    instead of through the share, and confirm this goes red.

    The status matters: `record_sale` on a `marketplace` venue creates the
    order `paid`, which is in `OPEN_ORDER_STATUSES`. A `heritage_venue` sale
    would be `delivered` and would deliberately not warn.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_id = lot.members[0].inventory_item_id
    record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    db.commit()

    response = client.patch(
        f"/api/inventory/{member_id}",
        headers=admin_headers,
        json={"source_title": "Renamed after the sale"},
    )
    assert response.status_code == 409
    assert "order #" in response.json()["detail"]


def test_a_lot_sale_refuses_a_split_of_the_member_it_sold(
    client: TestClient,
    db: Session,
    offered_lot_listing: Listing,
    admin_headers: dict[str, str],
    admin_user: User,
) -> None:
    """The lot twin of the "appears in an order" refusal above.

    `split_item`'s order check asked `listing.inventory_item_id`, which is
    NULL on a lot listing -- so a coin sold inside a lot passed it and was
    split, silently. That is the one shape of this branch's nullable-column
    fallout that costs money: the line's `sales_order_item_share` still
    credits the parent, whose `item_cost` has just been re-allocated to two
    children, so realised gain and cost basis double-count with no error
    anywhere. The plausible route is a returned tube or mint set that went
    out inside a lot and is now being broken up.

    Neither guard catches it without the fix. `sale_state.guard` passes
    `kinds={"listing"}`, and the sale released the claims and ended the
    listing, so its listing half finds nothing and its order half is
    filtered out -- which is why "For sale" is asserted *absent* here: the
    refusal has to come from `split_item` itself, as it does for a coin sold
    on its own.

    Acknowledging is asserted to be no way past it either, for the reason
    the single-item twin asserts the same: the refusal is not negotiable.

    The mutation that proves it: restore the old
    `where(Listing.inventory_item_id == parent.id)` in `app.splitting` and
    confirm this goes red on the status, the message and the split state.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_id = lot.members[0].inventory_item_id
    record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-2",
        fees=[],
        recorded_by=admin_user,
    )
    db.commit()
    body = {"mode": "equal", "pieces": _two_pieces()}

    refused = client.post(
        f"/api/inventory/{member_id}/split", json=body, headers=admin_headers
    )

    assert refused.status_code == 409, refused.text
    detail = refused.json()["detail"]
    assert "appears in an order" in detail
    assert "For sale" not in detail

    acknowledged = client.post(
        f"/api/inventory/{member_id}/split",
        json={**body, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert acknowledged.status_code == 409, acknowledged.text
    assert "appears in an order" in acknowledged.json()["detail"]

    # And nothing was written: the member is unsplit and still carries the
    # whole cost basis the sold line's share credits it with.
    db.expire_all()
    member = db.get(InventoryItem, member_id)
    assert member is not None
    assert member.split_at is None
    assert member.item_cost == Decimal("500.00")
    pieces = db.scalars(
        select(InventoryItem.id).where(InventoryItem.parent_item_id == member_id)
    ).all()
    assert list(pieces) == []
