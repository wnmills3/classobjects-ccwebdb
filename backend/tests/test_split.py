"""Breaking a lot into pieces, and dividing its cost between them."""

from __future__ import annotations

from decimal import Decimal

from app import lot_writes, offering_writes
from app.models import (
    CoinDetail,
    CurrencyDetail,
    InventoryItem,
    ItemKind,
    Listing,
    ListingFormat,
    SalesLot,
    SalesVenue,
)
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tests.builders import TUBE, build_split_lot, code_id, do_split
from tests.conftest import item_id_of

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


# ---------------------------------------------------------------------------
# Equal allocation
# ---------------------------------------------------------------------------


def test_equal_split_divides_the_cost_evenly(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = build_split_lot(db)
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
    parent = build_split_lot(
        db, item_cost=Decimal("100.00"), shipping_cost=Decimal("0.01")
    )
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
    parent = build_split_lot(
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
    parent = build_split_lot(
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
    parent = build_split_lot(
        db, piece_count=1, item_cost=Decimal("47.53"), shipping_cost=Decimal("6.99")
    )
    body = do_split(client, admin_headers, parent.id, MINT_SET).json()

    assert body["allocated_cost"] == "47.53"
    assert body["allocated_shipping"] == "6.99"


def test_relative_mode_requires_values(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = build_split_lot(db)
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
    parent = build_split_lot(db, piece_count=1, item_cost=Decimal("91.00"))
    do_split(client, admin_headers, parent.id, MINT_SET)

    db.expire_all()
    cent = db.scalar(
        select(InventoryItem).where(InventoryItem.source_title == "1964 Cent")
    )
    assert cent is not None
    assert cent.attributes["split_relative_value"] == "0.01"
    assert cent.attributes["split_mode"] == "relative"


# ---------------------------------------------------------------------------
# Lineage and double counting
# ---------------------------------------------------------------------------


def test_pieces_point_back_at_the_lot(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = build_split_lot(db)
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
    parent = build_split_lot(db)
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
    parent = build_split_lot(db)
    before = db.execute(text("select count(*) from coin_inventory")).scalar_one()

    do_split(client, admin_headers, parent.id, TUBE)
    db.expire_all()

    ids = {r[0] for r in db.execute(text("select id from coin_inventory"))}
    assert parent.id not in ids
    # One lot became four pieces: the count rises by three, not by four.
    after = db.execute(text("select count(*) from coin_inventory")).scalar_one()
    assert after == before + 3


def test_splitting_does_not_change_what_the_collection_cost(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Splitting moves cost basis; it creates and destroys none.

    The invariant that matters for tax: breaking a lot up moves cost basis
    around, it does not create or destroy any.
    """
    parent = build_split_lot(
        db, item_cost=Decimal("100.00"), shipping_cost=Decimal("8.00")
    )
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
    parent = build_split_lot(
        db, item_cost=Decimal("100.00"), shipping_cost=Decimal("0.00")
    )
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
    parent = build_split_lot(db)
    assert do_split(client, admin_headers, parent.id, TUBE).status_code == 200

    again = do_split(client, admin_headers, parent.id, TUBE)
    assert again.status_code == 409
    assert "already been split" in again.json()["detail"]


def test_a_piece_cannot_itself_be_split(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Cost basis stays one hop from the purchase rather than a tree to walk."""
    parent = build_split_lot(db)
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

    # Acknowledge the for-sale warning to reach `split_item`'s own check --
    # the point of this test is the order refusal underneath it, which
    # `acknowledge_for_sale` cannot get past (app.sale_state, kinds={"listing"}).
    response = do_split(
        client,
        admin_headers,
        item_id_of(listing),
        {**TUBE, "acknowledge_for_sale": True},
    )
    assert response.status_code == 409
    assert "order" in response.json()["detail"]


def test_splitting_into_one_piece_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    parent = build_split_lot(db)
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
    # The lot is listed, so the split must acknowledge that (app.sale_state).
    do_split(
        client,
        admin_headers,
        item_id_of(listing),
        {**TUBE, "acknowledge_for_sale": True},
    )

    db.expire_all()
    assert db.get_one(Listing, listing.id).is_active is False
    assert client.get(f"/api/catalog/{listing.id}").json()["is_active"] is False


def test_a_listed_lot_is_marked_split_without_autoflush(
    client: TestClient, admin_headers: dict[str, str], listing: Listing, db: Session
) -> None:
    """`split_at` must survive the listing being ended.

    Production's `SessionLocal` sets `autoflush=False`; the test session does
    not, and that difference is the whole test. `split_item` assigns
    `parent.split_at` and then ends the lot's listings through
    `offering_writes.end_offer`, which locks the items it affects and
    re-reads them with `populate_existing=True`. The parent is one of them.
    With autoflush on, the query that selects the listings flushes the
    assignment first and it survives; with autoflush off -- production -- the
    re-read overwrites the pending value and clears its dirty flag. The
    commit then writes the pieces and the ended listing while leaving the lot
    with `split_at` null: a lot that has been split, still claiming it has
    not, and so splittable a second time.

    Only a *listed* lot reaches `end_offer` at all, which is why the plain
    split tests never saw it.
    """
    item_id = item_id_of(listing)

    db.autoflush = False
    try:
        response = do_split(
            client,
            admin_headers,
            item_id,
            {**TUBE, "acknowledge_for_sale": True},
        )
    finally:
        db.autoflush = True
    assert response.status_code == 200, response.text

    # Read the column back rather than the object: the fault is a value that
    # never reaches the database, and an unexpired identity map would happily
    # keep reporting it as set.
    db.expire_all()
    assert db.get_one(InventoryItem, item_id).split_at is not None

    # And the lot really is closed to a second split.
    again = do_split(
        client, admin_headers, item_id, {**TUBE, "acknowledge_for_sale": True}
    )
    assert again.status_code == 409, again.text


def test_splitting_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    parent = build_split_lot(db)
    assert do_split(client, customer_headers, parent.id, TUBE).status_code == 403


def test_pieces_may_carry_their_own_classifiers(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A mint set's coins have different denominations from each other."""
    parent = build_split_lot(db, piece_count=1, item_cost=Decimal("91.00"))
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
    assert half is not None
    assert half.denomination is not None
    assert half.denomination.code == "usd_coin_0_50"
    assert half.year_start == 1964


def test_pieces_may_carry_their_own_description(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A lot of proof sets becomes one set per year, each described as such."""
    parent = build_split_lot(db, description="9 Proof Sets")
    payload = {
        "mode": "equal",
        "pieces": [
            {"source_title": "Lot", "description": "1980 US Proof Set"},
            {"source_title": "Lot"},
        ],
    }
    body = do_split(client, admin_headers, parent.id, payload).json()

    db.expire_all()
    descriptions = db.scalars(
        select(InventoryItem.description)
        .where(InventoryItem.id.in_([p["id"] for p in body["pieces"]]))
        .order_by(InventoryItem.id)
    ).all()
    assert descriptions == ["1980 US Proof Set", "9 Proof Sets"]


def test_pieces_keep_the_listing_they_were_bought_from(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """EBay's item id traces a piece to its listing, so every piece keeps it."""
    parent = build_split_lot(
        db,
        sellers_item_id="235872202173",
        listing_url="https://www.ebay.com/itm/235872202173",
    )
    body = do_split(client, admin_headers, parent.id, TUBE).json()

    db.expire_all()
    kept = db.execute(
        select(InventoryItem.sellers_item_id, InventoryItem.listing_url).where(
            InventoryItem.id.in_([p["id"] for p in body["pieces"]])
        )
    ).all()
    assert len(kept) == 4
    assert set(kept) == {("235872202173", "https://www.ebay.com/itm/235872202173")}


def test_a_split_lot_names_its_pieces(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The editor shows what a split lot became; a piece became nothing."""
    parent = build_split_lot(db)
    body = do_split(client, admin_headers, parent.id, TUBE).json()
    codes = [p["item_code"] for p in body["pieces"]]

    shown = client.get(f"/api/inventory/{parent.id}", headers=admin_headers).json()
    assert shown["piece_codes"] == codes
    piece = client.get(
        f"/api/inventory/{body['pieces'][0]['id']}", headers=admin_headers
    ).json()
    assert piece["piece_codes"] == []


# ---------------------------------------------------------------------------
# Detail rows
# ---------------------------------------------------------------------------


def test_a_split_piece_gets_its_own_coin_detail_row(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Every item in the collection has exactly one; a piece must too.

    Without it, mint mark and variety have nowhere to be written -- which is
    the whole point of splitting a lot of Morgans.
    """
    parent = build_split_lot(db)
    body = do_split(client, admin_headers, parent.id, TUBE).json()
    ids = sorted(p["id"] for p in body["pieces"])

    rows = sorted(
        db.scalars(
            select(CoinDetail.inventory_item_id).where(
                CoinDetail.inventory_item_id.in_(ids)
            )
        ).all()
    )
    assert rows == ids


def test_a_currency_piece_gets_a_currency_detail_row(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The kind decides the table: a note's serial has no home on coin_detail."""
    parent = build_split_lot(db, item_kind_id=code_id(db, ItemKind, "currency"))
    body = do_split(client, admin_headers, parent.id, TUBE).json()
    ids = sorted(p["id"] for p in body["pieces"])

    rows = sorted(
        db.scalars(
            select(CurrencyDetail.inventory_item_id).where(
                CurrencyDetail.inventory_item_id.in_(ids)
            )
        ).all()
    )
    assert rows == ids
    assert not db.scalars(
        select(CoinDetail.inventory_item_id).where(
            CoinDetail.inventory_item_id.in_(ids)
        )
    ).all(), "a banknote must not also get a coin_detail row"


def test_a_piece_detail_row_starts_empty(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The lot's detail row describes the lot, and must not be copied down.

    Copying would write the seller's guess into precisely the fields someone
    is about to fill in by examining the piece -- the thing the whole
    provenance design exists to prevent.
    """
    parent = build_split_lot(db)
    db.add(CoinDetail(inventory_item_id=parent.id, variety="VAM-1A"))
    db.commit()

    body = do_split(client, admin_headers, parent.id, TUBE).json()
    ids = [p["id"] for p in body["pieces"]]

    varieties = db.scalars(
        select(CoinDetail.variety).where(CoinDetail.inventory_item_id.in_(ids))
    ).all()
    assert set(varieties) == {None}


def test_a_coin_offered_inside_a_lot_cannot_be_split(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    lot_of_three: SalesLot,
    ebay_venue: SalesVenue,
) -> None:
    """A member of an *offered* sales lot is refused, acknowledgement or not.

    The sibling of "a coin sold inside a lot can no longer be split"
    (`7ea6eb0`), one step earlier in the lifecycle: that one closed the
    **sold** case, this one the **offered** case, and the two failed the same
    way because both asked `listing.inventory_item_id`, which is NULL on a
    lot listing. `split_item`'s listing loop still asks it -- deliberately,
    it is what ends an *item's own* listings -- so a lot listing is invisible
    there and nothing ended the lot's offer.

    Measured before the fix, through this exact route: unacknowledged gave
    409 with the for-sale warning (`sale_state._offering` reads claims, so
    the *warning* was always lot-aware), and acknowledged gave **200**. The
    coin was split, and the lot went on offering it: listing `active`, lot
    `offered`, membership open, claim `active`, `offered_items` still naming
    a parent with `split_at` set. A buyer looking at a group containing a
    coin that no longer exists as a whole item -- while
    `offering_writes._refuse_unofferable` refuses to offer a split item and
    `lot_writes._refuse_unofferable` refuses to put one in a lot.

    **Unconditional, not a `sale_state.guard` warning**, which is the whole
    point: `acknowledge_for_sale` exists for changes a buyer should be told
    about, not for ones that leave the offer describing something untrue.
    Only `offered` refuses, exactly as `offering_writes._refuse_grouped`
    scopes it: an `assembling` lot has been shown to nobody, and offering it
    later refuses the split member by name anyway.

    Survives: removing the `lot_holding` check from `splitting.split_item`
    makes this fail with 200 and two children.
    """
    offering_writes.offer(
        db,
        lot=lot_of_three,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("1000.00"),
        title="Three Morgan Dollars",
        description="",
        external_id=None,
    )
    db.commit()
    member = lot_writes.open_members(db, lot_of_three)[0].item
    member_id = member.id
    pieces = {
        "mode": "equal",
        "pieces": [{"source_title": "Half A"}, {"source_title": "Half B"}],
        "acknowledge_for_sale": True,
    }

    refused = do_split(client, admin_headers, member_id, pieces)

    assert refused.status_code == 409, refused.text
    # Names the lot, not just "cannot be split": ending or dissolving that
    # lot is the one thing that unfreezes the coin, and an operator reading
    # the refusal still has to go and find which lot.
    assert f"lot #{lot_of_three.id}" in refused.json()["detail"]

    db.expire_all()
    # Real state, not the status code alone: the code would pass on a split
    # that happened and then failed to serialize its response.
    parent = db.get_one(InventoryItem, member_id)
    assert parent.split_at is None
    assert (
        db.scalars(
            select(InventoryItem.id).where(InventoryItem.parent_item_id == member_id)
        ).all()
        == []
    )
