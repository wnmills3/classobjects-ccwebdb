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


#: Every key a catalogue entry may carry. An **allow-list**, written out by
#: hand, and the boundary's first assertion: a deny-list only catches the
#: private names somebody thought of, so a newly named one ships green for
#: ever and nobody finds out. This fails closed instead -- any new key, public
#: or private, reddens these tests until a person has written it down here.
#:
#: That edit is the point, not an inconvenience. Adding a field to
#: `CatalogItemOut` means editing this set in the same commit, which is a
#: review checkpoint on "should a buyer see this?" -- the checkpoint a
#: deny-list never offers. The evidence is in this file's own history: the
#: deny list had drifted into two unequal halves, and the weaker half was
#: silently wrong.
#:
#: Written out rather than derived from `CatalogItemOut.model_fields`,
#: deliberately. A derived set agrees with the model by construction, so it
#: fails **open** on exactly the case that matters: someone adding
#: `total_cost` to the model would add it to the expectation at the same
#: moment and the test would stay green.
EXPECTED_PUBLIC_FIELDS = {
    "id",
    "inventory_item_id",
    "version",
    "item_code",
    "title",
    "description",
    "item_kind",
    "country",
    "denomination",
    "bullion_form",
    "grade",
    "strike_type",
    "grade_display",
    "grading_service",
    "metal",
    "year_start",
    "year_end",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
    "piece_count",
    "price",
    "currency",
    "quantity_available",
    "is_active",
    "thumbnail_url",
    "image_url",
    "members",
    "created_at",
    "updated_at",
}

#: Every key one member of a lot may carry: `EXPECTED_PUBLIC_FIELDS` less the
#: listing's own fields, because a member has no price, no stock and no
#: listing of its own. Hand-written for the reason above -- this is the half
#: that was silently the weaker one.
EXPECTED_MEMBER_FIELDS = {
    "inventory_item_id",
    "item_code",
    "title",
    "description",
    "item_kind",
    "country",
    "denomination",
    "bullion_form",
    "grade",
    "strike_type",
    "grade_display",
    "grading_service",
    "metal",
    "year_start",
    "year_end",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
    "piece_count",
    "thumbnail_url",
    "image_url",
}

#: Staff-only names, kept **as well as** the allow-lists above and used over
#: the serialised payload rather than over its keys. The allow-lists cover
#: every key the two models declare; only this catches a private name nested
#: inside a structure neither of them declares today. Inexhaustive by nature,
#: which is why it is the second assertion and not the first.
PRIVATE_FIELD_NAMES = (
    "total_cost",
    "item_cost",
    "sales_tax",
    "shipping",
    "shipping_cost",
    "tax_rate",
    "tax_includes_shipping",
    "numismatic_value",
    "valuation_basis",
    "storage_location",
    "storage_location_id",
    "local_catalog_number",
    "purchase_order",
    "purchase_order_id",
    "order_number",
    "vendor",
    "rating",
    "weight_note",
    "listing_url",
    "source_url",
    "attributes",
    "deleted_at",
    "split_at",
    "parent_item_id",
)


def test_catalogue_never_exposes_cost_basis_or_location(
    client: TestClient, listing: Listing
) -> None:
    """Cost basis and location must not reach a customer.

    This test is the enforcement, not a second copy of it. The
    `public_catalog` view describes the same boundary but no endpoint reads
    it; what actually shapes this response is `catalog.to_catalog_item`
    building it field by field. So a field added for staff is kept out by
    that function plus this assertion, and by nothing else.

    The allow-list first, because it fails closed: a private field nobody
    thought to forbid still reddens it. The deny-list over the serialised
    payload second, for a name nested somewhere the key check cannot see.
    """
    body = client.get(f"/api/catalog/{listing.id}").json()
    assert set(body) == EXPECTED_PUBLIC_FIELDS, (
        f"unexpected: {sorted(set(body) - EXPECTED_PUBLIC_FIELDS)}, "
        f"missing: {sorted(EXPECTED_PUBLIC_FIELDS - set(body))}"
    )
    payload = json.dumps(body)
    leaked = [name for name in PRIVATE_FIELD_NAMES if name in payload]
    assert not leaked, f"leaked somewhere in the payload: {leaked}"


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


def _offer_in_store(
    db: Session,
    lot: SalesLot,
    *,
    title: str = "Two Morgan Dollars",
    description: str = "Both together.",
) -> Listing:
    """Offer a lot in the web store, the way the `store_lot_listing` fixture does."""
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    return offering_writes.offer(
        db,
        lot=lot,
        venue=store,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("75.00"),
        title=title,
        description=description,
        external_id=None,
    )


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
    listing = _offer_in_store(db, lot)
    db.commit()

    entry = _entry(client, listing.id)
    assert entry["title"] == "Two Morgan Dollars"
    assert entry["description"] == "Both together."


def test_a_buyers_order_line_calls_a_lot_what_the_shop_called_it(
    client: TestClient,
    db: Session,
    make_item: Callable[..., InventoryItem],
    make_lot: Callable[..., SalesLot],
    customer_headers: dict[str, str],
) -> None:
    """The same purchase, seen afterwards, must carry the same name.

    `routers.orders._sold_as` preferred the snapshot's `lot.title` -- the
    group's working name, which the test above establishes is not for buyers
    -- over the listing wording sitting beside it in the same snapshot. So
    the shop said "Two Morgan Dollars" and the buyer's own order said
    "Working name nobody should see". Asserted against the catalogue entry
    rather than against a literal, because agreeing with each other is the
    property that matters.
    """
    lot = make_lot(
        [make_item(title="First coin"), make_item(title="Second coin")],
        title="Working name nobody should see",
    )
    listing = _offer_in_store(db, lot)
    db.commit()

    entry = _entry(client, listing.id)
    placed = _buy(client, customer_headers, listing.id)

    assert placed["items"][0]["title"] == entry["title"] == "Two Morgan Dollars"


def test_a_lot_entry_counts_every_piece_in_it(
    client: TestClient,
    db: Session,
    make_item: Callable[..., InventoryItem],
    make_lot: Callable[..., SalesLot],
) -> None:
    """`piece_count` is how many objects the entry is, so a lot sums its members.

    One member is a five-coin roll, so the three candidate answers are all
    different: 7 (the truth), 3 (the number of members, which a multi-piece
    member makes wrong) and 1 (the field's default, which reads exactly like
    a genuine single coin and is what a lot reported before).
    """
    lot = make_lot(
        [
            make_item(title="A roll of Morgans", piece_count=5),
            make_item(title="One Morgan"),
            make_item(title="Another Morgan"),
        ]
    )
    listing = _offer_in_store(db, lot)
    db.commit()

    entry = _entry(client, listing.id)
    assert entry["piece_count"] == 7
    assert len(entry["members"]) == 3


def test_a_lot_entry_never_carries_cost_or_location(
    client: TestClient, db: Session, store_lot_listing: Listing
) -> None:
    """The authorisation boundary is `to_catalog_item` building fields by name.

    A lot widens what that function must build; this asserts the widening did
    not reach for the whole row. Three assertions, and they are not
    redundant: the entry's keys must be exactly `EXPECTED_PUBLIC_FIELDS`, so
    a field added to `CatalogItemOut` reddens whether or not anyone thought
    to forbid it; each member's keys must be exactly
    `EXPECTED_MEMBER_FIELDS`, which is what actually bites here, since a
    lot's private data would arrive nested; and `PRIVATE_FIELD_NAMES` over
    the serialised payload catches a private name appearing anywhere at all,
    including inside a structure neither model declares today.

    Both allow-lists are the same ones the single-item boundary test uses.
    Two lists drift, and the member half is the one a reader is most likely
    to widen.
    """
    db.commit()
    entry = _entry(client, store_lot_listing.id)
    assert set(entry) == EXPECTED_PUBLIC_FIELDS, (
        f"unexpected: {sorted(set(entry) - EXPECTED_PUBLIC_FIELDS)}, "
        f"missing: {sorted(EXPECTED_PUBLIC_FIELDS - set(entry))}"
    )
    for member in entry["members"]:
        assert set(member) == EXPECTED_MEMBER_FIELDS, (
            f"unexpected on a member: "
            f"{sorted(set(member) - EXPECTED_MEMBER_FIELDS)}, "
            f"missing: {sorted(EXPECTED_MEMBER_FIELDS - set(member))}"
        )
    payload = json.dumps(entry)
    leaked = [name for name in PRIVATE_FIELD_NAMES if name in payload]
    assert not leaked, f"leaked somewhere in the payload: {leaked}"


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


def test_a_sold_lots_page_still_says_which_coins_it_held(
    client: TestClient,
    db: Session,
    customer_headers: dict[str, str],
    store_lot_listing: Listing,
) -> None:
    """Selling a lot releases its memberships; the page must still list them.

    The detail endpoint serves an ended listing on purpose, so a page someone
    bookmarked can say the offer is over. Asked through
    `offering_writes.offered_items` -- "what does this listing offer now" --
    that page came back as a group with nothing in it, because ending a lot
    releases every membership in the same transaction. `members_held` is the
    past-tense reader that answers it.
    """
    db.commit()
    listing_id = store_lot_listing.id
    _buy(client, customer_headers, listing_id)

    response = client.get(f"/api/catalog/{listing_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_active"] is False
    assert len(body["members"]) == 3
    assert all(member["item_code"] for member in body["members"])
