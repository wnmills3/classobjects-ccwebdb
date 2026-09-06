"""Catalogue reads (public) and writes (admin only).

Ported from the scaffold's test_coins.py. One behaviour did not survive and
should not have: the scaffold enforced a unique `sku` per catalogue row. The
target schema has no such key, because two identical Morgan dollars are two
physical objects and two rows. Forcing artificial uniqueness on them was a
property of the demo, not of the domain.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Listing

NEW_ITEM = {
    "title": "1909-S VDB Lincoln Cent",
    "price": "1450.00",
    "quantity_available": 2,
    "item_kind": "coin",
    "country": "US",
    "year_start": 1909,
    "grade": "VF20",
}


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
    assert response.json()["title"] == listing.inventory_item.title


def test_detail_404_for_unknown_id(client: TestClient) -> None:
    assert client.get("/api/catalog/999999").status_code == 404


def test_price_is_serialised_with_two_decimals(
    client: TestClient, listing: Listing
) -> None:
    """Money must survive the round trip exactly -- no float drift."""
    assert client.get(f"/api/catalog/{listing.id}").json()["price"] == "189.00"


def test_withdrawn_listings_hidden_by_default(client: TestClient, make_listing) -> None:
    make_listing(is_active=False)
    assert client.get("/api/catalog").json()["total"] == 0
    assert client.get("/api/catalog?include_inactive=true").json()["total"] == 1


def test_search_matches_title_and_description(
    client: TestClient, listing: Listing
) -> None:
    assert client.get("/api/catalog?q=Morgan").json()["total"] == 1
    assert client.get("/api/catalog?q=fixture").json()["total"] == 1
    assert client.get("/api/catalog?q=nothingmatches").json()["total"] == 0


def test_filter_by_kind(client: TestClient, make_listing) -> None:
    make_listing(kind="coin")
    make_listing(kind="currency")
    assert client.get("/api/catalog?kind=coin").json()["total"] == 1
    assert client.get("/api/catalog?kind=currency").json()["total"] == 1


def test_filter_by_an_unknown_classifier_is_a_422_not_an_empty_page(
    client: TestClient, listing: Listing
) -> None:
    """Silently returning nothing would look like "no results" rather than
    "you asked for something that does not exist"."""
    response = client.get("/api/catalog?kind=notakind")
    assert response.status_code == 422
    assert "notakind" in response.json()["detail"]


def test_filter_in_stock_only(client: TestClient, make_listing) -> None:
    make_listing(quantity_available=2)
    make_listing(quantity_available=0)
    assert client.get("/api/catalog").json()["total"] == 2
    assert client.get("/api/catalog?in_stock=true").json()["total"] == 1


def test_filter_by_year_range(client: TestClient, make_listing) -> None:
    make_listing(year_start=1850)
    make_listing(year_start=1990)
    assert client.get("/api/catalog?year_min=1900").json()["total"] == 1
    assert client.get("/api/catalog?year_max=1900").json()["total"] == 1


def test_pagination_reports_total_not_page_size(
    client: TestClient, make_listing
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
    """The same authorisation boundary the public_catalog view enforces, at
    the API layer. A field added for staff must not reach a customer."""
    body = client.get(f"/api/catalog/{listing.id}").json()
    forbidden = {
        "total_cost", "taxes", "shipping", "tax_rate", "numismatic_value",
        "storage_location_id", "local_catalog_number", "purchase_order_id",
        "notes_raw", "error_details",
    }
    assert not (set(body) & forbidden), f"leaked: {sorted(set(body) & forbidden)}"


# --------------------------------------------------------------------------
# Authorisation on writes
# --------------------------------------------------------------------------


def test_create_requires_authentication(client: TestClient) -> None:
    assert client.post("/api/catalog", json=NEW_ITEM).status_code == 401


def test_create_forbidden_for_customer(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    response = client.post("/api/catalog", json=NEW_ITEM, headers=customer_headers)
    assert response.status_code == 403


def test_update_forbidden_for_customer(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    response = client.patch(
        f"/api/catalog/{listing.id}", json={"price": "1.00"}, headers=customer_headers
    )
    assert response.status_code == 403


def test_delete_forbidden_for_customer(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    response = client.delete(f"/api/catalog/{listing.id}", headers=customer_headers)
    assert response.status_code == 403


# --------------------------------------------------------------------------
# Admin writes
# --------------------------------------------------------------------------


def test_admin_can_create(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post("/api/catalog", json=NEW_ITEM, headers=admin_headers)
    assert response.status_code == 201
    body = response.json()
    assert body["price"] == "1450.00"
    assert body["grade"] == "VF20"
    assert body["inventory_item_id"] > 0


def test_creating_makes_both_an_item_and_a_listing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The two are separate rows: that is what lets an item be relisted at a
    different price without rewriting what it is."""
    body = client.post("/api/catalog", json=NEW_ITEM, headers=admin_headers).json()

    listing = db.get(Listing, body["id"])
    assert listing is not None
    assert listing.inventory_item_id == body["inventory_item_id"]
    assert listing.inventory_item.title == NEW_ITEM["title"]


def test_an_unknown_classifier_is_rejected(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A code the caller believed in must not become a silent NULL."""
    payload = {**NEW_ITEM, "grade": "NOT-A-GRADE"}
    response = client.post("/api/catalog", json=payload, headers=admin_headers)
    assert response.status_code == 422
    assert "NOT-A-GRADE" in response.json()["detail"]


def test_negative_price_rejected(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    payload = {**NEW_ITEM, "price": "-5.00"}
    assert (
        client.post("/api/catalog", json=payload, headers=admin_headers).status_code
        == 422
    )


def test_negative_quantity_rejected(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    payload = {**NEW_ITEM, "quantity_available": -1}
    assert (
        client.post("/api/catalog", json=payload, headers=admin_headers).status_code
        == 422
    )


def test_backwards_year_range_rejected(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    payload = {**NEW_ITEM, "year_start": 2000, "year_end": 1999}
    assert (
        client.post("/api/catalog", json=payload, headers=admin_headers).status_code
        == 422
    )


def test_patch_only_changes_supplied_fields(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    original_title = listing.inventory_item.title
    response = client.patch(
        f"/api/catalog/{listing.id}", json={"price": "200.00"}, headers=admin_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["price"] == "200.00"
    assert body["title"] == original_title
    assert body["grade"] == "MS64"


def test_patch_404_for_unknown_id(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/catalog/999999", json={"price": "1.00"}, headers=admin_headers
    )
    assert response.status_code == 404


def test_withdrawing_a_listing_returns_the_item_to_held(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    """Disposition is the sales axis: an item not for sale is held, not sold."""
    client.patch(
        f"/api/catalog/{listing.id}", json={"is_active": False}, headers=admin_headers
    )
    db.expire_all()
    assert db.get(Listing, listing.id).inventory_item.disposition.code == "held"


def test_admin_can_delete_unsold_item(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    assert (
        client.delete(f"/api/catalog/{listing.id}", headers=admin_headers).status_code
        == 204
    )
    assert client.get(f"/api/catalog/{listing.id}").status_code == 404


def test_cannot_delete_item_that_appears_in_an_order(
    client: TestClient,
    listing: Listing,
    admin_headers: dict[str, str],
    customer_headers: dict[str, str],
) -> None:
    """Order history must not be destroyed by a catalogue delete."""
    placed = client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=customer_headers,
    )
    assert placed.status_code == 201

    response = client.delete(f"/api/catalog/{listing.id}", headers=admin_headers)
    assert response.status_code == 409
    assert "is_active" in response.json()["detail"]
