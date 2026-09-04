"""Catalogue reads (public) and writes (admin only)."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Coin, ItemKind, OrderStatus

NEW_ITEM = {
    "sku": "TEST-NEW-001",
    "title": "1909-S VDB Lincoln Cent",
    "price": "1450.00",
    "quantity": 2,
    "country": "United States",
    "year": 1909,
    "grade": "VF-20",
}


# --------------------------------------------------------------------------
# Public reads
# --------------------------------------------------------------------------


def test_list_is_public(client: TestClient, coin: Coin) -> None:
    response = client.get("/api/coins")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["sku"] == coin.sku


def test_detail_is_public(client: TestClient, coin: Coin) -> None:
    response = client.get(f"/api/coins/{coin.id}")
    assert response.status_code == 200
    assert response.json()["title"] == coin.title


def test_detail_404_for_unknown_id(client: TestClient) -> None:
    assert client.get("/api/coins/999999").status_code == 404


def test_price_is_serialised_with_two_decimals(client: TestClient, coin: Coin) -> None:
    """Money must survive the round trip exactly -- no float drift."""
    assert client.get(f"/api/coins/{coin.id}").json()["price"] == "189.00"


def test_inactive_items_hidden_by_default(
    client: TestClient, make_coin, db: Session
) -> None:
    make_coin(n=1, sku="TEST-HIDDEN", is_active=False)
    assert client.get("/api/coins").json()["total"] == 0
    assert client.get("/api/coins?include_inactive=true").json()["total"] == 1


def test_search_matches_title_and_country(client: TestClient, coin: Coin) -> None:
    assert client.get("/api/coins?q=Morgan").json()["total"] == 1
    assert client.get("/api/coins?q=United").json()["total"] == 1
    assert client.get("/api/coins?q=nothingmatches").json()["total"] == 0


def test_filter_by_kind(client: TestClient, make_coin) -> None:
    make_coin(n=1, sku="TEST-COIN", kind=ItemKind.coin)
    make_coin(n=2, sku="TEST-NOTE", kind=ItemKind.banknote)
    assert client.get("/api/coins?kind=coin").json()["total"] == 1
    assert client.get("/api/coins?kind=banknote").json()["total"] == 1


def test_filter_in_stock_only(client: TestClient, make_coin) -> None:
    make_coin(n=1, sku="TEST-STOCKED", quantity=2)
    make_coin(n=2, sku="TEST-SOLDOUT", quantity=0)
    assert client.get("/api/coins").json()["total"] == 2
    assert client.get("/api/coins?in_stock=true").json()["total"] == 1


def test_filter_by_year_range(client: TestClient, make_coin) -> None:
    make_coin(n=1, sku="TEST-OLD", year=1850)
    make_coin(n=2, sku="TEST-NEW", year=1990)
    assert client.get("/api/coins?year_min=1900").json()["total"] == 1
    assert client.get("/api/coins?year_max=1900").json()["total"] == 1


def test_pagination_reports_total_not_page_size(
    client: TestClient, make_coin
) -> None:
    for n in range(5):
        make_coin(n=n, sku=f"TEST-PAGE-{n}")
    body = client.get("/api/coins?limit=2&offset=0").json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2

    second = client.get("/api/coins?limit=2&offset=2").json()
    assert len(second["items"]) == 2
    first_ids = {i["id"] for i in body["items"]}
    second_ids = {i["id"] for i in second["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_limit_is_bounded(client: TestClient) -> None:
    assert client.get("/api/coins?limit=0").status_code == 422
    assert client.get("/api/coins?limit=500").status_code == 422


# --------------------------------------------------------------------------
# Authorisation on writes
# --------------------------------------------------------------------------


def test_create_requires_authentication(client: TestClient) -> None:
    assert client.post("/api/coins", json=NEW_ITEM).status_code == 401


def test_create_forbidden_for_customer(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    response = client.post("/api/coins", json=NEW_ITEM, headers=customer_headers)
    assert response.status_code == 403


def test_update_forbidden_for_customer(
    client: TestClient, coin: Coin, customer_headers: dict[str, str]
) -> None:
    response = client.patch(
        f"/api/coins/{coin.id}", json={"price": "1.00"}, headers=customer_headers
    )
    assert response.status_code == 403


def test_delete_forbidden_for_customer(
    client: TestClient, coin: Coin, customer_headers: dict[str, str]
) -> None:
    response = client.delete(f"/api/coins/{coin.id}", headers=customer_headers)
    assert response.status_code == 403


# --------------------------------------------------------------------------
# Admin writes
# --------------------------------------------------------------------------


def test_admin_can_create(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.post("/api/coins", json=NEW_ITEM, headers=admin_headers)
    assert response.status_code == 201
    assert response.json()["price"] == "1450.00"


def test_duplicate_sku_rejected(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert client.post("/api/coins", json=NEW_ITEM, headers=admin_headers).status_code == 201
    duplicate = client.post("/api/coins", json=NEW_ITEM, headers=admin_headers)
    assert duplicate.status_code == 409


def test_negative_price_rejected(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    payload = {**NEW_ITEM, "sku": "TEST-NEG", "price": "-5.00"}
    assert client.post("/api/coins", json=payload, headers=admin_headers).status_code == 422


def test_negative_quantity_rejected(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    payload = {**NEW_ITEM, "sku": "TEST-NEGQ", "quantity": -1}
    assert client.post("/api/coins", json=payload, headers=admin_headers).status_code == 422


def test_patch_only_changes_supplied_fields(
    client: TestClient, coin: Coin, admin_headers: dict[str, str]
) -> None:
    original_title = coin.title
    response = client.patch(
        f"/api/coins/{coin.id}", json={"price": "200.00"}, headers=admin_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["price"] == "200.00"
    assert body["title"] == original_title
    assert body["grade"] == "MS-64"


def test_patch_404_for_unknown_id(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/coins/999999", json={"price": "1.00"}, headers=admin_headers
    )
    assert response.status_code == 404


def test_admin_can_delete_unsold_item(
    client: TestClient, coin: Coin, admin_headers: dict[str, str]
) -> None:
    assert client.delete(f"/api/coins/{coin.id}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/coins/{coin.id}").status_code == 404


def test_cannot_delete_item_that_appears_in_an_order(
    client: TestClient,
    coin: Coin,
    admin_headers: dict[str, str],
    customer_headers: dict[str, str],
) -> None:
    """Order history must not be destroyed by a catalogue delete."""
    placed = client.post(
        "/api/orders",
        json={"items": [{"coin_id": coin.id, "quantity": 1}]},
        headers=customer_headers,
    )
    assert placed.status_code == 201

    response = client.delete(f"/api/coins/{coin.id}", headers=admin_headers)
    assert response.status_code == 409
    assert "is_active" in response.json()["detail"]
