"""Deleting a row that should never have existed.

Guarded, because the two ways it goes wrong are both silent: deleting a lot
whose pieces then reference nothing, and deleting something a customer has
already bought.
"""

from __future__ import annotations

from app import lot_writes, offering_writes
from app.models import InventoryItem, Listing, SalesLot, SalesLotStatus
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import TUBE, build_bare_item, build_split_lot, do_split
from tests.conftest import build_listing


def test_a_deleted_item_leaves_search(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db, source_title="MISTAKE")

    assert (
        client.delete(f"/api/inventory/{item.id}", headers=admin_headers).status_code
        == 204
    )

    rows = client.get(
        "/api/inventory/coins/search?q=MISTAKE", headers=admin_headers
    ).json()["rows"]
    assert rows == []


def test_a_deleted_item_is_findable_when_asked_for(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Otherwise a mistaken delete is unrecoverable through the UI."""
    item = build_bare_item(db, source_title="MISTAKE")
    client.delete(f"/api/inventory/{item.id}", headers=admin_headers)

    only = client.get(
        "/api/inventory/coins/search?q=MISTAKE&deleted=only", headers=admin_headers
    ).json()
    assert [r["id"] for r in only["rows"]] == [item.id]

    both = client.get(
        "/api/inventory/coins/search?q=MISTAKE&deleted=any", headers=admin_headers
    ).json()
    assert both["total"] == 1


def test_an_unrecognised_deleted_mode_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Consistent with every other filter: never silently ignored."""
    response = client.get(
        "/api/inventory/coins/search?deleted=maybe", headers=admin_headers
    )
    assert response.status_code == 422


def test_the_lot_filter_finds_a_lot_s_pieces(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Everything split from one lot -- the review queue's entry point.

    By item code rather than id: the code is what is printed on the flip and
    what a person has in front of them.
    """
    parent = build_split_lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]
    build_bare_item(db, source_title="unrelated")

    body = client.get(
        f"/api/inventory/coins/search?lot={parent.item_code}", headers=admin_headers
    ).json()

    assert sorted(r["id"] for r in body["rows"]) == sorted(p["id"] for p in pieces)


def test_an_unknown_lot_code_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Returning nothing would read as 'that lot has no pieces'.

    That is a different and much more alarming answer than 'no such lot'.
    """
    response = client.get(
        "/api/inventory/coins/search?lot=CC-999999", headers=admin_headers
    )
    assert response.status_code == 422
    assert "CC-999999" in response.json()["detail"]


def test_a_lot_with_pieces_cannot_be_deleted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Its pieces hold cost basis allocated from it and would be orphaned."""
    parent = build_split_lot(db)
    do_split(client, admin_headers, parent.id, TUBE)

    response = client.delete(f"/api/inventory/{parent.id}", headers=admin_headers)
    assert response.status_code == 409
    assert "piece" in response.json()["detail"].lower()


def test_a_listed_item_cannot_be_deleted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A listing is what an order line points at, through to the sale.

    Guarding on the listing rather than on the order is the wider net and the
    cheaper query: an item cannot reach an order without one.
    """
    listing = build_listing(db)
    response = client.delete(
        f"/api/inventory/{listing.inventory_item_id}", headers=admin_headers
    )
    assert response.status_code == 409
    assert "listing" in response.json()["detail"].lower()


def test_an_ended_listing_still_refuses_and_the_message_says_so(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The refusal is permanent, and must not name a step that cannot clear it.

    Ending an offer leaves the listing row in place, and nothing removes one
    -- the catalog's delete endpoint was retired in phase 2. So "withdraw
    the listing first" was a remedy that could never work. The rule itself is
    right: once a coin has been offered the offer is part of the sales
    history, so the message has to state that instead.
    """
    listing = build_listing(db, is_active=False)
    response = client.delete(
        f"/api/inventory/{listing.inventory_item_id}", headers=admin_headers
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "permanently" in detail
    assert "withdraw" not in detail.lower()


def test_an_item_in_an_assembling_lot_cannot_be_deleted(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    lot_of_three: SalesLot,
) -> None:
    """A lot listing names no item, so the listing guard never saw a member.

    `delete_item` asked two questions -- pieces split from the item, and
    `Listing.inventory_item_id == item.id` -- and a lot's listing carries a
    null `inventory_item_id`, so a coin in a lot passed both and was soft
    deleted out from under the group it belongs to. Nothing else in
    `delete_item` looked at memberships or claims.

    The assertions below pin *why* this 409 happens: neither of the two old
    guards matches, so a refusal can only come from the lot-aware one.
    """
    db.commit()
    members = lot_writes.open_members(db, lot_of_three)
    member_id = members[0].inventory_item_id
    assert lot_of_three.status is SalesLotStatus.assembling
    item = db.get_one(InventoryItem, member_id)
    assert item.parent_item_id is None
    assert db.query(Listing).filter_by(inventory_item_id=member_id).count() == 0

    response = client.delete(f"/api/inventory/{member_id}", headers=admin_headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert f"lot #{lot_of_three.id}" in detail
    # A remedy that works: an assembling lot's membership really can be
    # removed, unlike an offer, which is why this message is not the
    # permanent one below.
    assert "permanently" not in detail
    db.expire_all()
    assert db.get_one(InventoryItem, member_id).deleted_at is None


def test_an_offered_lot_s_member_cannot_be_deleted(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """An open membership in an *offered* lot must not earn the clearable message.

    `lot_writes.lot_holding` matches any lot with an open membership --
    `offered` included, not only `assembling` -- so this member matches both
    of `delete_item`'s guards. The permanent one has to win: `remove_member`
    refuses every lot state but `assembling`
    (`lot_writes._refuse_unless_assembling`), so "Take it out of the lot
    first" would name a step this coin's lot cannot take. This is the
    discriminating half of the pair with
    `test_an_item_in_an_assembling_lot_cannot_be_deleted`: swap
    `delete_item`'s two guard blocks and this test goes red while that one,
    whose lot never matches the permanent guard, stays green.
    """
    sales_lot = offered_lot_listing.sales_lot
    assert sales_lot is not None
    member_id = lot_writes.open_members(db, sales_lot)[0].inventory_item_id

    # The state that makes this the discriminating case: an open membership
    # (so `lot_holding` matches) in a lot that is currently `offered` (so
    # `ever_offered` also matches) -- unlike the assembling case above, where
    # only `lot_holding` matches, and the dissolved case below, where only
    # `ever_offered` does.
    assert sales_lot.status is SalesLotStatus.offered
    assert lot_writes.open_members(db, sales_lot) != []

    response = client.delete(f"/api/inventory/{member_id}", headers=admin_headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "permanently" in detail
    assert "Take it out of the lot first" not in detail
    db.expire_all()
    assert db.get_one(InventoryItem, member_id).deleted_at is None


def test_a_dissolved_lot_s_member_cannot_be_deleted(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """Having been offered inside a lot is offer history, and it is permanent.

    The harder half of the same hole: once the lot is dissolved its
    memberships are released and its claims released too, so "is it in a lot
    now" answers no. The coin was still offered to buyers, and the record of
    that offer stays -- the same rule
    `test_an_ended_listing_still_refuses_and_the_message_says_so` pins for a
    direct listing.
    """
    sales_lot = offered_lot_listing.sales_lot
    assert sales_lot is not None
    member_id = lot_writes.open_members(db, sales_lot)[0].inventory_item_id
    offering_writes.end_offer(db, offered_lot_listing)
    db.commit()

    # The state that makes this the discriminating case: no open membership
    # left, and the listing names the lot rather than the coin -- so both the
    # old listing guard and a "in a lot right now" guard answer no.
    assert sales_lot.status is SalesLotStatus.dissolved
    assert lot_writes.open_members(db, sales_lot) == []
    assert offered_lot_listing.inventory_item_id is None

    response = client.delete(f"/api/inventory/{member_id}", headers=admin_headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "permanently" in detail
    assert "withdraw" not in detail.lower()
    db.expire_all()
    assert db.get_one(InventoryItem, member_id).deleted_at is None


def test_detaching_leaves_a_standalone_item(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """No parent is the normal state, not an orphan.

    Every item in the collection has none until a lot is split, so nothing
    may treat a null parent as a problem to be repaired.
    """
    parent = build_split_lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]
    child_id = pieces[0]["id"]

    response = client.delete(f"/api/inventory/{child_id}/parent", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["parent_item_id"] is None


def test_detaching_does_not_move_money(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A detached piece keeps the cost it was allocated."""
    parent = build_split_lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]
    before = pieces[0]["item_cost"]

    body = client.delete(
        f"/api/inventory/{pieces[0]['id']}/parent", headers=admin_headers
    ).json()

    assert body["item_cost"] == before


def test_detaching_an_item_with_no_parent_is_harmless(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Idempotent, because the end state is what was asked for."""
    item = build_bare_item(db)
    response = client.delete(f"/api/inventory/{item.id}/parent", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["parent_item_id"] is None


def test_a_lot_can_be_deleted_once_its_last_piece_is_detached(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The round trip the lot-with-pieces guard would otherwise make impossible."""
    parent = build_split_lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]

    for piece in pieces:
        client.delete(f"/api/inventory/{piece['id']}/parent", headers=admin_headers)

    assert (
        client.delete(f"/api/inventory/{parent.id}", headers=admin_headers).status_code
        == 204
    )
