"""The sales-lot endpoints: admin-only, optimistic, and money as strings."""

from __future__ import annotations

from collections.abc import Callable

import httpx
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


def test_the_lot_list_is_paged_newest_first_with_a_total(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The history only grows, so the list is a page plus how many there are."""
    ids = [
        client.post(
            "/api/sales-lots", headers=admin_headers, json={"title": f"Lot {n}"}
        ).json()["id"]
        for n in range(3)
    ]

    page = client.get(
        "/api/sales-lots", headers=admin_headers, params={"limit": 2}
    ).json()
    assert [row["id"] for row in page["lots"]] == [ids[2], ids[1]]
    assert page["total"] == 3

    rest = client.get(
        "/api/sales-lots", headers=admin_headers, params={"limit": 2, "offset": 2}
    ).json()
    assert [row["id"] for row in rest["lots"]] == [ids[0]]
    assert rest["total"] == 3


def test_membership_changes_need_the_current_version(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
    make_lot: Callable[..., SalesLot],
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

    **The joiner is made first, so it carries the lowest `inventory_item_id`
    of the four.** `lot_of_three` would have given it the highest, and the
    coin added last would then come back last whether or not `lots._out`
    sorted at all -- the ordering assertion below would have been comparing a
    list to a sorted copy of itself and could not fail. Built here instead:
    membership-row order and item-id order disagree, and the expected
    sequence is named rather than derived from the answer.
    """
    joiner = make_item(title="The oldest coin, added last")
    members = [make_item(title=f"Member {index}") for index in range(3)]
    lot = make_lot(members)
    db.commit()
    stale = lot.version
    expected = sorted([joiner.id, *(item.id for item in members)])
    assert expected[0] == joiner.id

    first = client.patch(
        f"/api/sales-lots/{lot.id}",
        headers=admin_headers,
        json={"version": stale, "add_item_ids": [joiner.id]},
    )
    assert first.status_code == 200, first.text
    # `open_members`' order, the one sequence the whole system agrees on --
    # and not the order the rows were written in, which puts `joiner` last.
    assert [row["inventory_item_id"] for row in first.json()["members"]] == expected

    second = client.patch(
        f"/api/sales-lots/{lot.id}",
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


def _pydantic_messages(response: httpx.Response) -> list[str]:
    """Every `msg` in a pydantic 422 body, so an assertion can name the fault.

    A request-validation 422 carries `detail` as a **list of error dicts**,
    not the string a router's own `HTTPException` produces. An assertion that
    does not go through this cannot tell the two apart -- which is exactly how
    two tests below passed on "Unknown venue: 'ebay'" while claiming to prove
    a schema rule.
    """
    detail = response.json()["detail"]
    assert isinstance(detail, list), detail
    return [str(error["msg"]) for error in detail]


def test_a_lot_body_cannot_also_carry_items(
    client: TestClient,
    admin_headers: dict[str, str],
    ebay_venue: SalesVenue,
    lot_of_three: SalesLot,
    make_item: ItemFactory,
) -> None:
    """Both subjects at once is what makes `offer`'s `ValueError` unreachable.

    `ebay_venue` and a real item, so **nothing else about this request is
    wrong**. Without the fixture `_venue_by_code` answers 422 for an unknown
    venue before `_one_subject` is ever consulted, and a status-only
    assertion passes with the rule deleted -- which is what this test did
    until the review caught it. The body is asserted for the same reason: a
    422 from pydantic and a 422 from the router are different answers and
    only one of them is this rule.
    """
    spare = make_item(title="Not in the lot")
    response = client.post(
        "/api/offers",
        headers=admin_headers,
        json={
            "venue": "ebay",
            "lot_id": lot_of_three.id,
            "price": "10.00",
            "items": [{"item_id": spare.id, "price": "10.00"}],
        },
    )
    assert response.status_code == 422, response.text
    assert "Unknown venue" not in response.text
    assert any("not both" in message for message in _pydantic_messages(response))


def test_a_lot_cannot_be_offered_as_more_than_one_unit(
    client: TestClient,
    admin_headers: dict[str, str],
    ebay_venue: SalesVenue,
    lot_of_three: SalesLot,
) -> None:
    """`ck_listing_lot_quantity_one` caps a lot listing at one unit.

    `offering_writes.offer` forces `quantity_available=1` for a lot, so an
    explicit `2` would be accepted and quietly ignored -- the caller would
    believe two lots were on offer. Refused instead, now that a caller can
    reach the override at all.

    `ebay_venue` and a body correct in every other way, so the only thing
    wrong with this request is the quantity; and the refusal is read out of
    the body, because an unknown venue answers 422 too and a status-only
    assertion cannot tell which one spoke.
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
    assert "Unknown venue" not in response.text
    assert any(
        "quantity must be 1" in message for message in _pydantic_messages(response)
    )


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
