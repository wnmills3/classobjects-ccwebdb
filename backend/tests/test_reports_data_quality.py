"""`dq_issues` and `dq_completeness`: what is missing or wrong in the record."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from app.inventory_search import (
    COIN_VIEW,
    CURRENCY_VIEW,
    MISSING_FIELDS,
    ViewSpec,
    count_issues,
    view_path,
)
from app.inventory_search import search as inventory_search
from app.issues import COIN_ISSUES, CURRENCY_ISSUES
from app.models import (
    CurrencyDetail,
    Denomination,
    Grade,
    Image,
    InventoryItem,
    ItemImage,
    ItemKind,
    Metal,
    Series,
    StorageLocation,
    StorageLocationKind,
)
from app.reports.base import ReportResult
from app.reports.data_quality import (
    DQ_COMPLETENESS,
    DQ_ISSUES,
    DqCompletenessParams,
    DqIssuesParams,
)
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id

# ---------------------------------------------------------------------------
# dq_issues
# ---------------------------------------------------------------------------


def test_every_check_of_every_view_appears_once(db: Session) -> None:
    """A check that counts zero is still listed, with 0 rather than omitted."""
    result = DQ_ISSUES.run(db, DqIssuesParams())

    rows_by_view_check = {(r["view"], r["check"]): r for r in result.rows}
    assert set(rows_by_view_check) == {
        *(("Coins", code) for code in COIN_ISSUES),
        *(("Currency", code) for code in CURRENCY_ISSUES),
    }


def test_issue_counts_equal_the_searchs_own_count_issues(db: Session) -> None:
    """A row's count is exactly what `inventory_search.count_issues` returns."""
    build_bare_item(db, year_start=None)
    build_bare_item(db, year_start=1900)
    build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))

    result = DQ_ISSUES.run(db, DqIssuesParams())

    coin_counts = count_issues(db, COIN_VIEW, params={})
    currency_counts = count_issues(db, CURRENCY_VIEW, params={})
    for row in result.rows:
        counts = coin_counts if row["view"] == "Coins" else currency_counts
        expected = counts.get(cast("str", row["check"]), 0)
        assert row["items"] == expected, (row["view"], row["check"])

    # A row links on its check's name, not on the view it counts in.
    assert result.link_column == "check"

    no_year_row = next(
        r for r in result.rows if r["view"] == "Coins" and r["check"] == "no_year"
    )
    assert no_year_row["items"] == 1


def test_a_nonzero_row_drills_to_its_own_search_a_zero_row_drills_nowhere(
    db: Session,
) -> None:
    build_bare_item(db, year_start=None)

    result = DQ_ISSUES.run(db, DqIssuesParams())
    by_check = {(r["view"], r["check"]): i for i, r in enumerate(result.rows)}

    no_year_index = by_check[("Coins", "no_year")]
    assert result.drills[no_year_index] == "/inventory/coins?issue=no_year"

    no_country_index = by_check[("Coins", "no_country")]
    assert result.rows[no_country_index]["items"] == 0
    assert result.drills[no_country_index] is None


def test_a_deleted_and_a_split_item_are_not_counted_as_issues(db: Session) -> None:
    build_bare_item(db, year_start=None)
    deleted = build_bare_item(db, year_start=None)
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    split = build_bare_item(db, year_start=None)
    split.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = DQ_ISSUES.run(db, DqIssuesParams())
    no_year_row = next(
        r for r in result.rows if r["view"] == "Coins" and r["check"] == "no_year"
    )
    assert no_year_row["items"] == 1


def test_dq_issues_has_no_totals_and_says_why(db: Session) -> None:
    """One item can carry several checks, so a sum of the counts means nothing."""
    result = DQ_ISSUES.run(db, DqIssuesParams())
    assert result.totals is None
    assert result.notes


# ---------------------------------------------------------------------------
# dq_completeness
# ---------------------------------------------------------------------------

#: One real row of each reference vocabulary a "fully filled" fixture needs,
#: named by code so the fixture reads as data rather than magic numbers.
_DENOMINATION = "usd_coin_1_00"
_GRADE = "70"
_METAL = "silver"
_SERIES = "large_cent"


def _location(db: Session) -> int:
    """A storage location's id -- not a seeded vocabulary, built like Receiving's."""
    location = StorageLocation(
        storage_location_kind_id=code_id(db, StorageLocationKind, "home"),
        identifier="test box",
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    return location.id


def _attach_photo(db: Session, item: InventoryItem) -> None:
    """Give `item` one photograph -- the minimum `missing=photo` needs to clear."""
    image = Image(
        sha256=f"{item.id:0>64}",
        storage_key=f"orig/{item.id}.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(image)
    db.flush()
    db.add(ItemImage(inventory_item_id=item.id, image_id=image.id))
    db.commit()


def _fully_filled_coin(db: Session) -> InventoryItem:
    """A coin with every field `dq_completeness` reports on filled in."""
    item = build_bare_item(
        db,
        year_start=1900,
        denomination_id=code_id(db, Denomination, _DENOMINATION),
        grade_id=code_id(db, Grade, _GRADE),
        series_id=code_id(db, Series, _SERIES),
        metal_id=code_id(db, Metal, _METAL),
        storage_location_id=_location(db),
        listing_url="https://example.com/listing/1",
        sellers_item_id="EBAY-1",
    )
    _attach_photo(db, item)
    return item


def _empty_coin(db: Session) -> InventoryItem:
    """A coin with every field `dq_completeness` reports on left empty."""
    return build_bare_item(
        db,
        year_start=None,
        country_id=None,
        denomination_id=None,
        grade_id=None,
        series_id=None,
        metal_id=None,
        storage_location_id=None,
        listing_url=None,
        sellers_item_id=None,
    )


def _fully_filled_note(db: Session) -> InventoryItem:
    """A note with every applicable field filled -- its year on `CurrencyDetail`.

    `denomination_id` and `grade_id` are real rows of those vocabularies, not
    ones a note would actually carry -- this fixture is only exercising
    "filled vs. empty", which does not depend on the value being one a note
    would realistically hold.
    """
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        year_start=None,
        denomination_id=code_id(db, Denomination, _DENOMINATION),
        grade_id=code_id(db, Grade, _GRADE),
        series_id=code_id(db, Series, _SERIES),
        storage_location_id=_location(db),
        listing_url="https://example.com/listing/2",
        sellers_item_id="EBAY-2",
    )
    db.add(CurrencyDetail(inventory_item_id=item.id, series_year=1935))
    db.commit()
    _attach_photo(db, item)
    return item


def _empty_note(db: Session) -> InventoryItem:
    """A note with every applicable field left empty, `series_year` included."""
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        year_start=None,
        country_id=None,
        denomination_id=None,
        grade_id=None,
        series_id=None,
        storage_location_id=None,
        listing_url=None,
        sellers_item_id=None,
    )
    db.add(CurrencyDetail(inventory_item_id=item.id, series_year=None))
    db.commit()
    return item


def _fully_filled_bullion(db: Session) -> InventoryItem:
    """A round with every field applicable to bullion filled -- no denomination."""
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "bullion"),
        year_start=1986,
        metal_id=code_id(db, Metal, _METAL),
        series_id=code_id(db, Series, _SERIES),
        storage_location_id=_location(db),
        listing_url="https://example.com/listing/3",
        sellers_item_id="EBAY-3",
    )
    _attach_photo(db, item)
    return item


def _row_for(result: ReportResult, kind_label: str) -> dict[str, object]:
    """The one completeness row for this kind's label."""
    return next(r for r in result.rows if r["kind"] == kind_label)


def test_completeness_leaves_a_sets_metal_blank(db: Session) -> None:
    """A set's metal cell is blank, never 0% for a set with no metal.

    A set is often of mixed metals, so metal does not apply (owner, 2026-09-28).
    """
    build_bare_item(db, item_kind_id=code_id(db, ItemKind, "set"), metal_id=None)

    result = DQ_COMPLETENESS.run(db, DqCompletenessParams())

    assert _row_for(result, "Set")["metal"] is None


def test_completeness_per_kind_with_a_notes_year_from_series_year_and_no_metal(
    db: Session,
) -> None:
    """A note's year and metal are read the way `missing=` reads them.

    Each kind here holds one fully filled item and one fully empty one --
    50.0% exactly, no rounding -- except bullion, which holds only the
    filled one.
    """
    _fully_filled_coin(db)
    _empty_coin(db)
    _fully_filled_note(db)
    _empty_note(db)
    _fully_filled_bullion(db)

    result = DQ_COMPLETENESS.run(db, DqCompletenessParams())

    coin_row = _row_for(result, "Coin")
    assert coin_row["live_items"] == 2
    for field in (
        "year",
        "denomination",
        "grade",
        "country",
        "series",
        "metal",
        "photo",
        "storage_location",
        "listing_link",
        "sellers_item_id",
    ):
        assert coin_row[field] == Decimal("50.0"), field

    currency_row = _row_for(result, "Currency")
    assert currency_row["live_items"] == 2
    # Both notes leave year_start NULL; only series_year tells them apart. If
    # year read year_start instead, this would be 0.0, not 50.0.
    assert currency_row["year"] == Decimal("50.0")
    assert currency_row["denomination"] == Decimal("50.0")
    assert currency_row["grade"] == Decimal("50.0")
    assert currency_row["metal"] is None  # not applicable to currency
    assert currency_row["series"] is None  # optional for a note (owner)

    bullion_row = _row_for(result, "Bullion")
    assert bullion_row["live_items"] == 1
    assert bullion_row["metal"] == Decimal("100.0")
    assert bullion_row["denomination"] is None  # not applicable to bullion
    assert bullion_row["grade"] is None  # not applicable to bullion
    assert bullion_row["year"] == Decimal("100.0")


def test_completeness_treats_an_empty_string_as_missing_not_just_null(
    db: Session,
) -> None:
    """An empty string is what a cleared form field leaves behind, not NULL."""
    _fully_filled_coin(db)
    build_bare_item(db, listing_url="", sellers_item_id="")

    result = DQ_COMPLETENESS.run(db, DqCompletenessParams())
    coin_row = _row_for(result, "Coin")
    assert coin_row["live_items"] == 2
    assert coin_row["listing_link"] == Decimal("50.0")
    assert coin_row["sellers_item_id"] == Decimal("50.0")


def test_row_drills_are_the_kinds_own_search(db: Session) -> None:
    _fully_filled_coin(db)
    _fully_filled_note(db)
    _fully_filled_bullion(db)

    result = DQ_COMPLETENESS.run(db, DqCompletenessParams())
    drill_by_kind = {
        r["kind"]: d for r, d in zip(result.rows, result.drills, strict=True)
    }

    assert drill_by_kind["Coin"] == "/inventory/coins?kind=coin"
    assert drill_by_kind["Currency"] == "/inventory/currency"
    assert drill_by_kind["Bullion"] == "/inventory/coins?kind=bullion"


def test_a_deleted_and_a_split_item_are_excluded_from_completeness(db: Session) -> None:
    _fully_filled_coin(db)
    deleted = _fully_filled_coin(db)
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    split = _fully_filled_coin(db)
    split.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = DQ_COMPLETENESS.run(db, DqCompletenessParams())
    assert _row_for(result, "Coin")["live_items"] == 1


def test_each_cells_drill_search_agrees_with_the_reports_own_count(db: Session) -> None:
    """Every percent cell's implied "missing" count equals the drill search's.

    Derived from the row itself -- `live_items` and the cell's own percent
    -- rather than hard-coded per field, so this proves agreement generically
    for every applicable field of every kind, not just the ones named above.
    Fixtures are built in exact halves and wholes, so turning a percent back
    into a count is exact and never a rounded guess.
    """
    _fully_filled_coin(db)
    _empty_coin(db)
    _fully_filled_note(db)
    _empty_note(db)
    _fully_filled_bullion(db)

    result = DQ_COMPLETENESS.run(db, DqCompletenessParams())
    view_and_code: dict[str, tuple[ViewSpec, str | None]] = {
        "Coin": (COIN_VIEW, "coin"),
        "Currency": (CURRENCY_VIEW, None),
        "Bullion": (COIN_VIEW, "bullion"),
    }

    for row in result.rows:
        spec, kind_code = view_and_code[str(row["kind"])]
        live = cast("int", row["live_items"])
        for column in result.columns[2:]:  # past "kind" and "live_items"
            pct = row[column.key]
            if pct is None:
                continue
            filled = int(live * cast("Decimal", pct) / 100)
            params: dict[str, object] = {"missing": column.key}
            if kind_code is not None:
                params["kind"] = kind_code
            _, total = inventory_search(db, spec, params=params)
            assert total == live - filled, (row["kind"], column.key)


def test_completeness_lists_kinds_in_their_own_order_not_by_code(db: Session) -> None:
    """Coin, Currency, Bullion -- the kinds' sort order, as cb_holdings lists them.

    Built in code order (bullion, coin, currency) so an ORDER BY code would
    list Bullion first and fail here.
    """
    _fully_filled_bullion(db)
    _fully_filled_coin(db)
    _fully_filled_note(db)

    result = DQ_COMPLETENESS.run(db, DqCompletenessParams())
    assert [r["kind"] for r in result.rows] == ["Coin", "Currency", "Bullion"]


def test_a_kinds_view_path_is_currency_or_else_coins() -> None:
    assert view_path("currency") == "/inventory/currency"
    for kind_code in ("coin", "bullion", "unknown"):
        assert view_path(kind_code) == "/inventory/coins"


def test_a_missing_field_applies_to_its_kinds_or_to_every_kind() -> None:
    assert MISSING_FIELDS["denomination"].applies_to("coin")
    assert not MISSING_FIELDS["denomination"].applies_to("bullion")
    assert not MISSING_FIELDS["metal"].applies_to("currency")
    assert MISSING_FIELDS["photo"].applies_to("bullion")
