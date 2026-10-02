"""What is missing or wrong in the record: the seven data-quality reports."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast

from app.field_sources import HELD, SERIES_CLASSIFY, SERIES_MATCH
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
from app.item_history import location_label
from app.models import (
    CurrencyDetail,
    Denomination,
    Grade,
    Image,
    InventoryItem,
    ItemFieldReview,
    ItemFieldSource,
    ItemImage,
    ItemKind,
    ItemStatus,
    Metal,
    Series,
    StorageLocation,
    StorageLocationKind,
)
from app.reports.base import ReportResult
from app.reports.data_quality import (
    DQ_COMPLETENESS,
    DQ_DERIVED,
    DQ_ISSUES,
    DQ_LOCATIONS,
    DQ_PHOTOS,
    DQ_PURCHASES,
    DQ_SERIES_YEARS,
    DqCompletenessParams,
    DqDerivedParams,
    DqIssuesParams,
    DqLocationsParams,
    DqPhotosParams,
    DqPurchasesParams,
    DqSeriesYearsParams,
    _a_year_before,
)
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, build_purchase_order, code_id

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

    A set is often of mixed metals, so metal does not apply.
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
    assert currency_row["series"] is None  # optional for a note

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


# ---------------------------------------------------------------------------
# dq_photos
# ---------------------------------------------------------------------------


def test_dq_photos_counts_live_items_missing_a_photo_by_kind_and_status(
    db: Session,
) -> None:
    build_bare_item(db, status_id=code_id(db, ItemStatus, "received"))
    photographed = build_bare_item(db, status_id=code_id(db, ItemStatus, "received"))
    _attach_photo(db, photographed)

    result = DQ_PHOTOS.run(db, DqPhotosParams())

    row = next(
        r for r in result.rows if r["kind"] == "Coin" and r["status"] == "Received"
    )
    assert row["items"] == 1


def test_dq_photos_row_drills_agree_with_their_own_search(db: Session) -> None:
    build_bare_item(db, status_id=code_id(db, ItemStatus, "received"))
    build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        status_id=code_id(db, ItemStatus, "ordered"),
    )

    result = DQ_PHOTOS.run(db, DqPhotosParams())

    coin_row_index = next(
        i
        for i, r in enumerate(result.rows)
        if r["kind"] == "Coin" and r["status"] == "Received"
    )
    coin_drill = result.drills[coin_row_index]
    assert coin_drill == "/inventory/coins?kind=coin&status=received&missing=photo"
    _, coin_total = inventory_search(
        db, COIN_VIEW, params={"kind": "coin", "status": "received", "missing": "photo"}
    )
    assert coin_total == result.rows[coin_row_index]["items"]

    currency_row_index = next(
        i
        for i, r in enumerate(result.rows)
        if r["kind"] == "Currency" and r["status"] == "Ordered"
    )
    currency_drill = result.drills[currency_row_index]
    assert currency_drill == "/inventory/currency?status=ordered&missing=photo"
    _, currency_total = inventory_search(
        db, CURRENCY_VIEW, params={"status": "ordered", "missing": "photo"}
    )
    assert currency_total == result.rows[currency_row_index]["items"]


def test_dq_photos_unfiled_row_counts_images_linked_to_no_item_and_is_last(
    db: Session,
) -> None:
    item = build_bare_item(db)
    _attach_photo(db, item)  # linked -- not unfiled

    unfiled = Image(
        sha256="f" * 64,
        storage_key="orig/unfiled.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(unfiled)
    db.commit()

    result = DQ_PHOTOS.run(db, DqPhotosParams())

    assert result.rows[-1]["kind"] == "Unfiled photographs"
    assert result.rows[-1]["items"] == 1
    assert result.drills[-1] == "/photos"


def test_a_deleted_and_a_split_item_are_excluded_from_dq_photos(db: Session) -> None:
    build_bare_item(db, status_id=code_id(db, ItemStatus, "received"))
    deleted = build_bare_item(db, status_id=code_id(db, ItemStatus, "received"))
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    split = build_bare_item(db, status_id=code_id(db, ItemStatus, "received"))
    split.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = DQ_PHOTOS.run(db, DqPhotosParams())
    row = next(
        r for r in result.rows if r["kind"] == "Coin" and r["status"] == "Received"
    )
    assert row["items"] == 1


# ---------------------------------------------------------------------------
# dq_derived
# ---------------------------------------------------------------------------


def _mark_derived(db: Session, item: InventoryItem, field: str, rule: str) -> None:
    """Record that `rule` filled `field` on `item`, as a machine pass would."""
    db.add(
        ItemFieldSource(inventory_item_id=item.id, field_name=field, derived_by=rule)
    )
    db.commit()


def _mark_reviewed(db: Session, item: InventoryItem, field: str) -> None:
    """Record that a person confirmed `field` on `item` by examination."""
    db.add(ItemFieldReview(inventory_item_id=item.id, field_name=field))
    db.commit()


def test_dq_derived_counts_a_source_with_no_matching_review(db: Session) -> None:
    item = build_bare_item(db)
    _mark_derived(db, item, "series_id", SERIES_CLASSIFY)

    result = DQ_DERIVED.run(db, DqDerivedParams())

    row = next(r for r in result.rows if r["field"] == "Series")
    assert row["rule"] == "Series classification"
    assert row["items"] == 1


def test_dq_derived_shows_the_raw_column_name_for_an_untitled_field(
    db: Session,
) -> None:
    """A field this report has no title for still shows, not dropped."""
    item = build_bare_item(db)
    _mark_derived(db, item, "some_future_column", SERIES_CLASSIFY)

    result = DQ_DERIVED.run(db, DqDerivedParams())

    row = next(r for r in result.rows if r["field"] == "some_future_column")
    assert row["items"] == 1


def test_dq_derived_excludes_a_field_reviewed_on_the_same_item(db: Session) -> None:
    item = build_bare_item(db)
    _mark_derived(db, item, "series_id", SERIES_CLASSIFY)
    _mark_reviewed(db, item, "series_id")

    result = DQ_DERIVED.run(db, DqDerivedParams())

    assert not [r for r in result.rows if r["field"] == "Series"]


def test_dq_derived_still_counts_an_unrelated_field_reviewed_on_the_same_item(
    db: Session,
) -> None:
    """Reviewing one field does not clear another field's own gap.

    This is exactly the case `issue=unreviewed` (item-level: any review row at
    all) would score differently from this report (field-level): the item
    below is not "unreviewed" by that check, since it has one review row, but
    still has an unconfirmed series.
    """
    item = build_bare_item(db)
    _mark_derived(db, item, "series_id", SERIES_CLASSIFY)
    _mark_reviewed(db, item, "grade_id")

    result = DQ_DERIVED.run(db, DqDerivedParams())

    row = next(r for r in result.rows if r["field"] == "Series")
    assert row["items"] == 1


def test_dq_derived_excludes_a_held_field(db: Session) -> None:
    """`HELD` means a person emptied the field on purpose -- not a rule fill."""
    item = build_bare_item(db)
    _mark_derived(db, item, "series_id", HELD)

    result = DQ_DERIVED.run(db, DqDerivedParams())

    assert not [r for r in result.rows if r["field"] == "Series"]


def test_dq_derived_groups_two_rules_on_the_same_field_separately(db: Session) -> None:
    a = build_bare_item(db)
    b = build_bare_item(db)
    _mark_derived(db, a, "series_id", SERIES_CLASSIFY)
    _mark_derived(db, b, "series_id", SERIES_MATCH)

    result = DQ_DERIVED.run(db, DqDerivedParams())

    rows = {
        (r["field"], r["rule"]): r["items"]
        for r in result.rows
        if r["field"] == "Series"
    }
    assert rows[("Series", "Series classification")] == 1
    assert rows[("Series", "Series match")] == 1


def test_a_deleted_and_a_split_item_are_excluded_from_dq_derived(db: Session) -> None:
    item = build_bare_item(db)
    _mark_derived(db, item, "series_id", SERIES_CLASSIFY)
    deleted = build_bare_item(db)
    _mark_derived(db, deleted, "series_id", SERIES_CLASSIFY)
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    split = build_bare_item(db)
    _mark_derived(db, split, "series_id", SERIES_CLASSIFY)
    split.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = DQ_DERIVED.run(db, DqDerivedParams())
    row = next(r for r in result.rows if r["field"] == "Series")
    assert row["items"] == 1


def test_dq_derived_never_drills_and_says_why(db: Session) -> None:
    item = build_bare_item(db)
    _mark_derived(db, item, "series_id", SERIES_CLASSIFY)

    result = DQ_DERIVED.run(db, DqDerivedParams())

    assert result.drills == [None] * len(result.rows)
    assert result.notes
    for note in result.notes:
        assert "`" not in note
        assert "issue=" not in note
        assert "field x rule" not in note


# ---------------------------------------------------------------------------
# dq_purchases
# ---------------------------------------------------------------------------


def test_a_year_before_a_leap_day_falls_back_to_february_28() -> None:
    """2024 is a leap year; 2023 is not, so `date(2024, 2, 29)` has no direct answer."""
    assert _a_year_before(date(2024, 2, 29)) == date(2023, 2, 28)


def _entry(day: date) -> datetime:
    """A `created_at` at noon UTC -- safe from a day's timezone shift."""
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


def test_dq_purchases_flags_a_generated_order_number(db: Session) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor A",
        order_number="Order-0007",
        ordered_on=date(2026, 1, 1),
        source_url="https://example.com/listing",
        created_at=_entry(date(2026, 1, 1)),
    )
    build_bare_item(db, purchase_order_id=order.id)

    result = DQ_PURCHASES.run(db, DqPurchasesParams())

    row = next(r for r in result.rows if r["purchase"] == f"#{order.id}")
    assert row["gaps"] == "generated number"
    assert row["order_number"] == "Order-0007"
    assert row["vendor"] == "Vendor A"


def test_dq_purchases_flags_no_order_date(db: Session) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor B",
        order_number="V-100",
        ordered_on=None,
        source_url="https://example.com/listing",
        created_at=_entry(date(2026, 1, 1)),
    )
    build_bare_item(db, purchase_order_id=order.id)

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    row = next(r for r in result.rows if r["purchase"] == f"#{order.id}")
    assert row["gaps"] == "no order date"


def test_dq_purchases_flags_no_web_address(db: Session) -> None:
    for index, source_url in enumerate((None, "", "Gift")):
        order = build_purchase_order(
            db,
            vendor_name=f"Vendor web {index}",
            order_number="V-100",
            ordered_on=date(2026, 1, 1),
            source_url=source_url,
            created_at=_entry(date(2026, 1, 1)),
        )
        build_bare_item(db, purchase_order_id=order.id)

        result = DQ_PURCHASES.run(db, DqPurchasesParams())
        row = next(r for r in result.rows if r["purchase"] == f"#{order.id}")
        assert row["gaps"] == "no web address", source_url


def test_dq_purchases_flags_an_order_date_after_entry(db: Session) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor C",
        order_number="V-101",
        ordered_on=date(2026, 1, 10),
        source_url="https://example.com/listing",
        created_at=_entry(date(2026, 1, 1)),
    )
    build_bare_item(db, purchase_order_id=order.id)

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    row = next(r for r in result.rows if r["purchase"] == f"#{order.id}")
    assert row["gaps"] == "order date after entry"


def test_dq_purchases_flags_an_order_date_over_a_year_before_entry(
    db: Session,
) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor D",
        order_number="V-102",
        ordered_on=date(2024, 1, 1),
        source_url="https://example.com/listing",
        created_at=_entry(date(2026, 6, 1)),
    )
    build_bare_item(db, purchase_order_id=order.id)

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    row = next(r for r in result.rows if r["purchase"] == f"#{order.id}")
    assert row["gaps"] == "order date over a year before entry"


def test_dq_purchases_does_not_flag_a_date_exactly_a_year_before_entry(
    db: Session,
) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor E",
        order_number="V-103",
        ordered_on=date(2025, 1, 1),
        source_url="https://example.com/listing",
        created_at=_entry(date(2026, 1, 1)),
    )
    build_bare_item(db, purchase_order_id=order.id)

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    assert not [r for r in result.rows if r["purchase"] == f"#{order.id}"]


def test_dq_purchases_flags_a_live_item_with_zero_cost(db: Session) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor F",
        order_number="V-104",
        ordered_on=date(2026, 1, 1),
        source_url="https://example.com/listing",
        created_at=_entry(date(2026, 1, 1)),
    )
    build_bare_item(db, purchase_order_id=order.id, item_cost=Decimal("0"))

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    row = next(r for r in result.rows if r["purchase"] == f"#{order.id}")
    assert row["gaps"] == "item with zero cost"


def test_dq_purchases_flags_no_items(db: Session) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor G",
        order_number="V-105",
        ordered_on=date(2026, 1, 1),
        source_url="https://example.com/listing",
        created_at=_entry(date(2026, 1, 1)),
    )

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    row = next(r for r in result.rows if r["purchase"] == f"#{order.id}")
    assert row["gaps"] == "no items"


def test_dq_purchases_a_deleted_only_item_still_counts_as_no_items(
    db: Session,
) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor H",
        order_number="V-106",
        ordered_on=date(2026, 1, 1),
        source_url="https://example.com/listing",
        created_at=_entry(date(2026, 1, 1)),
    )
    deleted = build_bare_item(db, purchase_order_id=order.id)
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    row = next(r for r in result.rows if r["purchase"] == f"#{order.id}")
    assert row["gaps"] == "no items"


def test_dq_purchases_lists_combined_gaps_in_the_fixed_order(db: Session) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor I",
        order_number="Order-0099",
        ordered_on=None,
        source_url="",
        created_at=_entry(date(2026, 1, 1)),
    )

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    row = next(r for r in result.rows if r["purchase"] == f"#{order.id}")
    assert row["gaps"] == "generated number, no order date, no web address, no items"


def test_dq_purchases_omits_a_purchase_with_no_gaps(db: Session) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor J",
        order_number="V-107",
        ordered_on=date(2026, 1, 1),
        source_url="https://example.com/listing",
        created_at=_entry(date(2026, 1, 1)),
    )
    build_bare_item(db, purchase_order_id=order.id)

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    assert not [r for r in result.rows if r["purchase"] == f"#{order.id}"]


def test_dq_purchases_orders_newest_purchase_first(db: Session) -> None:
    first = build_purchase_order(
        db,
        vendor_name="Vendor K",
        order_number=None,
        ordered_on=None,
        source_url=None,
        created_at=_entry(date(2026, 1, 1)),
    )
    second = build_purchase_order(
        db,
        vendor_name="Vendor L",
        order_number=None,
        ordered_on=None,
        source_url=None,
        created_at=_entry(date(2026, 1, 1)),
    )

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    ids = [r["purchase"] for r in result.rows]
    assert ids.index(f"#{second.id}") < ids.index(f"#{first.id}")


def test_dq_purchases_number_is_the_link_column_and_drills_to_receiving(
    db: Session,
) -> None:
    order = build_purchase_order(
        db,
        vendor_name="Vendor M",
        order_number=None,
        ordered_on=None,
        source_url=None,
        created_at=_entry(date(2026, 1, 1)),
    )

    result = DQ_PURCHASES.run(db, DqPurchasesParams())
    assert result.linked_key == "purchase"
    index = next(
        i for i, r in enumerate(result.rows) if r["purchase"] == f"#{order.id}"
    )
    assert result.drills[index] == f"/receiving?order={order.id}"


# ---------------------------------------------------------------------------
# dq_locations
# ---------------------------------------------------------------------------


def _location_of_kind(
    db: Session, kind_code: str, **overrides: object
) -> StorageLocation:
    """A storage location of this kind -- not a seeded vocabulary."""
    location = StorageLocation(
        storage_location_kind_id=code_id(db, StorageLocationKind, kind_code),
        **overrides,
    )
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


def test_dq_locations_groups_by_the_consoles_own_label(db: Session) -> None:
    boxed = _location_of_kind(db, "home", identifier="Box 3")
    build_bare_item(
        db,
        storage_location_id=boxed.id,
        item_cost=Decimal("40.00"),
        tax_rate=Decimal("0"),
    )
    build_bare_item(
        db,
        storage_location_id=boxed.id,
        item_cost=Decimal("60.00"),
        tax_rate=Decimal("0"),
    )
    build_bare_item(db, storage_location_id=None)

    result = DQ_LOCATIONS.run(db, DqLocationsParams())

    labeled = next(r for r in result.rows if r["location"] == location_label(boxed))
    assert labeled["items"] == 2
    assert labeled["total_cost"] == Decimal("100.00")

    none_row = next(r for r in result.rows if r["location"] == "None recorded")
    assert none_row["items"] == 1


def test_dq_locations_lists_two_locations_sharing_one_label_as_two_rows(
    db: Session,
) -> None:
    """The console lists two same-label boxes as two entries; so does this report.

    Each keeps its own id in the row (`Home (#<id>)`) since the plain label
    alone could not tell a reader which physical box a row means.
    """
    first = _location_of_kind(db, "home")
    second = _location_of_kind(db, "home")
    assert location_label(first) == location_label(second)

    build_bare_item(
        db,
        storage_location_id=first.id,
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )
    build_bare_item(
        db,
        storage_location_id=second.id,
        item_cost=Decimal("20.00"),
        tax_rate=Decimal("0"),
    )

    result = DQ_LOCATIONS.run(db, DqLocationsParams())

    label = location_label(first)
    matching = [r for r in result.rows if str(r["location"]).startswith(f"{label} (#")]
    assert len(matching) == 2
    assert {r["location"] for r in matching} == {
        f"{label} (#{first.id})",
        f"{label} (#{second.id})",
    }
    assert {r["items"] for r in matching} == {1}
    assert {r["total_cost"] for r in matching} == {Decimal("10.00"), Decimal("20.00")}


def test_dq_locations_a_unique_label_is_shown_plain(db: Session) -> None:
    """A label naming exactly one location carries no `(#id)` suffix."""
    only = _location_of_kind(db, "home", identifier="Only box")
    build_bare_item(db, storage_location_id=only.id)

    result = DQ_LOCATIONS.run(db, DqLocationsParams())

    row = next(r for r in result.rows if r["location"] == location_label(only))
    assert "#" not in str(row["location"])


def test_dq_locations_sorts_by_label_then_id(db: Session) -> None:
    safe = _location_of_kind(db, "safe", identifier="Zzz box")
    home_low = _location_of_kind(db, "home")
    home_high = _location_of_kind(db, "home")
    assert home_low.id < home_high.id
    build_bare_item(db, storage_location_id=safe.id)
    build_bare_item(db, storage_location_id=home_high.id)
    build_bare_item(db, storage_location_id=home_low.id)

    result = DQ_LOCATIONS.run(db, DqLocationsParams())

    labels = [
        str(r["location"]) for r in result.rows if r["location"] != "None recorded"
    ]
    home_label = location_label(home_low)
    assert labels == [
        f"{home_label} (#{home_low.id})",
        f"{home_label} (#{home_high.id})",
        location_label(safe),
    ]


def test_dq_locations_none_recorded_row_is_last(db: Session) -> None:
    z_location = _location_of_kind(db, "safe", identifier="Zzz box")
    build_bare_item(db, storage_location_id=z_location.id)
    build_bare_item(db, storage_location_id=None)

    result = DQ_LOCATIONS.run(db, DqLocationsParams())

    assert result.rows[-1]["location"] == "None recorded"


def test_dq_locations_totals_equal_the_sum_of_its_rows(db: Session) -> None:
    a = _location_of_kind(db, "home", identifier="A")
    b = _location_of_kind(db, "safe", identifier="B")
    build_bare_item(
        db, storage_location_id=a.id, item_cost=Decimal("15.00"), tax_rate=Decimal("0")
    )
    build_bare_item(
        db, storage_location_id=b.id, item_cost=Decimal("25.00"), tax_rate=Decimal("0")
    )
    build_bare_item(
        db, storage_location_id=None, item_cost=Decimal("5.00"), tax_rate=Decimal("0")
    )

    result = DQ_LOCATIONS.run(db, DqLocationsParams())
    assert result.totals is not None
    assert result.totals["items"] == sum(cast(int, r["items"]) for r in result.rows)
    assert result.totals["total_cost"] == sum(
        (cast(Decimal, r["total_cost"]) for r in result.rows), Decimal("0")
    )


def test_dq_locations_has_no_drill_and_says_why(db: Session) -> None:
    build_bare_item(db, storage_location_id=None)
    result = DQ_LOCATIONS.run(db, DqLocationsParams())
    assert all(drill is None for drill in result.drills)
    assert result.notes


def test_a_deleted_and_a_split_item_are_excluded_from_dq_locations(db: Session) -> None:
    location = _location_of_kind(db, "home", identifier="C")
    build_bare_item(db, storage_location_id=location.id)
    deleted = build_bare_item(db, storage_location_id=location.id)
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    split = build_bare_item(db, storage_location_id=location.id)
    split.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = DQ_LOCATIONS.run(db, DqLocationsParams())
    row = next(r for r in result.rows if r["location"] == location_label(location))
    assert row["items"] == 1


# ---------------------------------------------------------------------------
# dq_series_years
# ---------------------------------------------------------------------------


def _morgan(db: Session) -> int:
    """The Morgan dollar series, with its years set here rather than assumed."""
    series_id = code_id(db, Series, "morgan_dollar")
    series = db.get(Series, series_id)
    assert series is not None
    series.year_start, series.year_end = 1878, 1921
    db.commit()
    return series_id


def test_dq_series_years_lists_a_coin_dated_outside_its_series(db: Session) -> None:
    """An 1800 Morgan dollar: a year no Morgan was struck in."""
    morgan = _morgan(db)
    typo = build_bare_item(db, series_id=morgan, year_start=1800, year_end=1800)
    build_bare_item(db, series_id=morgan, year_start=1880, year_end=1880)
    build_bare_item(db, series_id=morgan, year_start=1878, year_end=1921)

    result = DQ_SERIES_YEARS.run(db, DqSeriesYearsParams())

    assert [row["item"] for row in result.rows] == [typo.item_code]
    row = result.rows[0]
    assert (row["year"], row["series_years"]) == ("1800", "1878-1921")
    assert result.drills == [f"/inventory/coins?item_code={typo.item_code}"]


def test_dq_series_years_catches_a_range_past_the_end(db: Session) -> None:
    morgan = _morgan(db)
    item = build_bare_item(db, series_id=morgan, year_start=1920, year_end=1925)
    result = DQ_SERIES_YEARS.run(db, DqSeriesYearsParams())
    assert [(r["item"], r["year"]) for r in result.rows] == [
        (item.item_code, "1920-1925")
    ]


def test_dq_series_years_leaves_out_notes_and_coins_it_cannot_judge(
    db: Session,
) -> None:
    """A note's year is its series year; no year or no series is no finding."""
    morgan = _morgan(db)
    build_bare_item(db, series_id=morgan, year_start=None)
    build_bare_item(db, series_id=None, year_start=1800)
    build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        series_id=morgan,
        year_start=1800,
    )
    assert DQ_SERIES_YEARS.run(db, DqSeriesYearsParams()).rows == []


def test_dq_series_years_lists_what_its_issue_filter_finds(db: Session) -> None:
    """Report and `?issue=year_outside_series` run one predicate."""
    morgan = _morgan(db)
    build_bare_item(db, series_id=morgan, year_start=1800, year_end=1800)
    build_bare_item(db, series_id=morgan, year_start=1950, year_end=1950)
    build_bare_item(db, series_id=morgan, year_start=1900, year_end=1900)

    reported = {r["item"] for r in DQ_SERIES_YEARS.run(db, DqSeriesYearsParams()).rows}
    found, _ = inventory_search(db, COIN_VIEW, params={"issue": "year_outside_series"})
    assert reported == {row["item_code"] for row in found}
    assert len(reported) == 2
