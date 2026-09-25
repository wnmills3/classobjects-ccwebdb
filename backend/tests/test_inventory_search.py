"""Browsing the coin and currency inventories."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import (
    CurrencyDetail,
    Denomination,
    ErrorType,
    Grade,
    InventoryItem,
    ItemError,
    ItemKind,
    PurchaseOrder,
    SealColor,
    Vendor,
)
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item


def coin(db: Session, **overrides: object) -> InventoryItem:
    return make_item(db, item_kind_id=code_id(db, ItemKind, "coin"), **overrides)


def note(
    db: Session,
    *,
    seal: str | None = None,
    serial: str | None = None,
    **overrides: object,
) -> InventoryItem:
    item = make_item(db, item_kind_id=code_id(db, ItemKind, "currency"), **overrides)
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id,
            seal_color_id=code_id(db, SealColor, seal) if seal else None,
            serial_number=serial,
            series_year=overrides.get("year_start"),
        )
    )
    db.commit()
    return item


def search(
    client: TestClient, view: str, headers: dict[str, str], **params: object
) -> Response:
    return client.get(f"/api/inventory/{view}/search", params=params, headers=headers)


# ---------------------------------------------------------------------------
# The two views are genuinely separate inventories
# ---------------------------------------------------------------------------


def test_each_view_holds_only_its_own_kind(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    a_coin = coin(db, source_title="A Morgan Dollar")
    a_note = note(db, source_title="A Silver Certificate")

    coins = search(client, "coins", admin_headers).json()
    currency = search(client, "currency", admin_headers).json()

    assert a_coin.id in {r["id"] for r in coins["rows"]}
    assert a_coin.id not in {r["id"] for r in currency["rows"]}
    assert a_note.id in {r["id"] for r in currency["rows"]}
    assert a_note.id not in {r["id"] for r in coins["rows"]}


def test_each_view_returns_the_columns_that_matter_to_it(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Each view carries the columns its own kind needs.

    The reason for two views: a coin has a mint mark, a note has a seal
    color and its own serial. One grid would leave most columns empty.
    """
    coin(db)
    note(db, seal="blue", serial="A12345678B")

    coin_row = search(client, "coins", admin_headers).json()["rows"][0]
    note_row = search(client, "currency", admin_headers).json()["rows"][0]

    assert "mint_mark" in coin_row
    assert "metal" in coin_row
    assert "seal_color" not in coin_row

    assert "seal_color" in note_row
    assert "serial_number" in note_row
    assert "mint_mark" not in note_row


# ---------------------------------------------------------------------------
# The purchase order behind an item
# ---------------------------------------------------------------------------


def _order(
    db: Session, *, number: str, vendor_name: str, ordered_on: date
) -> PurchaseOrder:
    vendor = Vendor(name=vendor_name)
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(
        vendor_id=vendor.id, order_number=number, ordered_on=ordered_on
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def test_a_coin_row_carries_its_purchase_order(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order(
        db, number="123-456", vendor_name="ebay.com", ordered_on=date(2025, 1, 9)
    )
    item = coin(db, purchase_order_id=order.id)

    row = search(client, "coins", admin_headers).json()["rows"]
    row = next(r for r in row if r["id"] == item.id)

    assert row["purchase_order_id"] == order.id
    assert row["order_number"] == "123-456"
    assert row["vendor"] == "ebay.com"
    assert row["ordered_on"] == "2025-01-09"


def test_part_of_an_order_number_finds_that_order_s_items(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Receiving searches by order number, typed in part -- "4452" or "114-4".

    Case-insensitive and anywhere in the number, as item code and serial
    number already are, so a number read off a packing slip in pieces finds
    its parcel. An item on another order, or on none, is not returned.
    """
    wanted = _order(
        db, number="114-4452-X", vendor_name="ebay.com", ordered_on=date(2025, 2, 1)
    )
    other = _order(
        db, number="227-0001", vendor_name="apmex.com", ordered_on=date(2025, 2, 2)
    )
    mine = coin(db, purchase_order_id=wanted.id)
    coin(db, purchase_order_id=other.id)
    coin(db)

    for fragment in ("4452", "114-4", "x"):
        body = search(client, "coins", admin_headers, order_number=fragment).json()
        assert {r["id"] for r in body["rows"]} == {mine.id}, fragment


def test_a_purchase_order_id_finds_exactly_that_order(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A link naming one order must show that order and no other.

    Matching its number instead was wrong twice over (code review,
    2026-09-23): "1001" also matched "11001" and another vendor's "1001", and
    an order recorded with no number could not be searched for at all.
    """
    wanted = _order(
        db, number="1001", vendor_name="ebay.com", ordered_on=date(2025, 4, 1)
    )
    lookalike = _order(
        db, number="11001", vendor_name="apmex.com", ordered_on=date(2025, 4, 2)
    )
    unnumbered = PurchaseOrder(vendor_id=wanted.vendor_id, ordered_on=date(2025, 4, 3))
    db.add(unnumbered)
    db.commit()
    mine = coin(db, purchase_order_id=wanted.id)
    coin(db, purchase_order_id=lookalike.id)
    loose = coin(db, purchase_order_id=unnumbered.id)

    body = search(client, "coins", admin_headers, purchase_order_id=wanted.id).json()
    assert {r["id"] for r in body["rows"]} == {mine.id}
    body = search(
        client, "coins", admin_headers, purchase_order_id=unnumbered.id
    ).json()
    assert {r["id"] for r in body["rows"]} == {loose.id}


def test_an_order_number_finds_notes_too(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """One parcel can hold coins and notes; the currency view filters the same."""
    order = _order(
        db, number="555-NOTES", vendor_name="ebay.com", ordered_on=date(2025, 3, 1)
    )
    wanted = note(db, source_title="On the order", serial="B11111111A")
    wanted.purchase_order_id = order.id
    db.commit()
    note(db, source_title="Not on it", serial="B22222222A")

    body = search(client, "currency", admin_headers, order_number="555").json()
    assert {r["id"] for r in body["rows"]} == {wanted.id}


def test_an_item_without_a_purchase_order_still_appears(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A LEFT JOIN, not an inner join -- an unordered item is not lost.

    An inner join to `purchase_order` would drop this row from the results
    entirely instead of returning it with nulls.
    """
    item = coin(db)

    row = search(client, "coins", admin_headers).json()["rows"]
    row = next(r for r in row if r["id"] == item.id)

    assert row["purchase_order_id"] is None
    assert row["order_number"] is None
    assert row["vendor"] is None
    assert row["ordered_on"] is None


def test_a_currency_row_carries_its_purchase_order(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order(
        db, number="123-456", vendor_name="ebay.com", ordered_on=date(2025, 1, 9)
    )
    item = note(db, purchase_order_id=order.id)

    row = search(client, "currency", admin_headers).json()["rows"]
    row = next(r for r in row if r["id"] == item.id)

    assert row["purchase_order_id"] == order.id
    assert row["order_number"] == "123-456"
    assert row["vendor"] == "ebay.com"
    assert row["ordered_on"] == "2025-01-09"


# ---------------------------------------------------------------------------
# Searching and filtering
# ---------------------------------------------------------------------------


def test_free_text_searches_title_and_item_code(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    wanted = coin(db, source_title="1881-S Morgan Dollar")
    coin(db, source_title="Walking Liberty Half")

    by_title = search(client, "coins", admin_headers, q="morgan").json()
    assert {r["id"] for r in by_title["rows"]} == {wanted.id}

    by_code = search(client, "coins", admin_headers, q=wanted.item_code).json()
    assert {r["id"] for r in by_code["rows"]} == {wanted.id}


def test_free_text_searches_the_rating_as_recorded(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The owner's rating is often the only descriptive text.

    A "funnyback" note may carry the word only there -- its title and
    description auction lot numbers -- so a search that skipped the rating
    would find nothing. The collection also spells it two ways, which is what
    `%` is for.
    """
    one_word = note(db, source_title="1", description="Lot #15", rating="Funnyback")
    two_words = note(
        db, source_title="1", description="Lot #16", rating="AU Funny Back"
    )
    note(db, source_title="1", description="Lot #17", rating="Blue Seal")

    def found(q: str) -> set[int]:
        body = search(client, "currency", admin_headers, q=q).json()
        return {r["id"] for r in body["rows"]}

    assert found("funnyback") == {one_word.id}
    assert found("funny%back") == {one_word.id, two_words.id}
    assert found("funny") == {one_word.id, two_words.id}

    # The coin view searches it too: a Whatnot purchase's only readable text
    # is often its rating, e.g. "Morgan Silver Dollar AU-55" above a lot number.
    wanted = coin(
        db,
        source_title="1",
        description="Auction #652",
        rating="Morgan Silver Dollar AU-55",
    )
    body = search(client, "coins", admin_headers, q="silver dollar").json()
    assert {r["id"] for r in body["rows"]} == {wanted.id}


def test_a_note_can_be_found_by_its_printed_serial(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    wanted = note(db, source_title="Blue seal", serial="A12345678B")
    note(db, source_title="Another", serial="C98765432D")

    found = search(client, "currency", admin_headers, serial_number="12345678").json()
    assert {r["id"] for r in found["rows"]} == {wanted.id}


def test_filters_combine(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    wanted = coin(db, year_start=1881, item_cost=Decimal("50.00"))
    coin(db, year_start=1921)

    body = search(client, "coins", admin_headers, year_min=1880, year_max=1890).json()
    assert {r["id"] for r in body["rows"]} == {wanted.id}


def test_error_type_filter_finds_an_item_with_two_errors_exactly_once(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Filtering on one error is a different question from having any error.

    A join naive to the fact that an item can carry several errors would
    either miss this item or return it twice; a correct implementation finds
    it filtering on either of its two errors, and once each time.
    """
    both = coin(db, source_title="Doubled die, struck off center")
    coin(db, source_title="An unremarkable coin")

    db.add_all(
        [
            ItemError(
                inventory_item_id=both.id,
                error_type_id=code_id(db, ErrorType, "doubled_die"),
            ),
            ItemError(
                inventory_item_id=both.id,
                error_type_id=code_id(db, ErrorType, "off_center"),
            ),
        ]
    )
    db.commit()

    for error_type in ("doubled_die", "off_center"):
        body = search(client, "coins", admin_headers, error_type=error_type).json()
        assert body["total"] == 1
        assert [r["id"] for r in body["rows"]] == [both.id]


def test_error_type_filter_excludes_an_item_without_that_error(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = coin(db)
    db.add(
        ItemError(
            inventory_item_id=item.id,
            error_type_id=code_id(db, ErrorType, "off_center"),
        )
    )
    db.commit()

    body = search(client, "coins", admin_headers, error_type="doubled_die").json()
    assert body["total"] == 0


def test_an_unknown_filter_is_refused_rather_than_ignored(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """An unknown filter fails loudly rather than widening the search.

    A silently dropped filter returns the whole collection and looks like a
    matching result -- the worst possible failure for a search.
    """
    coin(db)
    response = search(client, "coins", admin_headers, mint_mark_typo="D")

    assert response.status_code == 422
    assert "mint_mark_typo" in response.json()["detail"]


def test_a_filter_from_the_other_view_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A filter belonging to the other view is refused.

    seal_color means nothing to coins, and pretending otherwise would
    return every coin.
    """
    coin(db)
    assert search(client, "coins", admin_headers, seal_color="blue").status_code == 422
    assert search(client, "currency", admin_headers, metal="silver").status_code == 422


def test_an_unknown_view_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.get("/api/inventory/medals/search", headers=admin_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Sorting, paging, money
# ---------------------------------------------------------------------------


def test_sorting_both_ways(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin(db, source_title="old", year_start=1878)
    coin(db, source_title="new", year_start=2020)

    up = search(client, "coins", admin_headers, sort="year_start").json()
    down = search(client, "coins", admin_headers, sort="year_start", desc=True).json()

    assert up["rows"][0]["year_start"] == 1878
    assert down["rows"][0]["year_start"] == 2020


def test_an_unsortable_column_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin(db)
    response = search(client, "coins", admin_headers, sort="attributes")
    assert response.status_code == 422
    assert "Sortable" in response.json()["detail"]


def test_each_page_names_every_column_it_can_sort_by(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The table makes a header clickable only when the server lists it.

    Every header used to look sortable while the server refused most of them,
    so clicking Grade or Status put an error on the page. Each listed name is
    actually sorted by here, so the list cannot claim a sort that would fail.
    """
    coin(db)
    note(db)

    for view in ("coins", "currency"):
        listed = search(client, view, admin_headers).json()["sortable"]

        assert "item_code" in listed, view
        assert "grade" not in listed, view
        for key in listed:
            response = search(client, view, admin_headers, sort=key)
            assert response.status_code == 200, (view, key)


def test_paging_reports_the_total_not_the_page(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    for n in range(5):
        coin(db, source_title=f"item {n}")

    body = search(client, "coins", admin_headers, limit=2).json()
    assert body["total"] >= 5
    assert len(body["rows"]) == 2

    second = search(client, "coins", admin_headers, limit=2, offset=2).json()
    assert {r["id"] for r in body["rows"]}.isdisjoint({r["id"] for r in second["rows"]})


def test_money_never_arrives_as_a_float(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Money crosses the API as a string, never a float.

    FastAPI's encoder turns a Decimal in a plain dict into a float, which is
    the one thing this schema is careful never to do.
    """
    coin(db, item_cost=Decimal("0.10"), shipping_cost=Decimal("0.20"))
    row = search(client, "coins", admin_headers).json()["rows"][0]

    assert isinstance(row["item_cost"], str)
    assert isinstance(row["total_cost"], str)
    assert row["item_cost"] == "0.10"


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------


def test_facets_count_what_is_actually_present(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Facets offer the values present, not the whole vocabulary.

    A search panel over thousands of items needs to offer the values that
    exist, not the fifty-odd grades the vocabulary defines.
    """
    coin(db, grade_id=code_id(db, Grade, "64"))
    coin(db, grade_id=code_id(db, Grade, "64"))
    coin(db, grade_id=code_id(db, Grade, "65"))

    body = search(client, "coins", admin_headers, facets=True).json()
    grades = {f["value"]: f["count"] for f in body["facets"]["grade"]}

    assert grades["64"] == 2
    assert grades["65"] == 1


def test_facets_reflect_the_current_filters(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Facet counts respect the filters already applied.

    The counts describe what narrowing further would do, so a filter that
    would return nothing is visibly empty before it is chosen.
    """
    coin(db, year_start=1881)
    coin(db, year_start=2020)

    filtered = search(client, "coins", admin_headers, facets=True, year_min=2000).json()
    total_in_facet = sum(f["count"] for f in filtered["facets"]["item_kind"])

    assert total_in_facet == filtered["total"]


def test_facets_are_opt_in(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin(db)
    assert search(client, "coins", admin_headers).json()["facets"] == {}


def test_denomination_is_offered_by_label_and_filters_by_code(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A denomination can be chosen by name; the choice filters by its code.

    The panel shows each facet value's label, because `usd_coin_0_01` is what
    the filter compares and "Cent" is what a person picks from a list.
    """
    cent = code_id(db, Denomination, "usd_coin_0_01")
    quarter = code_id(db, Denomination, "usd_coin_0_25")
    pennies = {coin(db, denomination_id=cent).id for _ in range(2)}
    coin(db, denomination_id=quarter)

    body = search(client, "coins", admin_headers, facets=True).json()
    offered = {f["value"]: f for f in body["facets"]["denomination"]}

    assert offered["usd_coin_0_01"]["count"] == 2
    assert offered["usd_coin_0_01"]["label"] == db.get_one(Denomination, cent).label
    assert offered["usd_coin_0_01"]["label"] != "usd_coin_0_01"
    assert offered["usd_coin_0_25"]["count"] == 1

    chosen = search(client, "coins", admin_headers, denomination="usd_coin_0_01")
    assert {r["id"] for r in chosen.json()["rows"]} == pennies


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def test_browsing_inventory_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    """Browsing inventory is staff-only.

    These rows carry cost basis, storage quantity and local catalog
    numbers -- none of which is customer-facing.
    """
    response = client.get("/api/inventory/coins/search", headers=customer_headers)
    assert response.status_code == 403

    assert client.get("/api/inventory/coins/search").status_code == 401


def test_a_split_lot_does_not_appear(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A lot that has been broken up no longer appears.

    The view excludes split lots, so browsing cannot show a lot beside the
    pieces it became.
    """
    parent = coin(
        db, source_title="Tube of four", piece_count=4, item_cost=Decimal("100.00")
    )
    client.post(
        f"/api/inventory/{parent.id}/split",
        json={"mode": "equal", "pieces": [{"source_title": f"P{n}"} for n in range(4)]},
        headers=admin_headers,
    )

    body = search(client, "coins", admin_headers).json()
    assert parent.id not in {r["id"] for r in body["rows"]}
