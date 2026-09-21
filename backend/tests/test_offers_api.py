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
import pytest
from app import offering_writes
from app.models import InventoryItem, Listing, SalesVenue, SalesVenueKind
from app.schemas import OfferRefusedOut
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


def test_editing_and_ending_are_admin_only(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    """The guard runs before the handler, so no listing needs to exist.

    A 404 here would mean the dependency had been dropped and the request had
    reached the row lookup -- which is the regression these two lines exist
    to catch.
    """
    edit = {"price": "1.00"}
    assert client.patch("/api/listings/1", json=edit).status_code == 401
    assert (
        client.patch("/api/listings/1", json=edit, headers=customer_headers).status_code
        == 403
    )
    assert client.post("/api/listings/1/end").status_code == 401
    assert (
        client.post("/api/listings/1/end", headers=customer_headers).status_code == 403
    )


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
    # The exact string, not a Decimal comparison: money crosses this API as a
    # string and `Decimal(120.0) == Decimal("120.00")` would hide a regression
    # to float serialisation, which is the one thing forbidden here.
    assert listings[0]["price"] == "120.00"
    assert listings[1]["price"] == "45.50"
    assert listings[0]["item_code"] == first.item_code
    assert listings[0]["currency"] == "USD"
    # Admin-only, so the console may show what the item cost.
    assert listings[0]["cost_basis"] == str(first.total_cost)
    # A number, not the string "1": one `listing.version` column, so an int,
    # unlike the catalogue's composite version token.
    assert listings[0]["version"] == 1

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


def _rejected_fields(response: httpx.Response) -> set[str]:
    """The field each validation error names, so a 422 is pinned to its cause.

    A bare `status_code == 422` would pass if the request were rejected for
    some entirely different reason, which is exactly what these tests must not
    accept.
    """
    return {str(error["loc"][-1]) for error in response.json()["detail"]}


def test_the_same_item_twice_in_one_batch_says_so(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    """Named plainly, rather than as "already offered" by the writer."""
    item = make_item()
    venue = _venue(db, "ebay-twice")

    response = _offer(
        client,
        admin_headers,
        venue.code,
        [
            {"item_id": item.id, "price": "1.00"},
            {"item_id": item.id, "price": "2.00"},
        ],
    )
    assert response.status_code == 409, response.text
    refused = response.json()["refused"]
    assert [row["item_code"] for row in refused] == [item.item_code]
    assert "more than once in this batch" in refused[0]["reason"]

    db.expire_all()
    assert (
        db.scalars(select(Listing).where(Listing.inventory_item_id == item.id)).all()
        == []
    )


def test_a_price_with_too_many_decimal_places_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    """Refused here rather than silently rounded to 10.01 by the column."""
    item = make_item()
    venue = _venue(db, "ebay-precision")
    response = _offer(
        client, admin_headers, venue.code, [{"item_id": item.id, "price": "10.005"}]
    )
    assert response.status_code == 422, response.text
    assert _rejected_fields(response) == {"price"}


def test_a_price_too_large_for_the_column_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    """Twelve integer digits overflow `Numeric(12, 2)`; that must not be a 500."""
    item = make_item()
    venue = _venue(db, "ebay-overflow")
    response = _offer(
        client,
        admin_headers,
        venue.code,
        [{"item_id": item.id, "price": "999999999999.00"}],
    )
    assert response.status_code == 422, response.text
    assert _rejected_fields(response) == {"price"}


def test_a_negative_price_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    item = make_item()
    venue = _venue(db, "ebay-negative")
    response = _offer(
        client, admin_headers, venue.code, [{"item_id": item.id, "price": "-5.00"}]
    )
    assert response.status_code == 422, response.text
    assert _rejected_fields(response) == {"price"}


def test_a_quantity_below_one_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
) -> None:
    item = make_item()
    venue = _venue(db, "ebay-quantity")
    response = client.post(
        "/api/offers",
        headers=admin_headers,
        json={
            "venue": venue.code,
            "format": "fixed_price",
            "quantity": 0,
            "items": [{"item_id": item.id, "price": "1.00"}],
        },
    )
    assert response.status_code == 422, response.text
    assert _rejected_fields(response) == {"quantity"}


def test_the_race_refusal_has_the_same_body_as_every_other(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loser of a race reads the body the published schema promises.

    The race itself is `tests/test_offer_races.py`'s subject; what is pinned
    here is that the `IntegrityError` it ends in produces `OfferRefusedOut`
    and not a bare `{"detail": ...}`, which a console reading `refused` on
    every 409 would throw on.
    """
    item = make_item()
    venue = _venue(db, "ebay-race")

    def _lost(db_: Session, **kwargs: object) -> Listing:
        raise IntegrityError(
            "INSERT INTO offer_claim ...", {}, Exception("duplicate key value")
        )

    monkeypatch.setattr(offering_writes, "offer", _lost)
    response = _offer(
        client, admin_headers, venue.code, [{"item_id": item.id, "price": "1.00"}]
    )
    assert response.status_code == 409, response.text
    body = response.json()
    # Validates against the one 409 model the OpenAPI publishes.
    refusal = OfferRefusedOut.model_validate(body)
    assert [row.item_code for row in refusal.refused] == [item.item_code]
    assert "somewhere else" in refusal.refused[0].reason


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
    # The list serves the same version the console sends back on a PATCH, and
    # it is a number there too.
    assert rows[0]["version"] == 1


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
    assert good.json()["price"] == "25.00"
    assert good.json()["title"] == "Edited"
    assert listing["version"] == 1
    assert good.json()["version"] == 2

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


def test_the_listings_page_shows_a_lot_listing(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """`_out` reads `listing.inventory_item.item_code`, which a lot has not.

    Without the widening this is an `AttributeError` inside the endpoint --
    a 500 on the page that lists every offer, not a missing row.

    `GET /api/listings` answers a bare array, not an object with a
    `listings` key: `frontend/src/owner/pages/Listings.jsx` reads it as an
    array and `api.listListings` hands it straight over.
    """
    db.commit()
    rows = client.get("/api/listings", headers=admin_headers).json()
    row = next(entry for entry in rows if entry["id"] == offered_lot_listing.id)
    assert row["item_id"] is None
    assert row["item_code"] is None
    assert row["sales_lot_id"] == offered_lot_listing.sales_lot_id
    assert row["member_count"] == 3
    assert row["item_title"] == "Three Morgan Dollars"
    # The members cost 500, 300 and 200, and money crosses as a string.
    assert row["cost_basis"] == "1000.00"


def test_an_item_listing_keeps_its_own_fields(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    ebay_listing: Listing,
) -> None:
    """Widening `item_id` and `item_code` to null must not null them here."""
    db.commit()
    rows = client.get("/api/listings", headers=admin_headers).json()
    row = next(entry for entry in rows if entry["id"] == ebay_listing.id)
    assert row["item_id"] == ebay_listing.inventory_item_id
    assert row["item_code"]
    assert row["sales_lot_id"] is None
    assert row["member_count"] is None


def _members_of(listing: Listing) -> list[int]:
    """The item ids a lot listing offers, in `open_members` order."""
    lot = listing.sales_lot
    assert lot is not None, "this fixture is a lot listing"
    return sorted(row.inventory_item_id for row in lot.members)


def test_one_coin_s_offers_include_the_lot_that_offers_it(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """`item_id=` filtered on a column a lot listing leaves NULL.

    `Listing.inventory_item_id` is null on a lot listing -- the lot's coins
    are reached through `offer_claim` -- so filtering on it alone answered
    `[]` for a coin that is on sale inside a lot. The console's offers panel
    (`OffersPanel.jsx`) is the reader: it asked this endpoint per item, got
    nothing, and printed "Not offered anywhere yet" about a coin a buyer was
    looking at, while also offering an "Offer for sale..." button the writer
    refuses (`offering_writes._refuse_grouped`).

    Asserting the row's identity and its lot fields, not that the list is
    non-empty: this coin has exactly one offer, and naming it is what tells
    "found the lot listing" apart from "found something".
    """
    member_id = _members_of(offered_lot_listing)[0]
    db.commit()

    rows = client.get(
        "/api/listings",
        headers=admin_headers,
        params={"item_id": member_id, "status": "all"},
    ).json()

    assert [row["id"] for row in rows] == [offered_lot_listing.id]
    row = rows[0]
    # It must read as the lot it is, not as this coin: `item_code` is what
    # the panel would otherwise print, and a lot has none.
    assert row["item_code"] is None
    assert row["item_id"] is None
    assert row["sales_lot_id"] == offered_lot_listing.sales_lot_id
    assert row["member_count"] == 3
    assert row["item_title"] == "Three Morgan Dollars"


def test_one_coin_s_offers_keep_a_lot_listing_that_has_ended(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """Offer history outlives the offer, for a lot the same as for an item.

    The panel asks with `status=all` because "where this coin has been
    offered before, and for how much" is half of what it shows. Ending a lot
    listing releases every member's claim (`offering_writes.end_offer`), so
    a present-tense predicate -- `offers_holding`, or anything built on
    `_holds_any`, which filters claims to `HELD_BY` -- would answer this
    correctly while the lot was live and then lose the row the moment it
    ended. That is why the filter asks `ever_named_any`, whose claim half
    has no state filter at all.

    A single-item listing has never had this problem, because
    `Listing.inventory_item_id` survives the end of the offer. Only the lot
    half depends on released claims, so only the lot half can regress here.
    """
    member_id = _members_of(offered_lot_listing)[0]
    lot_listing_id = offered_lot_listing.id
    db.commit()

    ended = client.post(f"/api/listings/{lot_listing_id}/end", headers=admin_headers)
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "ended"

    rows = client.get(
        "/api/listings",
        headers=admin_headers,
        params={"item_id": member_id, "status": "all"},
    ).json()

    assert [row["id"] for row in rows] == [lot_listing_id]
    assert rows[0]["status"] == "ended"
