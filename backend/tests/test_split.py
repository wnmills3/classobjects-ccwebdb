"""Breaking a lot into pieces, and dividing its cost between them."""

from __future__ import annotations

from decimal import Decimal

from app.models import InventoryItem, Listing
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tests.test_schema import make_item

TUBE = {
    "mode": "equal",
    "pieces": [{"source_title": f"Silver Round {n}"} for n in range(1, 5)],
}

#: A 1964 mint set, split by face value. The cent must not carry the same
#: cost as the half dollar.
MINT_SET = {
    "mode": "relative",
    "pieces": [
        {"source_title": "1964 Cent", "relative_value": "0.01"},
        {"source_title": "1964 Nickel", "relative_value": "0.05"},
        {"source_title": "1964 Dime", "relative_value": "0.10"},
        {"source_title": "1964 Quarter", "relative_value": "0.25"},
        {"source_title": "1964 Half Dollar", "relative_value": "0.50"},
    ],
}


def lot(db: Session, **overrides: object) -> InventoryItem:
    defaults = {
        "source_title": "Tube of 4 Silver Rounds",
        "piece_count": 4,
        "item_cost": Decimal("100.00"),
        "shipping_cost": Decimal("8.00"),
    }
    defaults.update(overrides)
    return make_item(db, **defaults)


def do_split(
    client: TestClient, headers: dict[str, str], item_id: int, payload: dict
) -> None:
    return client.post(f"/api/inventory/{item_id}/split", json=payload, headers=headers)


# ---------------------------------------------------------------------------
# Equal allocation
# ---------------------------------------------------------------------------


def test_equal_split_divides_the_cost_evenly(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = lot(db)
    body = do_split(client, admin_headers, parent.id, TUBE).json()

    assert body["allocated_cost"] == "100.00"
    assert body["allocated_shipping"] == "8.00"
    assert [p["item_cost"] for p in body["pieces"]] == ["25.00"] * 4
    assert [p["shipping_cost"] for p in body["pieces"]] == ["2.00"] * 4


def test_price_and_shipping_reconcile_to_the_penny(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Price and shipping reconcile exactly, whatever the total.

    An awkward total, split three ways -- the case naive division loses a
    penny on.
    """
    parent = lot(db, item_cost=Decimal("100.00"), shipping_cost=Decimal("0.01"))
    payload = {"mode": "equal", "pieces": [{"source_title": f"P{n}"} for n in range(3)]}

    body = do_split(client, admin_headers, parent.id, payload).json()

    assert body["allocated_cost"] == body["parent_cost"] == "100.00"
    assert body["allocated_shipping"] == body["parent_shipping"] == "0.01"


def test_a_piece_holding_several_items_carries_several_shares(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A piece holding several items carries several shares.

    An equal split is per piece, not per row: a child holding three coins
    takes three coins' worth of the cost.
    """
    parent = lot(
        db, item_cost=Decimal("100.00"), shipping_cost=Decimal("0.00"), piece_count=4
    )
    payload = {
        "mode": "equal",
        "pieces": [
            {"source_title": "Three of them", "piece_count": 3},
            {"source_title": "The odd one", "piece_count": 1},
        ],
    }
    body = do_split(client, admin_headers, parent.id, payload).json()
    prices = {p["source_title"]: p["item_cost"] for p in body["pieces"]}
    assert prices == {"Three of them": "75.00", "The odd one": "25.00"}


# ---------------------------------------------------------------------------
# Relative allocation
# ---------------------------------------------------------------------------


def test_relative_split_follows_the_supplied_values(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Cost follows value, so a cent is not charged like a half.

    The reason this mode exists: charging the cent and the half dollar the
    same cost basis would make one look like a disaster and the other a
    windfall, and both figures would be wrong.
    """
    parent = lot(
        db,
        source_title="1964 Mint Set",
        piece_count=1,
        item_cost=Decimal("91.00"),
        shipping_cost=Decimal("0.00"),
    )

    body = do_split(client, admin_headers, parent.id, MINT_SET).json()

    prices = {p["source_title"]: p["item_cost"] for p in body["pieces"]}
    assert prices["1964 Cent"] == "1.00"
    assert prices["1964 Half Dollar"] == "50.00"
    assert body["allocated_cost"] == "91.00"


def test_relative_split_reconciles_on_an_awkward_total(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = lot(
        db, piece_count=1, item_cost=Decimal("47.53"), shipping_cost=Decimal("6.99")
    )
    body = do_split(client, admin_headers, parent.id, MINT_SET).json()

    assert body["allocated_cost"] == "47.53"
    assert body["allocated_shipping"] == "6.99"


def test_relative_mode_requires_values(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = lot(db)
    payload = {
        "mode": "relative",
        "pieces": [{"source_title": "A"}, {"source_title": "B"}],
    }
    response = do_split(client, admin_headers, parent.id, payload)
    assert response.status_code == 409
    assert "relative_value" in response.json()["detail"]


def test_the_relative_value_used_is_recorded_on_each_piece(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The value used for the allocation is recorded per piece.

    So the division can be re-checked later without guessing at what basis
    someone had in mind.
    """
    parent = lot(db, piece_count=1, item_cost=Decimal("91.00"))
    do_split(client, admin_headers, parent.id, MINT_SET)

    db.expire_all()
    cent = db.scalar(
        select(InventoryItem).where(InventoryItem.source_title == "1964 Cent")
    )
    assert cent.attributes["split_relative_value"] == "0.01"
    assert cent.attributes["split_mode"] == "relative"


# ---------------------------------------------------------------------------
# Lineage and double counting
# ---------------------------------------------------------------------------


def test_pieces_point_back_at_the_lot(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = lot(db)
    body = do_split(client, admin_headers, parent.id, TUBE).json()

    assert all(p["parent_item_id"] == parent.id for p in body["pieces"])
    assert all(p["item_code"] != parent.item_code for p in body["pieces"])
    assert len({p["item_code"] for p in body["pieces"]}) == 4


def test_the_lot_is_marked_split_and_kept(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A split lot is marked, not deleted.

    Kept, because it holds the purchase order, the price actually paid and
    the item code a receipt refers to.
    """
    parent = lot(db)
    do_split(client, admin_headers, parent.id, TUBE)

    db.expire_all()
    still_there = db.get(InventoryItem, parent.id)
    assert still_there is not None
    assert still_there.split_at is not None
    assert still_there.item_cost == Decimal("100.00")


def test_a_split_lot_disappears_from_the_inventory_views(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The double-counting trap.

    Without this the lot and its pieces are both counted and the collection
    appears to hold twice what it does.
    """
    parent = lot(db)
    before = db.execute(text("select count(*) from coin_inventory")).scalar()

    do_split(client, admin_headers, parent.id, TUBE)
    db.expire_all()

    ids = {r[0] for r in db.execute(text("select id from coin_inventory"))}
    assert parent.id not in ids
    # One lot became four pieces: the count rises by three, not by four.
    after = db.execute(text("select count(*) from coin_inventory")).scalar()
    assert after == before + 3


def test_splitting_does_not_change_what_the_collection_cost(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Splitting moves cost basis; it creates and destroys none.

    The invariant that matters for tax: breaking a lot up moves cost basis
    around, it does not create or destroy any.
    """
    parent = lot(db, item_cost=Decimal("100.00"), shipping_cost=Decimal("8.00"))
    total_before = db.execute(
        text(
            "select coalesce(sum(item_cost + shipping_cost), 0) from inventory_item "
            "where split_at is null"
        )
    ).scalar()

    do_split(client, admin_headers, parent.id, TUBE)
    db.expire_all()

    total_after = db.execute(
        text(
            "select coalesce(sum(item_cost + shipping_cost), 0) from inventory_item "
            "where split_at is null"
        )
    ).scalar()
    assert total_after == total_before


def test_the_tax_rounding_difference_is_reported_not_hidden(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The tax rounding difference is reported, not hidden.

    Taxes is generated per row, so the sum of rounded taxes need not equal
    the rounded tax of the sum. Whatever the difference is, it is stated.
    """
    parent = lot(db, item_cost=Decimal("100.00"), shipping_cost=Decimal("0.00"))
    payload = {"mode": "equal", "pieces": [{"source_title": f"P{n}"} for n in range(3)]}

    body = do_split(client, admin_headers, parent.id, payload).json()

    stated = Decimal(body["total_cost_difference"])
    actual = Decimal(body["allocated_total_cost"]) - Decimal(body["parent_total_cost"])
    assert stated == actual
    # And it stays within a cent per piece -- not silently arbitrary.
    assert abs(stated) <= Decimal("0.01") * len(payload["pieces"])


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_a_lot_cannot_be_split_twice(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = lot(db)
    assert do_split(client, admin_headers, parent.id, TUBE).status_code == 200

    again = do_split(client, admin_headers, parent.id, TUBE)
    assert again.status_code == 409
    assert "already been split" in again.json()["detail"]


def test_a_piece_cannot_itself_be_split(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Cost basis stays one hop from the purchase rather than a tree to walk."""
    parent = lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]

    response = do_split(client, admin_headers, pieces[0]["id"], TUBE)
    assert response.status_code == 409


def test_a_lot_that_has_been_ordered_cannot_be_split(
    client: TestClient,
    admin_headers: dict[str, str],
    customer_headers: dict[str, str],
    listing: Listing,
) -> None:
    """A lot that has been ordered cannot be split.

    Order history points at the lot; splitting would leave a sold line
    referring to something that no longer exists as sold.
    """
    placed = client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=customer_headers,
    )
    assert placed.status_code == 201

    response = do_split(client, admin_headers, listing.inventory_item_id, TUBE)
    assert response.status_code == 409
    assert "order" in response.json()["detail"]


def test_splitting_into_one_piece_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = lot(db)
    response = do_split(
        client,
        admin_headers,
        parent.id,
        {"mode": "equal", "pieces": [{"source_title": "only"}]},
    )
    assert response.status_code == 422


def test_splitting_withdraws_the_lots_listing(
    client: TestClient, admin_headers: dict[str, str], listing: Listing, db: Session
) -> None:
    """The lot is no longer a thing anyone can buy."""
    do_split(client, admin_headers, listing.inventory_item_id, TUBE)

    db.expire_all()
    assert db.get(Listing, listing.id).is_active is False
    assert client.get(f"/api/catalog/{listing.id}").json()["is_active"] is False


def test_splitting_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    parent = lot(db)
    assert do_split(client, customer_headers, parent.id, TUBE).status_code == 403


def test_pieces_may_carry_their_own_classifiers(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A mint set's coins have different denominations from each other."""
    parent = lot(db, piece_count=1, item_cost=Decimal("91.00"))
    payload = {
        "mode": "relative",
        "pieces": [
            {
                "source_title": "Cent",
                "relative_value": "0.01",
                "denomination": "usd_coin_0_01",
                "year_start": 1964,
            },
            {
                "source_title": "Half",
                "relative_value": "0.50",
                "denomination": "usd_coin_0_50",
                "year_start": 1964,
            },
        ],
    }
    assert do_split(client, admin_headers, parent.id, payload).status_code == 200

    db.expire_all()
    half = db.scalar(select(InventoryItem).where(InventoryItem.source_title == "Half"))
    assert half.denomination.code == "usd_coin_0_50"
    assert half.year_start == 1964
