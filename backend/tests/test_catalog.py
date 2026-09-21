"""Catalogue reads. Browsing is public; seeing what is *not* for sale is not.

No authorisation of any kind is needed to browse. The one exception is
`include_inactive`, the administrator's preview of withdrawn listings, which
is checked here rather than by a dependency because the endpoint itself has
to answer a signed-out browser.

Ported from the scaffold's test_coins.py. One behaviour did not survive and
should not have: the scaffold enforced a unique `sku` per catalogue row. The
target schema has no such key, because two identical Morgan dollars are two
physical objects and two rows. Forcing artificial uniqueness on them was a
property of the demo, not of the domain.

The catalogue used to also write: `POST`/`PATCH`/`DELETE /api/catalog` created
an item and a listing together, edited either, and deleted an unsold one. That
path is retired (the offers API replaces it -- `test_offers_api.py`,
`test_offering_writes.py`), because it could not offer an item the business
already owned, and creating an item outside a purchase is the bug the entry
panels were built to stop.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from app import offering_writes
from app.models import (
    InventoryItem,
    Listing,
    ListingFormat,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesOrderItemShare,
    SalesVenue,
    SalesVenueKind,
)
from app.sales_venues import store_venue_id
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------
# Public reads
# --------------------------------------------------------------------------


def test_list_is_public(client: TestClient, listing: Listing) -> None:
    response = client.get("/api/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == listing.id


def test_detail_is_public(client: TestClient, listing: Listing) -> None:
    response = client.get(f"/api/catalog/{listing.id}")
    assert response.status_code == 200
    item = listing.inventory_item
    assert item is not None
    assert response.json()["title"] == item.source_title


def test_detail_404_for_unknown_id(client: TestClient) -> None:
    assert client.get("/api/catalog/999999").status_code == 404


def test_price_is_serialised_with_two_decimals(
    client: TestClient, listing: Listing
) -> None:
    """Money must survive the round trip exactly -- no float drift."""
    assert client.get(f"/api/catalog/{listing.id}").json()["price"] == "189.00"


def test_withdrawn_listings_hidden_by_default(
    client: TestClient, make_listing: Callable[..., Listing]
) -> None:
    make_listing(is_active=False)
    assert client.get("/api/catalog").json()["total"] == 0


def test_a_stranger_cannot_ask_to_see_withdrawn_listings(
    client: TestClient, make_listing: Callable[..., Listing]
) -> None:
    """`include_inactive` is described as an admin preview, so make it one.

    The endpoint is public and must stay so -- the shop answers signed-out
    browsers. But the parameter was honoured for anyone who passed it, so
    every listing the owner had ever withdrawn was one query string away.
    That set is the stock taken off sale, mostly because it sold elsewhere:
    a history of the collection no buyer is owed.
    """
    make_listing(is_active=False)
    refused = client.get("/api/catalog?include_inactive=true")
    assert refused.status_code == 403, refused.text
    assert "Administrator" in refused.json()["detail"]


def test_a_customer_is_not_enough_to_see_withdrawn_listings(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
) -> None:
    """Signed in is not the same as being an administrator.

    Separate from the anonymous case on purpose: a check written as "is there
    a token" would pass that one and fail this, and a shop full of customer
    accounts is exactly where the difference bites.
    """
    make_listing(is_active=False)
    refused = client.get("/api/catalog?include_inactive=true", headers=customer_headers)
    assert refused.status_code == 403, refused.text


def test_an_administrator_may_preview_withdrawn_listings(
    client: TestClient,
    make_listing: Callable[..., Listing],
    admin_headers: dict[str, str],
) -> None:
    """The preview still works, which is what stops the guard being a removal."""
    make_listing(is_active=False)
    allowed = client.get("/api/catalog?include_inactive=true", headers=admin_headers)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["total"] == 1


def test_search_matches_title_and_description(
    client: TestClient, listing: Listing
) -> None:
    assert client.get("/api/catalog?q=Morgan").json()["total"] == 1
    assert client.get("/api/catalog?q=fixture").json()["total"] == 1
    assert client.get("/api/catalog?q=nothingmatches").json()["total"] == 0


def test_filter_by_kind(
    client: TestClient, make_listing: Callable[..., Listing]
) -> None:
    make_listing(kind="coin")
    make_listing(kind="currency")
    assert client.get("/api/catalog?kind=coin").json()["total"] == 1
    assert client.get("/api/catalog?kind=currency").json()["total"] == 1


def test_filter_by_an_unknown_classifier_is_a_422_not_an_empty_page(
    client: TestClient, listing: Listing
) -> None:
    """An unknown classifier is an error, not an empty page.

    Silently returning nothing would look like "no results" rather than
    "you asked for something that does not exist".
    """
    response = client.get("/api/catalog?kind=notakind")
    assert response.status_code == 422
    assert "notakind" in response.json()["detail"]


def test_filter_in_stock_only(
    client: TestClient, make_listing: Callable[..., Listing]
) -> None:
    make_listing(quantity_available=2)
    make_listing(quantity_available=0)
    assert client.get("/api/catalog").json()["total"] == 2
    assert client.get("/api/catalog?in_stock=true").json()["total"] == 1


def test_filter_by_year_range(
    client: TestClient, make_listing: Callable[..., Listing]
) -> None:
    make_listing(year_start=1850)
    make_listing(year_start=1990)
    assert client.get("/api/catalog?year_min=1900").json()["total"] == 1
    assert client.get("/api/catalog?year_max=1900").json()["total"] == 1


def test_pagination_reports_total_not_page_size(
    client: TestClient, make_listing: Callable[..., Listing]
) -> None:
    for _ in range(5):
        make_listing()
    body = client.get("/api/catalog?limit=2&offset=0").json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2

    second = client.get("/api/catalog?limit=2&offset=2").json()
    assert len(second["items"]) == 2
    assert {i["id"] for i in body["items"]}.isdisjoint(
        {i["id"] for i in second["items"]}
    )


def test_limit_is_bounded(client: TestClient) -> None:
    assert client.get("/api/catalog?limit=0").status_code == 422
    assert client.get("/api/catalog?limit=500").status_code == 422


def test_catalogue_never_exposes_cost_basis_or_location(
    client: TestClient, listing: Listing
) -> None:
    """Cost basis and location must not reach a customer.

    This test is the enforcement, not a second copy of it. The
    `public_catalog` view describes the same boundary but no endpoint reads
    it; what actually shapes this response is `catalog.to_catalog_item`
    building it field by field. So a field added for staff is kept out by
    that function plus this assertion, and by nothing else.
    """
    body = client.get(f"/api/catalog/{listing.id}").json()
    forbidden = {
        "total_cost",
        "sales_tax",
        "shipping",
        "tax_rate",
        "numismatic_value",
        "storage_location_id",
        "local_catalog_number",
        "purchase_order_id",
        "notes_raw",
    }
    assert not (set(body) & forbidden), f"leaked: {sorted(set(body) & forbidden)}"


def _ebay(db: Session) -> int:
    kind = db.scalar(
        select(SalesVenueKind.id).where(SalesVenueKind.code == "marketplace")
    )
    venue = SalesVenue(code="ebay-catalog-test", name="eBay", sales_venue_kind_id=kind)
    db.add(venue)
    db.commit()
    return venue.id


def test_the_catalogue_hides_other_platforms_and_auctions(
    client: TestClient,
    listing: Listing,
    make_listing: Callable[..., Listing],
    db: Session,
) -> None:
    make_listing(sales_venue_id=_ebay(db))
    make_listing(format=ListingFormat.auction)

    body = client.get("/api/catalog").json()

    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [listing.id]


def test_a_listing_on_another_platform_is_not_found_in_the_shop(
    client: TestClient, make_listing: Callable[..., Listing], db: Session
) -> None:
    ebay_listing = make_listing(sales_venue_id=_ebay(db))

    assert client.get(f"/api/catalog/{ebay_listing.id}").status_code == 404


def test_an_auction_listing_is_not_found_in_the_shop(
    client: TestClient, make_listing: Callable[..., Listing]
) -> None:
    auction_listing = make_listing(format=ListingFormat.auction)

    assert client.get(f"/api/catalog/{auction_listing.id}").status_code == 404


# --------------------------------------------------------------------------
# Lots in the shop
#
# A lot listing has no `inventory_item` at all, so every assertion below is
# about the shape `to_catalog_item` builds for a group rather than for a coin
# -- and about the boundary staying exactly where it was.
# --------------------------------------------------------------------------


def _entry(client: TestClient, listing_id: int) -> dict[str, Any]:
    """The catalogue's list entry for one listing, or fail saying it is absent."""
    body = client.get("/api/catalog").json()
    for row in body["items"]:
        if row["id"] == listing_id:
            return dict(row)
    raise AssertionError(f"listing {listing_id} is not in the catalogue: {body}")


def _buy(
    client: TestClient, headers: dict[str, str], listing_id: int
) -> dict[str, Any]:
    """Check out one unit of a listing, the way the shop does it."""
    placed = client.post(
        "/api/orders",
        headers=headers,
        json={"items": [{"listing_id": listing_id, "quantity": 1}]},
    )
    assert placed.status_code == 201, placed.text
    return dict(placed.json())


def test_a_store_lot_appears_as_one_catalogue_entry(
    client: TestClient, db: Session, store_lot_listing: Listing
) -> None:
    """One thing for sale, with its members' public descriptions."""
    db.commit()
    entry = _entry(client, store_lot_listing.id)
    lot = store_lot_listing.sales_lot
    assert lot is not None
    assert entry["title"] == lot.title
    assert entry["inventory_item_id"] is None
    assert entry["item_code"] is None
    assert len(entry["members"]) == 3


def test_a_store_lot_has_a_detail_page_too(
    client: TestClient, db: Session, store_lot_listing: Listing
) -> None:
    """The detail endpoint projects through the same function the list does.

    Separate from the list test because the two reach `to_catalog_item` by
    different queries -- `_get_listing` does not go through the outer join --
    so a lot that the list serves could still 500 here.
    """
    db.commit()
    response = client.get(f"/api/catalog/{store_lot_listing.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quantity_available"] == 1
    assert body["price"] == "1200.00"
    assert len(body["members"]) == 3


def test_a_lot_entry_shows_the_offer_wording_not_the_lots(
    client: TestClient,
    db: Session,
    make_item: Callable[..., InventoryItem],
    make_lot: Callable[..., SalesLot],
) -> None:
    """The buyer reads what the offer says, not the lot's working name.

    Written with the two strings deliberately different. The fixture's lot is
    titled the same as its listing, so an assertion against it would pass
    whichever of the two the catalogue actually showed.
    """
    lot = make_lot(
        [make_item(title="First coin"), make_item(title="Second coin")],
        title="Working name nobody should see",
        description="Internal note.",
    )
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    listing = offering_writes.offer(
        db,
        lot=lot,
        venue=store,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("75.00"),
        title="Two Morgan Dollars",
        description="Both together.",
        external_id=None,
    )
    db.commit()

    entry = _entry(client, listing.id)
    assert entry["title"] == "Two Morgan Dollars"
    assert entry["description"] == "Both together."


def test_a_lot_entry_never_carries_cost_or_location(
    client: TestClient, db: Session, store_lot_listing: Listing
) -> None:
    """The authorisation boundary is `to_catalog_item` building fields by name.

    A lot widens what that function must build; this asserts the widening did
    not reach for the whole row. `json.dumps` rather than a key check for
    storage location, because it could arrive nested inside a member.
    """
    db.commit()
    entry = _entry(client, store_lot_listing.id)
    assert "total_cost" not in entry
    assert "storage_location" not in json.dumps(entry)
    assert "item_cost" not in json.dumps(entry)
    for member in entry["members"]:
        assert "total_cost" not in member


def test_a_non_store_lot_listing_stays_out_of_the_catalogue(
    client: TestClient, db: Session, offered_lot_listing: Listing
) -> None:
    """`shop_listing_filters` is the rule, and a lot does not get an exemption.

    The mutation that proves it: drop `shop_listing_filters` from the widened
    query and confirm this goes red while the test above stays green.
    """
    db.commit()
    body = client.get("/api/catalog").json()
    assert all(row["id"] != offered_lot_listing.id for row in body["items"])
    assert client.get(f"/api/catalog/{offered_lot_listing.id}").status_code == 404


def test_buying_a_lot_sells_every_member(
    client: TestClient,
    db: Session,
    customer_headers: dict[str, str],
    store_lot_listing: Listing,
) -> None:
    """Checkout of a lot is one line, with a share per member."""
    db.commit()
    lot = store_lot_listing.sales_lot
    assert lot is not None
    member_ids = [member.inventory_item_id for member in lot.members]

    placed = _buy(client, customer_headers, store_lot_listing.id)

    line_id = placed["items"][0]["id"]
    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == line_id
        )
    ).all()
    assert sorted(share.inventory_item_id for share in shares) == sorted(member_ids)
    for item_id in member_ids:
        item = db.get(InventoryItem, item_id)
        assert item is not None
        assert item.disposition.code == "sold"


def test_buying_a_lot_ends_it_sold_and_releases_its_members(
    client: TestClient,
    db: Session,
    customer_headers: dict[str, str],
    store_lot_listing: Listing,
) -> None:
    """A lot bought in the shop ends `sold`, with nothing still open.

    The lifecycle is `assembling -> offered -> sold | dissolved`
    (`docs/specs/selling-design.md`), and `released_at` is set when the lot is
    sold or dissolved. Without this, a shop checkout left the lot `offered`
    for ever: its members sold, their membership rows still open -- blocking
    the already-sold coins under `uq_sales_lot_item_open` -- and the listing
    active at quantity zero. `ck_listing_lot_quantity_one` caps a lot listing
    at one unit, so there is no partly-sold lot to reason about.
    """
    db.commit()
    lot = store_lot_listing.sales_lot
    assert lot is not None
    lot_id = lot.id

    _buy(client, customer_headers, store_lot_listing.id)

    sold_lot = db.get(SalesLot, lot_id)
    assert sold_lot is not None
    assert sold_lot.status is SalesLotStatus.sold
    still_open = db.scalars(
        select(SalesLotItem).where(
            SalesLotItem.sales_lot_id == lot_id,
            SalesLotItem.released_at.is_(None),
        )
    ).all()
    assert not still_open, f"still open: {[row.id for row in still_open]}"
