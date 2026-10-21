"""Sales platforms (docs/specs/selling-design.md)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from app.models import (
    Listing,
    ListingFormat,
    ListingStatus,
    SalesVenue,
    SalesVenueKind,
    User,
    Vendor,
)
from app.references import retirable
from app.routers import sales_venues as sales_venues_router
from app.sales_venues import STORE_CODE, ensure_store_venue, store_venue_id
from app.schemas import SalesVenueUpdate
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.builders import patch_current


def test_the_platform_kinds_are_seeded(db: Session) -> None:
    codes = set(db.scalars(select(SalesVenueKind.code)))
    assert codes == {"own_store", "marketplace", "live_auction", "auction_house"}


def test_platform_kinds_cannot_be_retired() -> None:
    # The store's kind is looked up by code, and the API branches on it.
    assert not retirable("sales_venue_kind", "marketplace")


def _kind_id(db: Session, code: str) -> int:
    """The id of the sales venue kind with this code."""
    return db.scalars(
        select(SalesVenueKind.id).where(SalesVenueKind.code == code)
    ).one()


def test_the_test_database_has_exactly_one_store(db: Session) -> None:
    stores = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).all()
    assert [s.code for s in stores] == [STORE_CODE]
    assert store_venue_id(db) == stores[0].id


def test_ensure_store_venue_is_idempotent(db: Session) -> None:
    assert ensure_store_venue(db) == store_venue_id(db)


def test_a_second_store_is_refused_by_the_database(db: Session) -> None:
    db.add(
        SalesVenue(
            code="store2",
            name="Another store",
            sales_venue_kind_id=_kind_id(db, "own_store"),
            is_own_store=True,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_purchase_source_links_to_at_most_one_platform(db: Session) -> None:
    vendor = Vendor(name="linked.example")
    db.add(vendor)
    db.flush()
    kind = _kind_id(db, "marketplace")
    db.add(
        SalesVenue(code="a", name="A", sales_venue_kind_id=kind, vendor_id=vendor.id)
    )
    db.add(
        SalesVenue(code="b", name="B", sales_venue_kind_id=kind, vendor_id=vendor.id)
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_is_active_follows_status(db: Session, listing: Listing) -> None:
    """The database computes `is_active`, and the session goes back for it.

    No `db.refresh` here on purpose: `is_active` is a generated column, so
    the only place its new value exists after the write is the database.
    Reading it straight off the instance is what every caller does, and it
    has to be right -- if the session handed back the value it had written
    `status` over, this would still say True.

    Flushed, not committed: this session expires every instance on commit
    and production's does not (`app/database.py`), so after a commit the
    attribute would be reloaded here whatever the mapping did about a
    generated column.
    """
    assert listing.status is ListingStatus.active
    assert listing.is_active is True

    listing.status = ListingStatus.paused
    db.flush()
    assert listing.is_active is False


def test_a_new_listing_is_active_fixed_price(db: Session, listing: Listing) -> None:
    assert listing.is_active is True
    assert listing.format is ListingFormat.fixed_price
    assert listing.sales_venue_id == store_venue_id(db)


def test_public_catalog_shows_only_store_fixed_price_listings(
    db: Session, make_listing: Callable[..., Listing]
) -> None:
    store_listing = make_listing()
    ebay = SalesVenue(
        code="ebay", name="eBay", sales_venue_kind_id=_kind_id(db, "marketplace")
    )
    db.add(ebay)
    db.flush()
    elsewhere = make_listing(sales_venue_id=ebay.id)
    auction = make_listing(format=ListingFormat.auction)

    shown = set(db.scalars(text("select listing_id from public_catalog")))
    assert store_listing.id in shown
    assert elsewhere.id not in shown
    assert auction.id not in shown


URL = "/api/sales-venues"


def _create(client: TestClient, headers: dict[str, str], **fields: object) -> dict:
    """Add a platform through the API -- eBay unless overridden; the created body."""
    body = {"code": "ebay", "name": "eBay", "kind": "marketplace", **fields}
    response = client.post(URL, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_platforms_are_admin_only(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers=customer_headers).status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", URL, {"code": "ebay", "name": "eBay", "kind": "marketplace"}),
        ("PATCH", f"{URL}/ebay", {"name": "eBay US", "version": 1}),
    ],
)
def test_changing_a_platform_is_admin_only(
    method: str,
    path: str,
    body: dict[str, object],
    client: TestClient,
    customer_headers: dict[str, str],
    db: Session,
) -> None:
    """401 signed out, 403 for a customer, on the writes as well as the list.

    Each body is schema-valid, so a 422 cannot stand in for the gate, and
    nothing is written either way.
    """
    assert client.request(method, path, json=body).status_code == 401
    assert (
        client.request(method, path, json=body, headers=customer_headers).status_code
        == 403
    )
    assert db.scalar(select(SalesVenue).where(SalesVenue.code == "ebay")) is None


def test_the_store_is_listed_first(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _create(client, admin_headers, code="aaa", name="AAA")
    rows = client.get(URL, headers=admin_headers).json()
    assert rows[0]["code"] == "store"
    assert rows[0]["is_own_store"] is True
    assert rows[0]["kind"] == "own_store"


def test_create_a_platform_with_fees(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    created = _create(
        client,
        admin_headers,
        account_handle="nh_lakes_coins",
        listing_url_template="https://www.ebay.com/itm/{external_id}",
        commission_rate="0.1325",
        processing_fixed="0.40",
        terms_as_of="2026-09-17",
    )
    assert created["kind"] == "marketplace"
    assert created["commission_rate"] == "0.1325"
    assert created["processing_fixed"] == "0.40"
    assert created["processing_rate"] is None
    assert created["is_own_store"] is False
    assert created["version"] == 1


def test_a_second_store_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={"code": "shop2", "name": "Shop 2", "kind": "own_store"},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "only one web store" in response.json()["detail"]


def test_an_unknown_kind_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL, json={"code": "x", "name": "X", "kind": "bazaar"}, headers=admin_headers
    )
    assert response.status_code == 422
    assert "bazaar" in response.json()["detail"]


def test_a_duplicate_code_is_a_conflict(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _create(client, admin_headers)
    response = client.post(
        URL,
        json={"code": "ebay", "name": "eBay again", "kind": "marketplace"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_a_bad_code_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={"code": "Has Spaces", "name": "X", "kind": "marketplace"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_a_template_without_the_placeholder_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={
            "code": "x",
            "name": "X",
            "kind": "marketplace",
            "listing_url_template": "https://example.com/item",
        },
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_a_rate_above_one_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={
            "code": "x",
            "name": "X",
            "kind": "marketplace",
            "commission_rate": "13.25",
        },
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_a_vendor_links_to_one_platform_only(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = Vendor(name="ebay.example")
    db.add(vendor)
    db.commit()
    first = _create(client, admin_headers, vendor_id=vendor.id)
    assert first["vendor_name"] == "ebay.example"

    response = client.post(
        URL,
        json={
            "code": "ebay2",
            "name": "eBay 2",
            "kind": "marketplace",
            "vendor_id": vendor.id,
        },
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert "eBay" in response.json()["detail"]


def test_an_unknown_vendor_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={"code": "x", "name": "X", "kind": "marketplace", "vendor_id": 999999},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_edit_and_retire_a_platform(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    created = _create(client, admin_headers)
    response = client.patch(
        f"{URL}/ebay",
        json={"name": "eBay US", "is_active": False, "version": created["version"]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "eBay US"
    assert body["is_active"] is False
    assert body["version"] == created["version"] + 1


def test_unlink_a_vendor_with_null(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = Vendor(name="whatnot.example")
    db.add(vendor)
    db.commit()
    _create(client, admin_headers, code="whatnot", vendor_id=vendor.id)

    response = patch_current(
        client, f"{URL}/whatnot", json={"vendor_id": None}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["vendor_id"] is None


def test_a_purchase_source_gone_before_the_commit_is_not_called_linked(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a duplicate link reads as "already linked".

    The purchase source is checked before the write, and another request can
    delete it in between: the foreign key then refuses the commit. That is
    not a second platform holding the link, and saying so would send the
    owner looking for one that does not exist. The check is stood down here
    to open that window. It is the same bad field the check itself answers:
    a 422 naming the id.
    """
    created = _create(client, admin_headers)
    monkeypatch.setattr(sales_venues_router, "_check_vendor", lambda *_args: None)

    with pytest.raises(HTTPException) as refused:
        sales_venues_router.update_sales_venue(
            "ebay",
            SalesVenueUpdate(vendor_id=999999, version=created["version"]),
            db,
            admin_user,
        )
    assert refused.value.status_code == 422
    assert refused.value.detail == "No purchase source with id 999999"


def test_a_stale_version_is_a_conflict(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    created = _create(client, admin_headers)
    patch_current(client, f"{URL}/ebay", json={"name": "one"}, headers=admin_headers)
    response = client.patch(
        f"{URL}/ebay",
        json={"name": "two", "version": created["version"]},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_the_store_keeps_its_kind_and_stays_active(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    kind = patch_current(
        client, f"{URL}/store", json={"kind": "marketplace"}, headers=admin_headers
    )
    retire = patch_current(
        client, f"{URL}/store", json={"is_active": False}, headers=admin_headers
    )
    rename = patch_current(
        client, f"{URL}/store", json={"name": "Our shop"}, headers=admin_headers
    )
    assert kind.status_code == 422
    assert retire.status_code == 422
    assert rename.status_code == 200


def test_patching_an_unknown_platform_is_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = patch_current(
        client, f"{URL}/nope", json={"name": "x"}, headers=admin_headers
    )
    assert response.status_code == 404


def test_an_explicit_null_name_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _create(client, admin_headers)
    response = patch_current(
        client, f"{URL}/ebay", json={"name": None}, headers=admin_headers
    )
    assert response.status_code == 422
    assert "name" in response.json()["detail"]


def test_an_explicit_null_is_active_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _create(client, admin_headers)
    response = patch_current(
        client, f"{URL}/ebay", json={"is_active": None}, headers=admin_headers
    )
    assert response.status_code == 422
    assert "is_active" in response.json()["detail"]
