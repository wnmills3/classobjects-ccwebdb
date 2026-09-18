"""The offers API (selling design, phase 2).

The HTTP face of `offering_writes`: offering items, listing what is offered,
editing an offer and ending one. The writer's own rules are tested in
`test_offering_writes.py`; what is checked here is the API contract on top of
them -- who may call it, that a batch is all or nothing, and that the response
shape carries what the console needs.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import httpx
from app.models import InventoryItem, Listing, SalesVenue, SalesVenueKind
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]

EBAY_TEMPLATE = "https://www.ebay.com/itm/{external_id}"


def _venue(
    db: Session,
    code: str,
    *,
    template: str | None = None,
    kind: str = "marketplace",
) -> SalesVenue:
    """A platform to offer on, committed so a rollback under test keeps it."""
    kind_id = db.scalar(select(SalesVenueKind.id).where(SalesVenueKind.code == kind))
    venue = SalesVenue(
        code=code,
        name=code.title(),
        sales_venue_kind_id=kind_id,
        listing_url_template=template,
    )
    db.add(venue)
    db.commit()
    return venue


def _offer(
    client: TestClient,
    headers: dict[str, str],
    venue: str,
    items: list[dict[str, object]],
) -> httpx.Response:
    """POST one batch of offers and hand back the response."""
    return client.post(
        "/api/offers",
        headers=headers,
        json={"venue": venue, "format": "fixed_price", "items": items},
    )


# --------------------------------------------------------------------------
# Who may call it
# --------------------------------------------------------------------------


def test_offering_is_admin_only(
    client: TestClient, customer_headers: dict[str, str], make_item: ItemFactory
) -> None:
    item = make_item()
    body = {
        "venue": "store",
        "format": "fixed_price",
        "items": [{"item_id": item.id, "price": "10.00"}],
    }
    assert client.post("/api/offers", json=body).status_code == 401
    assert (
        client.post("/api/offers", json=body, headers=customer_headers).status_code
        == 403
    )


def test_the_listing_list_is_admin_only(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    assert client.get("/api/listings").status_code == 401
    assert client.get("/api/listings", headers=customer_headers).status_code == 403


# --------------------------------------------------------------------------
# Offering
# --------------------------------------------------------------------------


def test_offering_two_items_at_once(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    first = make_item()
    second = make_item()
    venue = _venue(db, "ebay-two")

    response = _offer(
        client,
        admin_headers,
        venue.code,
        [
            {"item_id": first.id, "price": "120.00", "title": "First"},
            {"item_id": second.id, "price": "45.50", "title": "Second"},
        ],
    )
    assert response.status_code == 201, response.text
    listings = response.json()["listings"]
    assert len(listings) == 2
    assert [row["item_id"] for row in listings] == [first.id, second.id]
    assert {row["status"] for row in listings} == {"active"}
    assert {row["venue"] for row in listings} == {"ebay-two"}
    assert {row["format"] for row in listings} == {"fixed_price"}
    assert Decimal(listings[0]["price"]) == Decimal("120.00")
    assert listings[0]["item_code"] == first.item_code
    assert listings[0]["currency"] == "USD"
    # Admin-only, so the console may show what the item cost.
    assert Decimal(listings[0]["cost_basis"]) == first.total_cost

    written = db.scalars(
        select(Listing).where(Listing.inventory_item_id.in_([first.id, second.id]))
    ).all()
    assert len(written) == 2


def test_a_refused_batch_writes_nothing(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    """One item offered elsewhere refuses the whole batch, the rest included."""
    spoken_for = make_item()
    free = make_item()
    first_venue = _venue(db, "ebay-taken")
    second_venue = _venue(db, "whatnot-taken")

    assert (
        _offer(
            client,
            admin_headers,
            first_venue.code,
            [{"item_id": spoken_for.id, "price": "50.00"}],
        ).status_code
        == 201
    )

    response = _offer(
        client,
        admin_headers,
        second_venue.code,
        # The free item first, so it is written before the refusal happens.
        [
            {"item_id": free.id, "price": "10.00"},
            {"item_id": spoken_for.id, "price": "60.00"},
        ],
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert [row["item_code"] for row in body["refused"]] == [spoken_for.item_code]
    assert "ebay-taken".title() in body["refused"][0]["reason"]

    # Read back, not remembered: the other item must have no listing at all.
    db.expire_all()
    assert (
        db.scalars(select(Listing).where(Listing.inventory_item_id == free.id)).all()
        == []
    )


def test_an_unknown_item_id_is_unprocessable(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
) -> None:
    venue = _venue(db, "ebay-unknown-item")
    response = _offer(
        client, admin_headers, venue.code, [{"item_id": 999999, "price": "1.00"}]
    )
    assert response.status_code == 422, response.text


def test_an_unknown_platform_code_is_unprocessable(
    client: TestClient, admin_headers: dict[str, str], make_item: ItemFactory
) -> None:
    item = make_item()
    response = _offer(
        client,
        admin_headers,
        "no-such-platform",
        [{"item_id": item.id, "price": "1.00"}],
    )
    assert response.status_code == 422, response.text


def test_external_url_is_built_from_the_platform_template(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    """The URL is computed for the response and never stored on the row."""
    with_id = make_item()
    without_id = make_item()
    venue = _venue(db, "ebay-url", template=EBAY_TEMPLATE)

    response = _offer(
        client,
        admin_headers,
        venue.code,
        [
            {"item_id": with_id.id, "price": "1.00", "external_id": "12345"},
            {"item_id": without_id.id, "price": "1.00"},
        ],
    )
    assert response.status_code == 201, response.text
    listings = response.json()["listings"]
    assert listings[0]["external_url"] == "https://www.ebay.com/itm/12345"
    assert listings[1]["external_url"] is None

    stored = db.get(Listing, listings[0]["id"])
    assert stored is not None
    assert stored.external_url is None


# --------------------------------------------------------------------------
# Listing what is offered
# --------------------------------------------------------------------------


def test_listings_are_filtered_by_platform_and_by_status(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    here = make_item()
    there = make_item()
    first_venue = _venue(db, "ebay-filter")
    second_venue = _venue(db, "whatnot-filter")

    mine = _offer(
        client, admin_headers, first_venue.code, [{"item_id": here.id, "price": "9.00"}]
    ).json()["listings"][0]
    _offer(
        client,
        admin_headers,
        second_venue.code,
        [{"item_id": there.id, "price": "9.00"}],
    )

    by_venue = client.get(
        "/api/listings", headers=admin_headers, params={"venue": "ebay-filter"}
    )
    assert by_venue.status_code == 200, by_venue.text
    assert [row["id"] for row in by_venue.json()] == [mine["id"]]

    ended = client.post(f"/api/listings/{mine['id']}/end", headers=admin_headers)
    assert ended.status_code == 200, ended.text

    # Ended offers are out of the default list, and back in with status=all.
    still = client.get(
        "/api/listings", headers=admin_headers, params={"venue": "ebay-filter"}
    ).json()
    assert still == []
    everything = client.get(
        "/api/listings",
        headers=admin_headers,
        params={"venue": "ebay-filter", "status": "all"},
    ).json()
    assert [row["id"] for row in everything] == [mine["id"]]
    only_ended = client.get(
        "/api/listings", headers=admin_headers, params={"status": "ended"}
    ).json()
    assert [row["id"] for row in only_ended] == [mine["id"]]


def test_listings_are_filtered_by_item(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    wanted = make_item()
    other = make_item()
    venue = _venue(db, "ebay-by-item")
    _offer(
        client,
        admin_headers,
        venue.code,
        [
            {"item_id": wanted.id, "price": "1.00"},
            {"item_id": other.id, "price": "2.00"},
        ],
    )

    rows = client.get(
        "/api/listings", headers=admin_headers, params={"item_id": wanted.id}
    ).json()
    assert [row["item_id"] for row in rows] == [wanted.id]


# --------------------------------------------------------------------------
# Editing and ending
# --------------------------------------------------------------------------


def test_editing_an_offer_and_a_stale_version(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    item = make_item()
    venue = _venue(db, "ebay-edit")
    listing = _offer(
        client, admin_headers, venue.code, [{"item_id": item.id, "price": "10.00"}]
    ).json()["listings"][0]

    good = client.patch(
        f"/api/listings/{listing['id']}",
        headers=admin_headers,
        json={"price": "25.00", "title": "Edited", "version": listing["version"]},
    )
    assert good.status_code == 200, good.text
    assert Decimal(good.json()["price"]) == Decimal("25.00")
    assert good.json()["title"] == "Edited"
    assert good.json()["version"] != listing["version"]

    # The version the form loaded is now one behind.
    stale = client.patch(
        f"/api/listings/{listing['id']}",
        headers=admin_headers,
        json={"price": "30.00", "version": listing["version"]},
    )
    assert stale.status_code == 409, stale.text
    db.expire_all()
    unchanged = db.get(Listing, listing["id"])
    assert unchanged is not None
    assert unchanged.price == Decimal("25.00")


def test_ending_an_offer(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    item = make_item()
    venue = _venue(db, "ebay-end")
    listing = _offer(
        client, admin_headers, venue.code, [{"item_id": item.id, "price": "10.00"}]
    ).json()["listings"][0]

    response = client.post(f"/api/listings/{listing['id']}/end", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ended"
    assert body["ended_at"] is not None

    # The item is free to be offered again.
    again = _offer(
        client, admin_headers, venue.code, [{"item_id": item.id, "price": "11.00"}]
    )
    assert again.status_code == 201, again.text


def test_ending_an_unknown_listing_is_not_found(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post("/api/listings/999999/end", headers=admin_headers)
    assert response.status_code == 404
    # The router's own 404, not the one a missing route would give.
    assert response.json()["detail"] == "No such offer"
