"""Catalogue reads. Public: no authorisation of any kind is needed to browse.

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

from collections.abc import Callable

from app.models import Listing, ListingFormat, SalesVenue, SalesVenueKind
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
    assert response.json()["title"] == listing.inventory_item.source_title


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
    assert client.get("/api/catalog?include_inactive=true").json()["total"] == 1


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

    The same authorisation boundary the public_catalog view enforces, at
    the API layer. A field added for staff must not reach a customer.
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
