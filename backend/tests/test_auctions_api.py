"""The auctions API (`docs/specs/selling-design.md`, *API*).

The HTTP face of `app.auctions`: creating and editing auctions, adding and
removing lots, the transitions, and settlement. The writer's own rules are
tested in `test_auctions.py` and `test_auction_settlement.py`; what is
checked here is the API contract on top of them -- who may call it, that a
settlement refusal names every problem lot rather than the first, that money
crosses the wire as a string, and that the narrow half of
each refusal pair (`SettlementInputInvalid`/`AuctionRefused`,
`sales_writes.SaleInputInvalid`/`SaleRefused`) reaches the console as a 422
and the wide half as a 409, **regardless of which order anything is raised
or registered in**. `app.main` registers a handler per class rather than
deciding by `except` order; the tests below prove that registration, not an
assumption about it, is what is running.

Also here: a migrated-but-unseeded database's `ReferenceDataMissing` from
`consign` reaches the operator as a 500 naming the seed command.
`POST /api/listings/{id}/end` refusing to end an auction lot's listing
directly is `routers.offers`, tested in `test_offers_api.py`.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app import auctions, sales_writes
from app.models import (
    Auction,
    AuctionLot,
    AuctionStatus,
    InventoryItem,
    Listing,
    ListingStatus,
    LocationHistory,
    SalesOrder,
    SalesVenue,
    StorageLocation,
    StorageLocationKind,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, ListingFactory, priced_item
from tests.conftest import build_lot, item_of

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def draft_auction(db: Session, heritage_venue: SalesVenue) -> Auction:
    """A draft auction at an auction house, with no lots yet."""
    row = Auction(
        sales_venue_id=heritage_venue.id,
        title="September Signature Sale",
        external_id="SIG-2026-09",
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def scheduled_auction(db: Session, draft_auction: Auction) -> Auction:
    """The same auction, moved to `scheduled` through the writer directly."""
    auctions.schedule(db, draft_auction)
    db.commit()
    return draft_auction


@pytest.fixture
def closed_auction_of_two_lots(
    db: Session, scheduled_auction: Auction, make_item: ItemFactory
) -> Auction:
    """Two single-item lots at an auction house, closed and awaiting results."""
    for number in (1, 2):
        auctions.add_lot(
            db,
            scheduled_auction,
            priced_item(make_item, f"Lot {number}", Decimal("100.00")),
            lot_number=str(number),
            reserve=None,
            price=Decimal("10.00"),
        )
    auctions.close(db, scheduled_auction)
    db.commit()
    return scheduled_auction


def _item_codes(listing: Listing) -> list[str]:
    """The item codes an auction lot's listing offers.

    Always a **lot** listing, never a plain item one: `app.auctions.add_lot`
    wraps even a single item in a lot of one (spec, *Three kinds of lot*), so
    `item_of` -- built for `build_listing`'s single-item listings -- does not
    apply here, and this reads `sales_lot_item` the way the application does.
    """
    lot = listing.sales_lot
    assert lot is not None, f"listing #{listing.id} is not a lot listing"
    return [row.item.item_code for row in lot.members]


def _auction_lots(db: Session, auction: Auction) -> list[AuctionLot]:
    """This auction's lots, ordered the way `app.auctions._lots_of` reads them."""
    return list(
        db.scalars(
            select(AuctionLot)
            .where(AuctionLot.auction_id == auction.id)
            .order_by(AuctionLot.id)
        ).all()
    )


# --------------------------------------------------------------------------
# Who may call it
# --------------------------------------------------------------------------


def test_auctions_are_admin_only(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    assert client.get("/api/auctions").status_code == 401
    assert client.get("/api/auctions", headers=customer_headers).status_code == 403
    body = {"venue": "store", "title": "Whatever"}
    assert client.post("/api/auctions", json=body).status_code == 401
    assert (
        client.post("/api/auctions", json=body, headers=customer_headers).status_code
        == 403
    )


def test_auction_transitions_are_admin_only(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    """The guard runs before the handler, so no auction needs to exist.

    Every transition, not just schedule and
    close -- consign, cancel and settle each take a body, so each is sent a
    schema-valid one to isolate the admin gate from request validation.
    """
    posts: tuple[tuple[str, dict[str, object]], ...] = (
        ("/api/auctions/1/schedule", {}),
        ("/api/auctions/1/consign", {"on_date": "2026-01-01"}),
        ("/api/auctions/1/close", {}),
        ("/api/auctions/1/cancel", {}),
        ("/api/auctions/1/settle", {"lines": [], "fees": []}),
        (
            "/api/auctions/1/lots",
            {"lot_number": "1", "item_id": 1, "price": "1.00"},
        ),
    )
    for path, body in posts:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers=customer_headers).status_code == 403
    for path in ("/api/auctions/1/lots/1",):
        assert client.delete(path).status_code == 401
        assert client.delete(path, headers=customer_headers).status_code == 403
        assert client.patch(path, json={"lot_number": "1A"}).status_code == 401
        assert (
            client.patch(
                path, json={"lot_number": "1A"}, headers=customer_headers
            ).status_code
            == 403
        )


# --------------------------------------------------------------------------
# Creating, listing, editing
# --------------------------------------------------------------------------


def test_creating_and_listing_an_auction(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    heritage_venue: SalesVenue,
) -> None:
    response = client.post(
        "/api/auctions",
        headers=admin_headers,
        json={
            "venue": heritage_venue.code,
            "title": "September Signature Sale",
            "external_id": "SIG-2026-09",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "draft"
    assert body["venue"] == heritage_venue.code
    assert body["lots"] == []
    assert body["version"] == 1

    listed = client.get("/api/auctions", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()["auctions"]] == [body["id"]]


def test_an_unknown_venue_code_is_unprocessable(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/auctions",
        headers=admin_headers,
        json={"venue": "no-such-platform", "title": "Whatever"},
    )
    assert response.status_code == 422, response.text


def test_an_auction_on_the_web_store_is_unprocessable(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """The shop sells at a fixed price through checkout; it runs no auctions.

    A sale recorded on the store has no order status to start at
    (`sales_writes._STATUS_BY_VENUE_KIND` has no `own_store` key), so an
    auction there could take lots -- pulling their coins out of the shop --
    and then never settle one as sold. Refused when the auction is created.
    """
    store_code = db.scalars(
        select(SalesVenue.code).where(SalesVenue.is_own_store)
    ).one()
    response = client.post(
        "/api/auctions",
        headers=admin_headers,
        json={"venue": store_code, "title": "A sale the shop cannot run"},
    )
    assert response.status_code == 422, response.text
    assert "store" in response.json()["detail"]
    assert db.scalars(select(Auction.id)).all() == []


def test_auction_list_filtered_by_venue_and_status(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    scheduled_auction: Auction,
    ebay_venue: SalesVenue,
) -> None:
    other = Auction(sales_venue_id=ebay_venue.id, title="Weekly eBay auction")
    db.add(other)
    db.commit()

    by_venue = client.get(
        "/api/auctions",
        headers=admin_headers,
        params={"venue": ebay_venue.code},
    )
    assert [row["id"] for row in by_venue.json()["auctions"]] == [other.id]

    by_status = client.get(
        "/api/auctions", headers=admin_headers, params={"status": "scheduled"}
    )
    assert [row["id"] for row in by_status.json()["auctions"]] == [scheduled_auction.id]


def test_updating_an_auction_and_a_stale_version(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    draft_auction: Auction,
) -> None:
    good = client.patch(
        f"/api/auctions/{draft_auction.id}",
        headers=admin_headers,
        json={"title": "Renamed Sale", "version": 1},
    )
    assert good.status_code == 200, good.text
    assert good.json()["title"] == "Renamed Sale"
    assert good.json()["version"] == 2

    stale = client.patch(
        f"/api/auctions/{draft_auction.id}",
        headers=admin_headers,
        json={"title": "Renamed Again", "version": 1},
    )
    assert stale.status_code == 409, stale.text

    db.expire_all()
    unchanged = db.get(Auction, draft_auction.id)
    assert unchanged is not None
    assert unchanged.title == "Renamed Sale"


def test_updating_an_auction_s_title_to_null_is_unprocessable(
    client: TestClient, admin_headers: dict[str, str], draft_auction: Auction
) -> None:
    response = client.patch(
        f"/api/auctions/{draft_auction.id}",
        headers=admin_headers,
        json={"title": None},
    )
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------
# Lots
# --------------------------------------------------------------------------


def test_adding_a_single_item_lot(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    make_item: ItemFactory,
) -> None:
    item = make_item()
    response = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={
            "lot_number": "1",
            "item_id": item.id,
            "price": "25.00",
            "reserve": "20.00",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["lots"]) == 1
    lot = body["lots"][0]
    assert lot["lot_number"] == "1"
    # The exact string, not a Decimal comparison: money crosses this API as a
    # string and `Decimal(25.0) == Decimal("25.00")` would hide a regression
    # to float serialization.
    assert lot["listing"]["price"] == "25.00"
    assert lot["reserve"] == "20.00"
    assert lot["listing"]["format"] == "auction"
    # A single item offered in an auction is a sales lot of one (spec, *Three
    # kinds of lot*): `app.auctions.add_lot` wraps it through `_lot_of_one`,
    # so the listing it creates names a lot, never the item directly, and
    # `item_code`/`item_id` stay null the same way a multi-item lot's do.
    assert lot["listing"]["item_code"] is None
    assert lot["listing"]["member_count"] == 1
    assert lot["listing"]["item_title"] == item.source_title
    assert lot["result"] is None


def test_adding_an_assembled_lot(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    make_item: ItemFactory,
) -> None:
    items = [make_item(title=f"Member {i}") for i in range(2)]
    lot = build_lot(db, items, title="Two Morgan Dollars")

    response = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "lot_id": lot.id, "price": "50.00"},
    )
    assert response.status_code == 201, response.text
    row = response.json()["lots"][0]
    assert row["listing"]["sales_lot_id"] == lot.id
    assert row["listing"]["member_count"] == 2
    assert row["listing"]["item_title"] == "Two Morgan Dollars"


def test_adding_a_lot_with_both_ids_is_unprocessable(
    client: TestClient,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    make_item: ItemFactory,
) -> None:
    item = make_item()
    response = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": item.id, "lot_id": 999, "price": "1.00"},
    )
    assert response.status_code == 422, response.text


def test_adding_a_lot_with_neither_id_is_unprocessable(
    client: TestClient, admin_headers: dict[str, str], draft_auction: Auction
) -> None:
    response = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "price": "1.00"},
    )
    assert response.status_code == 422, response.text


def test_adding_a_lot_with_an_unknown_item_id_is_unprocessable(
    client: TestClient, admin_headers: dict[str, str], draft_auction: Auction
) -> None:
    response = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": 999999, "price": "1.00"},
    )
    assert response.status_code == 422, response.text


def test_adding_a_lot_to_a_closed_auction_is_refused(
    client: TestClient,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
    make_item: ItemFactory,
) -> None:
    """`AuctionRefused` from `add_lot`, reaching the console as a 409."""
    item = make_item()
    response = client.post(
        f"/api/auctions/{closed_auction_of_two_lots.id}/lots",
        headers=admin_headers,
        json={"lot_number": "3", "item_id": item.id, "price": "1.00"},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert "closed" in body["detail"]
    # `refused` is built from the exception's own `refusals`
    # attribute, not by splitting `detail` on "; " -- a single-reason
    # `AuctionRefused` from `add_lot` names no one lot, so `lot_number` is
    # null.
    assert body["refused"] == [{"reason": body["detail"], "lot_number": None}]


def test_adding_a_lot_with_a_repeated_lot_number_is_a_conflict(
    client: TestClient,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    make_item: ItemFactory,
) -> None:
    first = make_item()
    second = make_item()
    assert (
        client.post(
            f"/api/auctions/{draft_auction.id}/lots",
            headers=admin_headers,
            json={"lot_number": "1", "item_id": first.id, "price": "1.00"},
        ).status_code
        == 201
    )
    again = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": second.id, "price": "1.00"},
    )
    assert again.status_code == 409, again.text
    assert "1" in again.json()["detail"]


def test_a_lost_race_adding_a_lot_is_not_blamed_on_the_lot_number(
    client: TestClient,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only `uq_auction_lot_auction_lot_number` means a repeated lot number.

    Adding a lot can also lose to another unique index -- the same coin
    entering a lot or an offer elsewhere at that moment
    (`uq_sales_lot_item_open`, `uq_offer_claim_active`). Telling the operator
    to pick another lot number for that sends them to change the one thing
    that was not in the way. The writer is patched to raise what the losing
    insert raises, since a single session cannot lose a race to itself.
    """
    item = make_item()

    def _lose_the_race(*args: object, **kwargs: object) -> None:
        """Fail as the second of two inserts of one open lot membership does."""
        raise IntegrityError(
            "INSERT INTO sales_lot_item ...",
            {},
            Exception(
                "duplicate key value violates unique constraint "
                '"uq_sales_lot_item_open"'
            ),
        )

    monkeypatch.setattr(auctions, "add_lot", _lose_the_race)
    response = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "7", "item_id": item.id, "price": "1.00"},
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "lot_number" not in detail
    assert "'7'" not in detail


def test_removing_a_lot_resumes_a_paused_store_listing(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    make_listing: ListingFactory,
) -> None:
    store_listing = make_listing(quantity_available=1)
    item = item_of(store_listing)

    added = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": item.id, "price": "10.00"},
    )
    assert added.status_code == 201, added.text
    lot_id = added.json()["lots"][0]["id"]

    db.expire_all()
    assert db.get_one(Listing, store_listing.id).status is ListingStatus.paused

    removed = client.delete(
        f"/api/auctions/{draft_auction.id}/lots/{lot_id}", headers=admin_headers
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["lots"] == []

    db.expire_all()
    assert db.get_one(Listing, store_listing.id).status is ListingStatus.active


def test_removing_a_lot_from_a_closed_auction_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
) -> None:
    """`remove_lot` refuses `closed` unconditionally."""
    lot_id = _auction_lots(db, closed_auction_of_two_lots)[0].id
    response = client.delete(
        f"/api/auctions/{closed_auction_of_two_lots.id}/lots/{lot_id}",
        headers=admin_headers,
    )
    assert response.status_code == 409, response.text
    assert "closed" in response.json()["detail"]


def test_renumbering_a_lot(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    make_item: ItemFactory,
) -> None:
    item = make_item()
    added = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": item.id, "price": "1.00"},
    )
    lot_id = added.json()["lots"][0]["id"]

    response = client.patch(
        f"/api/auctions/{draft_auction.id}/lots/{lot_id}",
        headers=admin_headers,
        json={"lot_number": "1A", "reserve": "15.00"},
    )
    assert response.status_code == 200, response.text
    row = response.json()["lots"][0]
    assert row["lot_number"] == "1A"
    assert row["reserve"] == "15.00"


def test_renumbering_a_lot_in_a_closed_auction_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
) -> None:
    """`draft`, `scheduled` or `consigned` only, never `closed`.

    Once closed, the lot numbers are part of the record a house's statement
    is reconciled against, and a reserve is meaningless -- the same boundary
    `remove_lot` already draws (`_LOTS_REMOVABLE`). The other side of this --
    that the change is allowed before close -- is `test_renumbering_a_lot`
    (`draft`) and, below,
    `test_renumbering_a_lot_while_scheduled` and
    `test_renumbering_a_lot_while_consigned`.
    """
    lot = _auction_lots(db, closed_auction_of_two_lots)[0]
    response = client.patch(
        f"/api/auctions/{closed_auction_of_two_lots.id}/lots/{lot.id}",
        headers=admin_headers,
        json={"lot_number": "1A"},
    )
    assert response.status_code == 409, response.text
    assert "closed" in response.json()["detail"]

    db.expire_all()
    assert db.get_one(AuctionLot, lot.id).lot_number == lot.lot_number


def test_renumbering_is_decided_on_the_auction_as_it_is_not_as_it_was_read(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    scheduled_auction: Auction,
    make_item: ItemFactory,
) -> None:
    """A close that landed after this request read the auction still refuses.

    The other operator's close is simulated rather than threaded, the way
    `test_a_second_sale_of_the_same_listing_is_refused_as_not_on_offer`
    simulates a sale: the row says `closed` while the session's instance
    still says `scheduled`, and `synchronize_session=False` is what keeps it
    stale. A gate that answers from the instance in hand lets the lot be
    renumbered on a closed auction; one that takes the auction row and
    re-reads it refuses.
    """
    item = make_item()
    added = client.post(
        f"/api/auctions/{scheduled_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": item.id, "price": "1.00"},
    )
    assert added.status_code == 201, added.text
    lot_id = added.json()["lots"][0]["id"]

    # Loaded before the row changes, so the identity map holds `scheduled`.
    assert scheduled_auction.status is AuctionStatus.scheduled
    db.execute(
        update(Auction)
        .where(Auction.id == scheduled_auction.id)
        .values(status=AuctionStatus.closed)
        .execution_options(synchronize_session=False)
    )
    assert scheduled_auction.status is AuctionStatus.scheduled

    response = client.patch(
        f"/api/auctions/{scheduled_auction.id}/lots/{lot_id}",
        headers=admin_headers,
        json={"lot_number": "1A"},
    )
    assert response.status_code == 409, response.text
    assert "closed" in response.json()["detail"]
    db.expire_all()
    assert db.get_one(AuctionLot, lot_id).lot_number == "1"


def test_renumbering_a_lot_while_scheduled(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    scheduled_auction: Auction,
    make_item: ItemFactory,
) -> None:
    """The allowed side of the renumbering rule, for `scheduled`.

    `test_renumbering_a_lot` covers `draft`.
    """
    item = make_item()
    added = client.post(
        f"/api/auctions/{scheduled_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": item.id, "price": "1.00"},
    )
    lot_id = added.json()["lots"][0]["id"]

    response = client.patch(
        f"/api/auctions/{scheduled_auction.id}/lots/{lot_id}",
        headers=admin_headers,
        json={"lot_number": "1A"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["lots"][0]["lot_number"] == "1A"


def test_renumbering_a_lot_while_consigned(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    scheduled_auction: Auction,
    make_item: ItemFactory,
) -> None:
    """The allowed side of the renumbering rule, for `consigned`.

    A house may still fix a lot number after custody moved, before the sale
    itself runs.
    """
    item = make_item()
    added = client.post(
        f"/api/auctions/{scheduled_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": item.id, "price": "1.00"},
    )
    lot_id = added.json()["lots"][0]["id"]
    consigned = client.post(
        f"/api/auctions/{scheduled_auction.id}/consign",
        headers=admin_headers,
        json={"on_date": "2026-10-01"},
    )
    assert consigned.status_code == 200, consigned.text
    assert consigned.json()["status"] == "consigned"

    response = client.patch(
        f"/api/auctions/{scheduled_auction.id}/lots/{lot_id}",
        headers=admin_headers,
        json={"lot_number": "1A"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["lots"][0]["lot_number"] == "1A"


def test_renumbering_a_lot_to_null_is_unprocessable(
    client: TestClient,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    make_item: ItemFactory,
) -> None:
    item = make_item()
    added = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": item.id, "price": "1.00"},
    )
    lot_id = added.json()["lots"][0]["id"]
    response = client.patch(
        f"/api/auctions/{draft_auction.id}/lots/{lot_id}",
        headers=admin_headers,
        json={"lot_number": None},
    )
    assert response.status_code == 422, response.text


def test_renumbering_a_lot_to_a_number_already_used_is_a_conflict(
    client: TestClient,
    admin_headers: dict[str, str],
    draft_auction: Auction,
    make_item: ItemFactory,
) -> None:
    first = make_item()
    second = make_item()
    client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": first.id, "price": "1.00"},
    )
    added = client.post(
        f"/api/auctions/{draft_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "2", "item_id": second.id, "price": "1.00"},
    )
    lot_id = added.json()["lots"][1]["id"]

    response = client.patch(
        f"/api/auctions/{draft_auction.id}/lots/{lot_id}",
        headers=admin_headers,
        json={"lot_number": "1"},
    )
    assert response.status_code == 409, response.text


# --------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------


def test_scheduling_an_auction(
    client: TestClient, admin_headers: dict[str, str], draft_auction: Auction
) -> None:
    response = client.post(
        f"/api/auctions/{draft_auction.id}/schedule", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "scheduled"


def test_scheduling_twice_is_refused(
    client: TestClient, admin_headers: dict[str, str], scheduled_auction: Auction
) -> None:
    response = client.post(
        f"/api/auctions/{scheduled_auction.id}/schedule", headers=admin_headers
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert "scheduled" in body["detail"]
    assert body["refused"] == [{"reason": body["detail"], "lot_number": None}]


def test_consigning_a_marketplace_auction_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    ebay_venue: SalesVenue,
) -> None:
    row = Auction(sales_venue_id=ebay_venue.id, title="Weekly eBay auction")
    db.add(row)
    db.commit()
    auctions.schedule(db, row)
    db.commit()

    response = client.post(
        f"/api/auctions/{row.id}/consign",
        headers=admin_headers,
        json={"on_date": "2026-10-01"},
    )
    assert response.status_code == 409, response.text
    assert "auction house" in response.json()["detail"]


def test_consigning_moves_every_item_to_the_house(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    scheduled_auction: Auction,
    make_item: ItemFactory,
) -> None:
    item = make_item()
    added = client.post(
        f"/api/auctions/{scheduled_auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": item.id, "price": "1.00"},
    )
    assert added.status_code == 201, added.text

    response = client.post(
        f"/api/auctions/{scheduled_auction.id}/consign",
        headers=admin_headers,
        json={"on_date": "2026-10-01"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "consigned"
    assert body["consigned_on"] == "2026-10-01"

    db.expire_all()
    refreshed = db.get_one(InventoryItem, item.id)
    assert refreshed.storage_location_id is not None
    location = db.get_one(StorageLocation, refreshed.storage_location_id)
    assert location.kind.code == "consigned"


def test_consign_with_no_seed_is_a_500_naming_the_seeder(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    scheduled_auction: Auction,
) -> None:
    """The operator sees the actionable message, not a bare 500.

    A database migrated but not loaded with `python -m app.seeding load` has
    no `consigned` kind. `app.main` answers `errors.ReferenceDataMissing`
    with the message `app.auctions.consign` raises -- registered on that
    class, not on `RuntimeError` itself
    (`test_an_unrelated_runtime_error_is_not_swallowed`).
    """
    db.execute(
        delete(StorageLocationKind).where(StorageLocationKind.code == "consigned")
    )
    db.commit()

    response = client.post(
        f"/api/auctions/{scheduled_auction.id}/consign",
        headers=admin_headers,
        json={"on_date": "2026-10-01"},
    )
    assert response.status_code == 500, response.text
    assert "app.seeding load" in response.json()["detail"]


def test_a_shopper_is_not_told_how_the_server_is_misconfigured(
    client: TestClient,
    customer_headers: dict[str, str],
    listing: Listing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkout raises the same exception; its text is not the shopper's.

    "run `alembic upgrade head` on this database" is an operator's
    instruction. A customer's checkout gets a generic 500 -- the full
    message still goes to the log, and an administrator's request still
    carries it (the test above).
    """
    from app import order_writes
    from app.errors import ReferenceDataMissing

    def missing(*_args: object, **_kwargs: object) -> int:
        """Fail as the venue lookup does on a database with no web store."""
        raise ReferenceDataMissing(
            "No web store platform: run `alembic upgrade head` on this database"
        )

    monkeypatch.setattr(order_writes, "store_venue_id", missing)

    response = client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=customer_headers,
    )

    assert response.status_code == 500, response.text
    detail = response.json()["detail"]
    assert "alembic" not in detail
    assert "not fully set up" in detail


def test_an_unrelated_runtime_error_is_not_swallowed(
    client: TestClient,
    admin_headers: dict[str, str],
    scheduled_auction: Auction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only `errors.ReferenceDataMissing` is caught.

    `RuntimeError` is also the base of `NotImplementedError` and
    `RecursionError`, and of every incidental `RuntimeError` anywhere in the
    app. Handling the base class would take a genuine bug away from
    `ServerErrorMiddleware` (no traceback logged) and from `TestClient` (no
    re-raise), turning it into a tidy 500 wearing a "server precondition"
    label. A plain `RuntimeError`
    from a monkeypatched writer -- not `ReferenceDataMissing` -- must still
    escape this client unhandled, the same as any other unexpected crash.
    """

    def _boom(*args: object, **kwargs: object) -> None:
        """Fail with an error that has nothing to do with reference data."""
        raise RuntimeError("something else entirely, not a seeding problem")

    monkeypatch.setattr(auctions, "consign", _boom)
    with pytest.raises(RuntimeError, match="something else entirely"):
        client.post(
            f"/api/auctions/{scheduled_auction.id}/consign",
            headers=admin_headers,
            json={"on_date": "2026-10-01"},
        )


def _consigned_item(
    client: TestClient,
    admin_headers: dict[str, str],
    auction: Auction,
    make_item: ItemFactory,
) -> tuple[InventoryItem, int]:
    """One item in a lot of `auction`, consigned through the API; its lot's id."""
    item = make_item()
    added = client.post(
        f"/api/auctions/{auction.id}/lots",
        headers=admin_headers,
        json={"lot_number": "1", "item_id": item.id, "price": "1.00"},
    )
    assert added.status_code == 201, added.text
    consigned = client.post(
        f"/api/auctions/{auction.id}/consign",
        headers=admin_headers,
        json={"on_date": "2026-10-01"},
    )
    assert consigned.status_code == 200, consigned.text
    return item, added.json()["lots"][0]["id"]


def _home(db: Session) -> StorageLocation:
    """A plain location to bring consigned items back to."""
    kind = db.scalars(
        select(StorageLocationKind).where(StorageLocationKind.code == "home")
    ).one()
    location = StorageLocation(storage_location_kind_id=kind.id)
    db.add(location)
    db.commit()
    return location


def _last_move(db: Session, item: InventoryItem) -> LocationHistory:
    """The newest row of the item's location history, read afresh."""
    db.expire_all()
    return db.scalars(
        select(LocationHistory)
        .where(LocationHistory.inventory_item_id == item.id)
        .order_by(LocationHistory.id.desc())
        .limit(1)
    ).one()


def test_removing_a_consigned_lot_records_who_brought_it_back(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    admin_user: User,
    scheduled_auction: Auction,
    make_item: ItemFactory,
) -> None:
    item, lot_id = _consigned_item(client, admin_headers, scheduled_auction, make_item)
    home = _home(db)
    response = client.delete(
        f"/api/auctions/{scheduled_auction.id}/lots/{lot_id}",
        headers=admin_headers,
        params={"returned_to_location_id": home.id},
    )
    assert response.status_code == 200, response.text
    move = _last_move(db, item)
    assert move.storage_location_id == home.id
    assert move.moved_by_id == admin_user.id


def test_cancelling_a_consigned_auction_records_who_brought_it_back(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    admin_user: User,
    scheduled_auction: Auction,
    make_item: ItemFactory,
) -> None:
    item, _ = _consigned_item(client, admin_headers, scheduled_auction, make_item)
    home = _home(db)
    response = client.post(
        f"/api/auctions/{scheduled_auction.id}/cancel",
        headers=admin_headers,
        json={"returned_to_location_id": home.id},
    )
    assert response.status_code == 200, response.text
    move = _last_move(db, item)
    assert move.storage_location_id == home.id
    assert move.moved_by_id == admin_user.id


def test_closing_and_cancelling_an_auction(
    client: TestClient, admin_headers: dict[str, str], scheduled_auction: Auction
) -> None:
    closed = client.post(
        f"/api/auctions/{scheduled_auction.id}/close", headers=admin_headers
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "closed"

    cancelled = client.post(
        f"/api/auctions/{scheduled_auction.id}/cancel",
        headers=admin_headers,
        json={},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"


def test_cancelling_an_already_cancelled_auction_is_refused(
    client: TestClient, admin_headers: dict[str, str], scheduled_auction: Auction
) -> None:
    first = client.post(
        f"/api/auctions/{scheduled_auction.id}/cancel", headers=admin_headers, json={}
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/auctions/{scheduled_auction.id}/cancel", headers=admin_headers, json={}
    )
    assert second.status_code == 409, second.text


# --------------------------------------------------------------------------
# Settlement
# --------------------------------------------------------------------------


def test_settling_creates_orders_and_returns_them(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
) -> None:
    lots = _auction_lots(db, closed_auction_of_two_lots)
    response = client.post(
        f"/api/auctions/{closed_auction_of_two_lots.id}/settle",
        headers=admin_headers,
        json={
            "lines": [
                {
                    "auction_lot_id": lots[0].id,
                    "result": "sold",
                    "hammer_price": "150.00",
                    "buyer_username": "amy",
                },
                {
                    "auction_lot_id": lots[1].id,
                    "result": "sold",
                    "hammer_price": "90.00",
                    "buyer_username": "amy",
                },
            ],
            "fees": [
                {
                    "buyer_username": "amy",
                    "fees": [{"kind": "commission", "amount": "24.00"}],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auction"]["status"] == "settled"
    assert len(body["orders"]) == 1
    order = body["orders"][0]
    # The exact string, not a Decimal comparison -- money crosses the wire as
    # a string, never a JavaScript number.
    assert order["total_amount"] == "240.00"
    assert order["fee_total"] == "24.00"
    assert order["net_amount"] == "216.00"
    assert order["buyer"] == "amy"
    assert order["external_order_id"] == "SIG-2026-09"
    assert sorted(order["item_codes"]) == sorted(
        code for row in lots for code in _item_codes(row.listing)
    )

    sold_lots = body["auction"]["lots"]
    assert {row["result"] for row in sold_lots} == {"sold"}
    assert {row["hammer_price"] for row in sold_lots} == {"150.00", "90.00"}
    assert {row["buyer"] for row in sold_lots} == {"amy"}

    db.expire_all()
    assert (
        db.get_one(Auction, closed_auction_of_two_lots.id).status
        is AuctionStatus.settled
    )


def test_settlement_refusal_lists_every_problem_lot(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
) -> None:
    """The console shows a grid; fixing one problem at a time is miserable."""
    lots = _auction_lots(db, closed_auction_of_two_lots)
    response = client.post(
        f"/api/auctions/{closed_auction_of_two_lots.id}/settle",
        headers=admin_headers,
        # Neither lot given a result at all: two problems, not one.
        json={"lines": [], "fees": []},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert len(body["refused"]) == 2
    # `refused` is structured from `auctions.AuctionRefusal`, not
    # parsed back out of `detail` -- each problem here is about exactly one
    # lot, and its own `lot_number` says which, with no re-parsing needed.
    assert sorted(row["lot_number"] for row in body["refused"]) == sorted(
        row.lot_number for row in lots
    )
    reasons = " ".join(row["reason"] for row in body["refused"])
    for row in lots:
        assert row.lot_number in reasons
    # `detail` still carries the whole message, not just the first problem.
    assert body["detail"].count(";") >= 1


def test_two_fee_groups_for_one_buyer_are_unprocessable(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
) -> None:
    """Neither group may vanish: the same spelling twice is refused whole.

    The router keys the fee groups by `buyer_username`, so an exact repeat
    would leave only the second group's fees on the order. Both lots sell to
    the buyer and both groups carry a real fee, so a request that got through
    would settle the auction with 5.00 of fees where 15.00 was entered.
    """
    lots = _auction_lots(db, closed_auction_of_two_lots)
    response = client.post(
        f"/api/auctions/{closed_auction_of_two_lots.id}/settle",
        headers=admin_headers,
        json={
            "lines": [
                {
                    "auction_lot_id": row.id,
                    "result": "sold",
                    "hammer_price": "100.00",
                    "buyer_username": "amy",
                }
                for row in lots
            ],
            "fees": [
                {
                    "buyer_username": "amy",
                    "fees": [{"kind": "commission", "amount": "10.00"}],
                },
                {
                    "buyer_username": "amy",
                    "fees": [{"kind": "processing", "amount": "5.00"}],
                },
            ],
        },
    )
    assert response.status_code == 422, response.text
    assert "fees given twice" in response.text
    assert "amy" in response.text
    db.expire_all()
    assert (
        db.get_one(Auction, closed_auction_of_two_lots.id).status
        is AuctionStatus.closed
    )
    assert db.scalar(select(func.count()).select_from(SalesOrder)) == 0


def test_settling_brings_an_unsold_consigned_lot_back_to_the_chosen_location(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    admin_user: User,
    scheduled_auction: Auction,
    make_item: ItemFactory,
) -> None:
    """`returned_to_location_id` reaches `settle`, and so does who settled."""
    item, lot_id = _consigned_item(client, admin_headers, scheduled_auction, make_item)
    closed = client.post(
        f"/api/auctions/{scheduled_auction.id}/close", headers=admin_headers
    )
    assert closed.status_code == 200, closed.text
    home = _home(db)

    response = client.post(
        f"/api/auctions/{scheduled_auction.id}/settle",
        headers=admin_headers,
        json={
            "lines": [{"auction_lot_id": lot_id, "result": "unsold"}],
            "fees": [],
            "returned_to_location_id": home.id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["auction"]["status"] == "settled"
    assert body["auction"]["consigned_on"] is None
    assert body["orders"] == []
    move = _last_move(db, item)
    assert move.storage_location_id == home.id
    assert move.moved_by_id == admin_user.id
    assert db.get_one(InventoryItem, item.id).storage_location_id == home.id


def test_settling_with_an_unknown_return_location_is_unprocessable(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    scheduled_auction: Auction,
    make_item: ItemFactory,
) -> None:
    """An id no location wears is bad input, refused before anything moves."""
    item, lot_id = _consigned_item(client, admin_headers, scheduled_auction, make_item)
    closed = client.post(
        f"/api/auctions/{scheduled_auction.id}/close", headers=admin_headers
    )
    assert closed.status_code == 200, closed.text
    db.expire_all()
    consigned_location_id = db.get_one(InventoryItem, item.id).storage_location_id
    assert consigned_location_id is not None

    response = client.post(
        f"/api/auctions/{scheduled_auction.id}/settle",
        headers=admin_headers,
        json={
            "lines": [{"auction_lot_id": lot_id, "result": "unsold"}],
            "fees": [],
            "returned_to_location_id": 999_999_999,
        },
    )
    assert response.status_code == 422, response.text
    assert "Unknown returned_to_location_id" in response.json()["detail"]
    db.expire_all()
    assert db.get_one(Auction, scheduled_auction.id).status is AuctionStatus.closed
    assert (
        db.get_one(InventoryItem, item.id).storage_location_id == consigned_location_id
    )


def test_a_marketplace_lot_must_name_its_buyer(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    ebay_venue: SalesVenue,
    make_item: ItemFactory,
) -> None:
    row = Auction(sales_venue_id=ebay_venue.id, title="Weekly eBay auction")
    db.add(row)
    db.commit()
    auctions.schedule(db, row)
    auctions.add_lot(
        db, row, make_item(), lot_number="1", reserve=None, price=Decimal("1.00")
    )
    auctions.close(db, row)
    db.commit()
    lot = _auction_lots(db, row)[0]

    response = client.post(
        f"/api/auctions/{row.id}/settle",
        headers=admin_headers,
        json={
            "lines": [
                {"auction_lot_id": lot.id, "result": "sold", "hammer_price": "10.00"}
            ],
            "fees": [],
        },
    )
    assert response.status_code == 409, response.text
    assert "buyer" in response.json()["detail"]


# --------------------------------------------------------------------------
# Per-class handlers, not `except` order
# --------------------------------------------------------------------------


def test_settlement_input_invalid_is_a_422_not_a_409(
    client: TestClient,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`SettlementInputInvalid` is a subclass of `AuctionRefused`.

    An ordered `except AuctionRefused` before `except SettlementInputInvalid`
    would swallow this and answer 409; a registered-by-class handler cannot,
    because Starlette resolves by the raised exception's own class, walking
    its MRO for the most specific registered handler -- see `app.main`'s own
    note. Patching `auctions.settle` to raise directly, the same way
    `test_sale_input_invalid_from_record_sale_is_a_422_not_a_409` isolates
    the router's dispatch from anything a real settlement grid could trigger.
    """

    def _raise_invalid(*args: object, **kwargs: object) -> list[SalesOrder]:
        """Stand in for settlement, refusing its input as malformed."""
        raise auctions.SettlementInputInvalid("deliberately malformed, for the test")

    monkeypatch.setattr(auctions, "settle", _raise_invalid)
    response = client.post(
        f"/api/auctions/{closed_auction_of_two_lots.id}/settle",
        headers=admin_headers,
        json={"lines": [], "fees": []},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["detail"] == "deliberately malformed, for the test"
    assert body["refused"] == [
        {"reason": "deliberately malformed, for the test", "lot_number": None}
    ]


def test_a_plain_auction_refused_from_settle_is_still_a_409(
    client: TestClient,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The base `AuctionRefused` -- a genuine conflict -- still maps to 409."""

    def _raise_refused(*args: object, **kwargs: object) -> list[SalesOrder]:
        """Stand in for settlement, refusing the auction as a conflict."""
        raise auctions.AuctionRefused("a real conflict, for the test")

    monkeypatch.setattr(auctions, "settle", _raise_refused)
    response = client.post(
        f"/api/auctions/{closed_auction_of_two_lots.id}/settle",
        headers=admin_headers,
        json={"lines": [], "fees": []},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["detail"] == "a real conflict, for the test"
    assert body["refused"] == [
        {"reason": "a real conflict, for the test", "lot_number": None}
    ]


def test_sale_input_invalid_from_settle_is_a_422_not_a_409(
    client: TestClient,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`settle` can also raise `sales_writes`' pair, from `record_sale_lines`.

    A second, independent hierarchy from `AuctionRefused`'s own, which the
    per-class handlers cover without any `except` order to get right.
    """

    def _raise_invalid(*args: object, **kwargs: object) -> list[SalesOrder]:
        """Stand in for settlement, the sale writer refusing its input."""
        raise sales_writes.SaleInputInvalid("deliberately malformed, for the test")

    monkeypatch.setattr(auctions, "settle", _raise_invalid)
    response = client.post(
        f"/api/auctions/{closed_auction_of_two_lots.id}/settle",
        headers=admin_headers,
        json={"lines": [], "fees": []},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["detail"] == "deliberately malformed, for the test"
    assert body["refused"] == [
        {"reason": "deliberately malformed, for the test", "lot_number": None}
    ]


def test_sale_refused_from_settle_is_a_409(
    client: TestClient,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_refused(*args: object, **kwargs: object) -> list[SalesOrder]:
        """Stand in for settlement, the sale writer refusing a conflict."""
        raise sales_writes.SaleRefused("a real conflict, for the test")

    monkeypatch.setattr(auctions, "settle", _raise_refused)
    response = client.post(
        f"/api/auctions/{closed_auction_of_two_lots.id}/settle",
        headers=admin_headers,
        json={"lines": [], "fees": []},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["detail"] == "a real conflict, for the test"
    # `sales_writes.SaleRefused` carries no `refusals` attribute --
    # `record_sale_lines` stops at the first problem rather than collecting
    # a grid's worth -- so `app.main._refusal_body` falls back to a single
    # entry built from the plain message, not an empty or missing list.
    assert body["refused"] == [
        {"reason": "a real conflict, for the test", "lot_number": None}
    ]


def test_a_semicolon_in_a_reason_survives_the_round_trip(
    client: TestClient,
    admin_headers: dict[str, str],
    closed_auction_of_two_lots: Auction,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`refused` is built from `AuctionRefused.refusals`, never text-split.

    Splitting `str(exc)` on `"; "` -- a text convention, not a type-checked
    contract -- would silently cut this single problem in two. Passing
    `refusals` explicitly, the way `auctions.settle` itself does, a reason
    containing a real semicolon reaches the console as the one entry it is.
    """
    problem = "lot 1 and lot 2 cannot both be results; that is the whole problem"

    def _raise_refused(*args: object, **kwargs: object) -> list[SalesOrder]:
        """Stand in for settlement, refusing one lot for a reason with a semicolon."""
        raise auctions.AuctionRefused(
            f"auction #{closed_auction_of_two_lots.id} cannot be settled: {problem}",
            refusals=[auctions.AuctionRefusal(reason=problem, lot_number="1")],
        )

    monkeypatch.setattr(auctions, "settle", _raise_refused)
    response = client.post(
        f"/api/auctions/{closed_auction_of_two_lots.id}/settle",
        headers=admin_headers,
        json={"lines": [], "fees": []},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    # Exactly one entry, the reason intact, semicolon and all: splitting on
    # "; " would produce two.
    assert body["refused"] == [{"reason": problem, "lot_number": "1"}]
