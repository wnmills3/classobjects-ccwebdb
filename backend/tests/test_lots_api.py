"""The sales-lot endpoints: admin-only, optimistic, and money as strings."""

from __future__ import annotations

from collections.abc import Callable

from app.models import InventoryItem, Listing, SalesLot, SalesVenue
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


def test_a_lot_can_be_created_and_read_back(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The round trip, so every test below starts from something real."""
    created = client.post(
        "/api/sales-lots",
        headers=admin_headers,
        json={"title": "Three Morgans", "description": "Lightly toned"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "assembling"
    assert body["members"] == []

    listed = client.get("/api/sales-lots", headers=admin_headers).json()
    assert [row["id"] for row in listed["lots"]] == [body["id"]]


def test_membership_changes_need_the_current_version(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    lot_of_three: SalesLot,
    make_item: ItemFactory,
) -> None:
    """Optimistic locking, as everywhere else in the console: 409 on a stale token.

    The second PATCH is what this is really about. A membership change writes
    `sales_lot_item` and never `sales_lot`, so nothing moves
    `sales_lot.version` unless the router does it on purpose
    (`lot_writes.touch`). Without that call the first PATCH leaves the
    version exactly where it was, the second one's stale token still matches,
    and two people editing the same lot from the same loaded form are never
    told -- which is the failure this test exists to catch, not the 409
    itself.
    """
    db.commit()
    stale = lot_of_three.version
    joiner = make_item(title="A fourth coin")
    db.commit()

    first = client.patch(
        f"/api/sales-lots/{lot_of_three.id}",
        headers=admin_headers,
        json={"version": stale, "add_item_ids": [joiner.id]},
    )
    assert first.status_code == 200, first.text
    members = first.json()["members"]
    assert len(members) == 4
    # `open_members`' order, the one sequence the whole system agrees on.
    assert [row["inventory_item_id"] for row in members] == sorted(
        row["inventory_item_id"] for row in members
    )

    second = client.patch(
        f"/api/sales-lots/{lot_of_three.id}",
        headers=admin_headers,
        json={"version": stale, "remove_item_ids": [joiner.id]},
    )
    assert second.status_code == 409
    assert "changed" in second.json()["detail"].lower()


def test_editing_an_offered_lot_is_refused(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """409, naming the listing that froze it."""
    db.commit()
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    response = client.patch(
        f"/api/sales-lots/{lot.id}",
        headers=admin_headers,
        json={"version": lot.version, "title": "Renamed after the fact"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "offered" in detail
    # The listing, not just the status: ending that offer is the one thing
    # that unfreezes the lot, and a message that named only the lot would
    # leave an administrator hunting for which offer to end.
    assert f"listing #{offered_lot_listing.id}" in detail


def test_an_empty_lot_cannot_be_offered(
    client: TestClient, admin_headers: dict[str, str], ebay_venue: SalesVenue
) -> None:
    """422, not 409: the spec's *Errors* list calls an empty lot bad input.

    **The body is asserted, not only the status.** A 422 alone proves
    nothing here: pydantic answers 422 for a malformed offer body too, so a
    status-only assertion passes whether or not `create_offers` has ever
    heard of `EmptyLot` -- and it would keep passing if the `except
    EmptyLot` clause were moved below `except LotRefused`, which turns every
    422 of this kind into a 409. Naming the lot in the detail is what only
    `offering_writes._lot_members`' own message does, so this assertion goes
    red for both of those.

    The request is otherwise complete -- a real venue, a real format, a
    price -- so the empty lot is the only thing wrong with it. A body
    pydantic would reject on its own could never reach the clause under
    test.
    """
    lot_id = client.post(
        "/api/sales-lots",
        headers=admin_headers,
        json={"title": "Nothing in it", "description": ""},
    ).json()["id"]
    response = client.post(
        "/api/offers",
        headers=admin_headers,
        json={
            "venue": "ebay",
            "format": "fixed_price",
            "lot_id": lot_id,
            "price": "1000.00",
            "title": "Nothing in it",
        },
    )
    assert response.status_code == 422, response.text
    assert f"lot #{lot_id}" in response.json()["detail"]


def test_offering_a_frozen_lot_is_a_conflict_not_bad_input(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """The other half of the `except` order: a plain `LotRefused` is 409.

    `EmptyLot` subclasses `LotRefused`, so the two clauses can only be told
    apart by what each answers. With this case and the empty-lot one above,
    swapping them turns exactly one of the two red -- which is the most a
    test can do about an ordering the type system cannot see.
    """
    db.commit()
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    response = client.post(
        "/api/offers",
        headers=admin_headers,
        json={
            "venue": "ebay",
            "format": "fixed_price",
            "lot_id": lot.id,
            "price": "1100.00",
            "title": "Three Morgan Dollars",
        },
    )
    assert response.status_code == 409, response.text
    assert f"lot #{lot.id}" in response.json()["detail"]


def test_a_lot_body_cannot_also_carry_items(
    client: TestClient, admin_headers: dict[str, str], lot_of_three: SalesLot
) -> None:
    """Both subjects at once is what makes `offer`'s `ValueError` unreachable."""
    response = client.post(
        "/api/offers",
        headers=admin_headers,
        json={
            "venue": "ebay",
            "lot_id": lot_of_three.id,
            "price": "10.00",
            "items": [{"item_id": 1, "price": "10.00"}],
        },
    )
    assert response.status_code == 422, response.text


def test_a_lot_cannot_be_offered_as_more_than_one_unit(
    client: TestClient, admin_headers: dict[str, str], lot_of_three: SalesLot
) -> None:
    """`ck_listing_lot_quantity_one` caps a lot listing at one unit.

    `offering_writes.offer` forces `quantity_available=1` for a lot, so an
    explicit `2` would be accepted and quietly ignored -- the caller would
    believe two lots were on offer. Refused instead, now that a caller can
    reach the override at all.
    """
    response = client.post(
        "/api/offers",
        headers=admin_headers,
        json={
            "venue": "ebay",
            "lot_id": lot_of_three.id,
            "price": "1000.00",
            "quantity": 2,
        },
    )
    assert response.status_code == 422, response.text


def test_the_lot_list_is_admin_only(
    client: TestClient, db: Session, lot_of_three: SalesLot
) -> None:
    """Cost basis is on the row; an anonymous caller gets 401."""
    db.commit()
    assert client.get("/api/sales-lots").status_code == 401


def test_a_customer_cannot_read_a_lot(
    client: TestClient,
    customer_headers: dict[str, str],
    db: Session,
    lot_of_three: SalesLot,
) -> None:
    """403 for a signed-in non-administrator: 401 alone would not prove the role.

    The fixture is `customer_headers`, not an invented `customer_token` --
    conftest has no such fixture.
    """
    db.commit()
    assert client.get("/api/sales-lots", headers=customer_headers).status_code == 403


def test_money_fields_are_strings(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    lot_of_three: SalesLot,
) -> None:
    """A Decimal in a plain dict becomes a float; these must not.

    The fixture's three members cost 500, 300 and 200 with no tax, so the
    running cost basis is exactly 1000.00 -- a figure a float would render as
    1000.0 and this assertion would catch.
    """
    db.commit()
    row = client.get("/api/sales-lots", headers=admin_headers).json()["lots"][0]
    assert row["cost_basis"] == "1000.00"
    assert isinstance(row["cost_basis"], str)
    # None of the three has been valued, so the running value is a floor of
    # zero over three unpriced coins rather than a total anyone should trust.
    assert row["value"] == "0.00"
    assert row["unvalued_count"] == 3


def test_an_assembling_lot_can_be_deleted_and_an_offered_one_cannot(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """A lot that was offered is a record of what was tried; it stays."""
    db.commit()
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    assert (
        client.delete(f"/api/sales-lots/{lot.id}", headers=admin_headers).status_code
        == 409
    )
    spare = client.post(
        "/api/sales-lots",
        headers=admin_headers,
        json={"title": "Never offered", "description": ""},
    ).json()
    assert (
        client.delete(
            f"/api/sales-lots/{spare['id']}", headers=admin_headers
        ).status_code
        == 204
    )
    assert (
        client.get(f"/api/sales-lots/{spare['id']}", headers=admin_headers).status_code
        == 404
    )


def test_an_item_cannot_be_added_and_removed_in_one_request(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    lot_of_three: SalesLot,
    make_item: ItemFactory,
) -> None:
    """Ambiguous, not redundant: either order of applying the two disagrees."""
    db.commit()
    joiner = make_item(title="Both at once")
    db.commit()
    response = client.patch(
        f"/api/sales-lots/{lot_of_three.id}",
        headers=admin_headers,
        json={
            "version": lot_of_three.version,
            "add_item_ids": [joiner.id],
            "remove_item_ids": [joiner.id],
        },
    )
    assert response.status_code == 422, response.text
    assert str(joiner.id) in response.json()["detail"]
