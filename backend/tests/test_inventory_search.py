"""Browsing the coin and currency inventories."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.models import (
    CoinDetail,
    CurrencyDetail,
    Denomination,
    ErrorType,
    FriedbergNumber,
    Grade,
    Image,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ItemError,
    ItemImage,
    ItemKind,
    ItemStatus,
    ProvenanceSource,
    PurchaseOrder,
    SealColor,
)
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, build_purchase_order, code_id


def coin(db: Session, **overrides: object) -> InventoryItem:
    """A coin with only the columns a search needs, the overrides applied."""
    return build_bare_item(db, item_kind_id=code_id(db, ItemKind, "coin"), **overrides)


def note(
    db: Session,
    *,
    seal: str | None = None,
    serial: str | None = None,
    **overrides: object,
) -> InventoryItem:
    """A banknote and its currency detail, with this seal colour and serial."""
    item = build_bare_item(
        db, item_kind_id=code_id(db, ItemKind, "currency"), **overrides
    )
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
    """The response to searching one inventory view with these parameters."""
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
    """A purchase with this order number, vendor and date, to search items by."""
    return build_purchase_order(
        db, vendor_name=vendor_name, order_number=number, ordered_on=ordered_on
    )


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

    Matching its number instead would be wrong twice over: "1001" also
    matches "11001" and another vendor's "1001", and an order recorded with
    no number could not be searched for at all.
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


def test_ordered_since_finds_the_items_of_orders_placed_that_day_or_later(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Receiving narrows to recent orders when the order number is not known.

    The day itself counts. An order placed earlier, one with no date, and an
    item on no order are all left out -- and on both views alike.
    """
    before = _order(db, number="B-1", vendor_name="a.com", ordered_on=date(2025, 5, 9))
    on_day = _order(db, number="D-1", vendor_name="b.com", ordered_on=date(2025, 5, 10))
    after = _order(db, number="A-1", vendor_name="c.com", ordered_on=date(2025, 5, 20))
    undated = PurchaseOrder(vendor_id=before.vendor_id, order_number="U-1")
    db.add(undated)
    db.commit()
    day_coin = coin(db, purchase_order_id=on_day.id)
    later_coin = coin(db, purchase_order_id=after.id)
    coin(db, purchase_order_id=before.id)
    coin(db, purchase_order_id=undated.id)
    coin(db)
    later_note = note(db, serial="C33333333A")
    later_note.purchase_order_id = after.id
    early_note = note(db, serial="C44444444A")
    early_note.purchase_order_id = before.id
    db.commit()

    coins = search(client, "coins", admin_headers, ordered_since="2025-05-10").json()
    assert {r["id"] for r in coins["rows"]} == {day_coin.id, later_coin.id}
    notes = search(client, "currency", admin_headers, ordered_since="2025-05-10").json()
    assert {r["id"] for r in notes["rows"]} == {later_note.id}


def test_ordered_since_narrows_with_denomination_and_part_of_a_serial(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A $1 note with a serial containing 4241, ordered lately: one item."""
    recent = _order(db, number="R-1", vendor_name="a.com", ordered_on=date(2025, 6, 2))
    old = _order(db, number="O-1", vendor_name="b.com", ordered_on=date(2025, 1, 2))
    one = code_id(db, Denomination, "usd_note_1")
    two = code_id(db, Denomination, "usd_note_2")
    wanted = note(db, serial="K42411424A", denomination_id=one)
    for serial, order, denomination in (
        ("K42411424B", old, one),  # the serial, but ordered long ago
        ("K42411424C", recent, two),  # the serial, but a $2 note
        ("K99999999A", recent, one),  # recent $1, another serial
    ):
        other = note(db, serial=serial, denomination_id=denomination)
        other.purchase_order_id = order.id
    wanted.purchase_order_id = recent.id
    db.commit()

    body = search(
        client,
        "currency",
        admin_headers,
        ordered_since="2025-06-01",
        denomination="usd_note_1",
        serial_number="4241",
    ).json()
    assert {r["id"] for r in body["rows"]} == {wanted.id}


def test_an_ordered_since_that_is_no_date_is_refused_by_name(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Refused before the database sees it, naming the filter."""
    response = search(client, "coins", admin_headers, ordered_since="10/20/2026")
    assert response.status_code == 422
    assert "ordered_since" in response.json()["detail"]
    assert "YYYY-MM-DD" in response.json()["detail"]


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


def test_a_backslash_in_the_search_is_matched_as_itself(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A backslash is not ILIKE's escape here: trailing or embedded, it is literal.

    Left as the escape, a term ending in one would match nothing, and one
    holding it before a letter would match the title without it.
    """
    wanted = coin(db, source_title="Box 7\\B", sellers_item_id="AB\\12")
    coin(db, source_title="Box 7B", sellers_item_id="AB12")

    for q in ("7\\", "7\\B"):
        rows = search(client, "coins", admin_headers, q=q).json()["rows"]
        assert [r["id"] for r in rows] == [wanted.id], q

    # A partial-match filter takes the same pattern.
    by_filter = search(client, "coins", admin_headers, sellers_item_id="B\\1")
    assert [r["id"] for r in by_filter.json()["rows"]] == [wanted.id]


def test_free_text_finds_items_by_their_purchase_order_number(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    ebay = build_purchase_order(
        db, vendor_name="ebay.com", order_number="31-24680-13579"
    )
    other = build_purchase_order(db, vendor_name="hibid.com", order_number="H-2")
    wanted_note = note(db, purchase_order_id=ebay.id)
    wanted_coin = coin(db, purchase_order_id=ebay.id)
    note(db, purchase_order_id=other.id)

    notes = search(client, "currency", admin_headers, q="31-24680-13579").json()
    coins = search(client, "coins", admin_headers, q="31-24680-13579").json()
    partial = search(client, "currency", admin_headers, q="24680-135").json()

    assert {r["id"] for r in notes["rows"]} == {wanted_note.id}
    assert {r["id"] for r in coins["rows"]} == {wanted_coin.id}
    assert {r["id"] for r in partial["rows"]} == {wanted_note.id}


def test_a_pasted_search_ignores_the_whitespace_around_it(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Text copied from an order page or a spreadsheet brings a space or a tab.

    Matched as typed, `%31-24680-13579 %` finds nothing, and neither does an
    item code copied out of a table cell.
    """
    order = build_purchase_order(
        db, vendor_name="ebay.com", order_number="31-24680-13579"
    )
    wanted = note(db, purchase_order_id=order.id)
    note(db)

    for pasted in (" 31-24680-13579", "31-24680-13579 ", "\t31-24680-13579\n"):
        found = search(client, "currency", admin_headers, q=pasted).json()
        assert {r["id"] for r in found["rows"]} == {wanted.id}, repr(pasted)

    by_code = search(client, "currency", admin_headers, q=f"{wanted.item_code} ").json()
    assert {r["id"] for r in by_code["rows"]} == {wanted.id}


def test_an_order_number_is_found_without_its_dashes(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Read aloud or typed from a receipt, the separators are the first to go."""
    order = build_purchase_order(
        db, vendor_name="ebay.com", order_number="31-24680-13579"
    )
    other = build_purchase_order(db, vendor_name="hibid.com", order_number="31-24680-1")
    wanted = note(db, purchase_order_id=order.id)
    note(db, purchase_order_id=other.id)

    for typed in ("312468013579", "31 24680 13579", "24680 13579"):
        found = search(client, "currency", admin_headers, q=typed).json()
        assert {r["id"] for r in found["rows"]} == {wanted.id}, typed


def test_an_order_number_search_still_honours_the_other_filters(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = build_purchase_order(db, vendor_name="ebay.com", order_number="22-1")
    kept = coin(db, purchase_order_id=order.id, source_title="1881 Morgan")
    deleted = coin(db, purchase_order_id=order.id, source_title="1882 Morgan")
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    found = search(client, "coins", admin_headers, q="22-1").json()

    assert {r["id"] for r in found["rows"]} == {kept.id}


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
        """The ids of the notes a free-text search returns."""
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


def test_a_bullion_item_can_be_found_by_its_serial(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bullion = code_id(db, ItemKind, "bullion")
    wanted = build_bare_item(db, item_kind_id=bullion)
    other = build_bare_item(db, item_kind_id=bullion)
    db.add_all(
        [
            CoinDetail(inventory_item_id=wanted.id, serial_number="PAMP-A0042"),
            CoinDetail(inventory_item_id=other.id, serial_number="JM-77"),
        ]
    )
    db.commit()

    found = search(client, "coins", admin_headers, serial_number="a004").json()
    assert {r["id"] for r in found["rows"]} == {wanted.id}
    assert found["rows"][0]["serial_number"] == "PAMP-A0042"


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


def test_errors_and_attributes_are_offered_with_how_many_items_carry_each(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The pickers list what the matching items carry, counted in items.

    An item with two errors is one item under each. An attribute a person
    removed is not counted. Each picker goes on offering its other values
    once one is chosen, and the other picker is counted under the choice.
    """
    both = coin(db)
    one = coin(db)
    coin(db)
    star = code_id(db, ItemAttribute, "star")
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
            ItemError(
                inventory_item_id=one.id,
                error_type_id=code_id(db, ErrorType, "off_center"),
            ),
            ItemAttributeLink(inventory_item_id=both.id, item_attribute_id=star),
            ItemAttributeLink(
                inventory_item_id=one.id,
                item_attribute_id=star,
                removed_at=datetime.now(UTC),
            ),
        ]
    )
    db.commit()

    def counts(body: dict[str, Any], facet: str) -> dict[str, int]:
        return {f["value"]: f["count"] for f in body["facets"][facet]}

    body = search(client, "coins", admin_headers, facets=True).json()
    assert counts(body, "error_type") == {"off_center": 2, "doubled_die": 1}
    assert counts(body, "attribute") == {"star": 1}
    labels = {f["value"]: f["label"] for f in body["facets"]["attribute"]}
    assert labels["star"]

    picked = search(
        client, "coins", admin_headers, facets=True, error_type="doubled_die"
    ).json()
    assert [r["id"] for r in picked["rows"]] == [both.id]
    assert counts(picked, "error_type") == {"off_center": 2, "doubled_die": 1}
    assert counts(picked, "attribute") == {"star": 1}

    by_attribute = search(
        client, "coins", admin_headers, facets=True, attribute="star"
    ).json()
    assert [r["id"] for r in by_attribute["rows"]] == [both.id]
    assert counts(by_attribute, "error_type") == {"off_center": 1, "doubled_die": 1}


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


def test_an_unknown_filter_s_refusal_lists_every_parameter_taken(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The list names the parameters read beside the filter table too.

    Without them a caller who mistyped `grade_min` is shown a list that
    does not hold the name they meant.
    """
    detail = search(client, "coins", admin_headers, mint_mark_typo="D").json()["detail"]

    for name in ("deleted", "lot", "issue", "missing", "item_codes", "grade_min"):
        assert f"'{name}'" in detail, name
    assert "'denomination'" in detail


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


def test_a_number_filter_given_text_is_refused_naming_it(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A year or an id that is not a number is a 422, not a database error."""
    coin(db)
    note(db)

    for view, param in (
        ("coins", "year_min"),
        ("coins", "year_max"),
        ("coins", "purchase_order_id"),
        ("currency", "series_year"),
        ("currency", "year_min"),
    ):
        response = search(client, view, admin_headers, **{param: "abc"})
        assert response.status_code == 422, (view, param, response.text)
        assert param in response.json()["detail"], (view, param)

    # A number sent as the text a query string always is still filters.
    wanted = coin(db, year_start=1999)
    found = search(client, "coins", admin_headers, year_min="1999").json()
    assert {r["id"] for r in found["rows"]} == {wanted.id}


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

    The server refuses a column it does not list, so a header that merely
    looked sortable would put an error on the page when clicked. Each listed
    name is actually sorted by here, both ways, so the list cannot claim a
    sort that would fail.
    """
    coin(db)
    note(db)

    for view in ("coins", "currency"):
        listed = search(client, view, admin_headers).json()["sortable"]

        assert "item_code" in listed, view
        for key in listed:
            for desc in (False, True):
                response = search(client, view, admin_headers, sort=key, desc=desc)
                assert response.status_code == 200, (view, key, desc)


#: The columns each table shows -- `columns` in the console's
#: `inventory/specs.js`, which is where a column added there is added here.
_SHOWN = {
    "coins": (
        "item_code",
        "denomination_label",
        "year_start",
        "mint_mark",
        "series_label",
        "item_kind",
        "grade",
        "metal",
        "fine_weight_ozt",
        "piece_count",
        "total_cost",
        "order_number",
        "status",
    ),
    "currency": (
        "item_code",
        "denomination_label",
        "series_label",
        "series_designation",
        "seal_color",
        "fed_district_letter",
        "serial_number",
        "friedberg_number",
        "grade_label",
        "total_cost",
        "order_number",
        "status",
    ),
}


def test_every_column_a_table_shows_can_be_sorted_by(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """No header in either table is one that cannot be clicked."""
    coin(db)
    note(db)

    for view, shown in _SHOWN.items():
        listed = search(client, view, admin_headers).json()["sortable"]
        assert set(shown) <= set(listed), (view, set(shown) - set(listed))


def _order_of(
    client: TestClient, headers: dict[str, str], view: str, sort: str, **params: object
) -> list[int]:
    """The ids of a view's items, as sorting by `sort` orders them."""
    body = search(client, view, headers, sort=sort, **params).json()
    return [row["id"] for row in body["rows"]]


def test_a_grade_is_sorted_by_its_rank_not_its_text(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """As text, 58 sorts after 4 only by luck and "Fine 12" after "Choice 64"."""
    low = coin(db, grade_id=code_id(db, Grade, "4"))
    high = coin(db, grade_id=code_id(db, Grade, "58"))
    ungraded = coin(db)
    fine = note(db, grade_id=code_id(db, Grade, "N12"))
    choice = note(db, grade_id=code_id(db, Grade, "N64"))

    up = _order_of(client, admin_headers, "coins", "grade")
    down = _order_of(client, admin_headers, "coins", "grade", desc=True)
    notes = _order_of(client, admin_headers, "currency", "grade_label")

    # Unrecorded last, whichever way: it is not the lowest grade.
    assert up == [low.id, high.id, ungraded.id]
    assert down == [high.id, low.id, ungraded.id]
    assert notes == [fine.id, choice.id]


def test_a_denomination_is_sorted_by_its_face_value(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """As text, "$10 Bill" sorts before "$2 Bill"."""
    ten = note(db, denomination_id=code_id(db, Denomination, "usd_note_10"))
    two = note(db, denomination_id=code_id(db, Denomination, "usd_note_2"))

    shown = _order_of(client, admin_headers, "currency", "denomination_label")

    assert shown == [two.id, ten.id]


def test_a_friedberg_number_is_sorted_by_the_number_it_opens_with(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """As text, 99000 sorts before 9901. Both numbers are made up."""
    items = {}
    for number in ("99000", "9901-B", "9901-A"):
        row = FriedbergNumber(fr_number=number, source=ProvenanceSource.manual)
        db.add(row)
        db.flush()
        item = note(db)
        detail = db.get(CurrencyDetail, item.id)
        assert detail is not None
        detail.friedberg_id = row.id
        db.commit()
        items[number] = item.id
    unnumbered = note(db)

    shown = _order_of(client, admin_headers, "currency", "friedberg_number")

    assert shown == [
        items["9901-A"],
        items["9901-B"],
        items["99000"],
        unnumbered.id,
    ]


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


def test_a_facet_offers_every_value_present_however_many(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """No cut-off: the least common value present is offered like the rest.

    Sixty-one series years, one note each, and one year held twice so the
    order is decided: a cut at sixty would drop one of the singletons.
    """
    years = list(range(1900, 1961))
    for year in [*years, 1900]:
        note(db, year_start=year)

    body = search(client, "currency", admin_headers, facets=True).json()
    offered = {f["value"]: f["count"] for f in body["facets"]["series_year"]}

    assert sorted(offered) == years
    assert offered[1900] == 2


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


def test_a_facet_goes_on_offering_the_values_its_own_filter_excludes(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """With Status set, the status picker still offers the other statuses.

    Counted with its own filter, the picker would offer only the status
    already chosen, and an item just marked Returned could not be found by
    choosing Returned. The other filters still narrow the counts, and every
    other facet still counts under this one.
    """
    received = code_id(db, ItemStatus, "received")
    returned = code_id(db, ItemStatus, "returned")
    coin(db, status_id=received, year_start=2020)
    coin(db, status_id=received, year_start=2021)
    coin(db, status_id=returned, year_start=2022)
    coin(db, status_id=returned, year_start=1881)

    body = search(
        client, "coins", admin_headers, facets=True, status="received", year_min=2000
    ).json()

    assert body["total"] == 2
    statuses = {f["value"]: f["count"] for f in body["facets"]["status"]}
    # The year filter narrows it (one returned coin is from 1881); the status
    # filter does not.
    assert statuses == {"received": 2, "returned": 1}
    # Another facet is counted under the status filter as before.
    assert sum(f["count"] for f in body["facets"]["item_kind"]) == 2


def test_a_facet_named_differently_from_its_filter_is_also_kept_open(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """`item_kind` is counted and `kind` is filtered: the same rule holds."""
    coin(db)
    build_bare_item(db, item_kind_id=code_id(db, ItemKind, "bullion"))

    body = search(client, "coins", admin_headers, facets=True, kind="coin").json()

    assert body["total"] == 1
    kinds = {f["value"]: f["count"] for f in body["facets"]["item_kind"]}
    assert kinds == {"coin": 1, "bullion": 1}


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


# ---------------------------------------------------------------------------
# missing=<field>
# ---------------------------------------------------------------------------


def _attach_photo(db: Session, item: InventoryItem) -> None:
    """Give `item` one photograph -- the minimum `missing=photo` needs to clear."""
    image = Image(
        sha256="f" * 64, storage_key="orig/f.jpg", media_type="image/jpeg", byte_size=10
    )
    db.add(image)
    db.flush()
    db.add(ItemImage(inventory_item_id=item.id, image_id=image.id))
    db.commit()


def test_missing_finds_items_with_the_field_empty(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    empty = coin(db, denomination_id=None)
    coin(db, denomination_id=code_id(db, Denomination, "usd_coin_1_00"))

    rows = search(client, "coins", admin_headers, missing="denomination").json()["rows"]
    assert [r["id"] for r in rows] == [empty.id]


def test_missing_photo_finds_items_with_no_photograph(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bare = coin(db)
    photographed = coin(db)
    _attach_photo(db, photographed)

    rows = search(client, "coins", admin_headers, missing="photo").json()["rows"]
    assert [r["id"] for r in rows] == [bare.id]


def test_missing_year_reads_a_notes_series_year(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A note's year is its series year; `year_start` is never read for one."""
    stale_year_start = build_bare_item(
        db, item_kind_id=code_id(db, ItemKind, "currency"), year_start=1899
    )
    db.add(CurrencyDetail(inventory_item_id=stale_year_start.id, series_year=None))
    dated = build_bare_item(
        db, item_kind_id=code_id(db, ItemKind, "currency"), year_start=None
    )
    db.add(CurrencyDetail(inventory_item_id=dated.id, series_year=1935))
    db.commit()

    rows = search(client, "currency", admin_headers, missing="year").json()["rows"]
    assert [r["id"] for r in rows] == [stale_year_start.id]


def test_missing_metal_never_matches_currency(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Metal does not apply to a note -- `missing=metal` is FALSE, not TRUE."""
    note(db, metal_id=None)  # would match if the filter were not kind-aware

    rows = search(client, "currency", admin_headers, missing="metal").json()["rows"]
    assert rows == []


def test_missing_series_never_matches_currency(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A note's design series is optional, so an empty one is not missing.

    Its year is the series year.
    """
    note(db, series_id=None)  # would match if the filter were not kind-aware

    rows = search(client, "currency", admin_headers, missing="series").json()["rows"]
    assert rows == []


def test_missing_metal_ignores_a_set(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Metal does not apply to a set, while a coin with none is still found.

    A set is often of mixed metals.
    """
    build_bare_item(db, item_kind_id=code_id(db, ItemKind, "set"), metal_id=None)
    gap = coin(db, metal_id=None)

    rows = search(client, "coins", admin_headers, missing="metal").json()["rows"]
    assert [r["id"] for r in rows] == [gap.id]


def test_missing_denomination_ignores_a_kind_it_does_not_apply_to(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Denomination applies only to coins and banknotes, not bullion.

    A bullion item with no denomination is not "missing" one -- bullion has
    none by nature -- so the coins view's search for it must stay empty even
    though bullion shares that view with coins.
    """
    build_bare_item(
        db, item_kind_id=code_id(db, ItemKind, "bullion"), denomination_id=None
    )

    rows = search(client, "coins", admin_headers, missing="denomination").json()["rows"]
    assert rows == []


def test_missing_excludes_a_deleted_and_a_split_item(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    live_gap = coin(db, denomination_id=None)
    deleted_gap = coin(db, denomination_id=None)
    deleted_gap.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    split_gap = coin(db, denomination_id=None)
    split_gap.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    rows = search(client, "coins", admin_headers, missing="denomination").json()["rows"]
    assert [r["id"] for r in rows] == [live_gap.id]


def test_missing_treats_an_empty_string_as_missing_not_just_null(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """An empty string is what a cleared form field leaves behind, not NULL.

    Each fixture fills the *other* field so the two checks stay isolated --
    NULL already counts as missing (via `coalesce`), so an item that left a
    field unset would confound "empty string counts too" with "NULL still
    counts", which is not what this test is proving.
    """
    blank_link = coin(db, listing_url="", sellers_item_id="EBAY-X")
    blank_sellers_id = coin(db, listing_url="https://example.com/y", sellers_item_id="")
    coin(db, listing_url="https://example.com/x", sellers_item_id="EBAY-1")

    link_rows = search(client, "coins", admin_headers, missing="listing_link").json()[
        "rows"
    ]
    assert [r["id"] for r in link_rows] == [blank_link.id]

    sellers_id_rows = search(
        client, "coins", admin_headers, missing="sellers_item_id"
    ).json()["rows"]
    assert [r["id"] for r in sellers_id_rows] == [blank_sellers_id.id]


def test_an_unknown_missing_field_is_refused_listing_the_known_ones(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Silently ignoring it would return the whole collection as a clean bill."""
    response = search(client, "coins", admin_headers, missing="bogus")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "bogus" in detail
    assert "photo" in detail
