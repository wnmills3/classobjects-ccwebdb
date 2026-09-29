"""`cb_holdings` and the other Collection reports."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast, get_args
from urllib.parse import parse_qs, urlsplit

import pytest
from app.inventory_search import COIN_VIEW, CURRENCY_VIEW
from app.inventory_search import search as inventory_search
from app.models import (
    BullionForm,
    CurrencyDetail,
    Denomination,
    Disposition,
    ErrorType,
    FedDistrict,
    Grade,
    GradingService,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ItemError,
    ItemKind,
    ItemStatus,
    Metal,
    MetalPrice,
    NoteType,
    SealColor,
    Series,
    StrikeType,
)
from app.reports.base import ReportResult
from app.reports.collection import (
    CB_ATTRIBUTES,
    CB_DESIGNS,
    CB_GRADES,
    CB_HOLDINGS,
    CB_METAL,
    CB_NOTES,
    AttributesParams,
    DesignsParams,
    DispositionParam,
    GradesParams,
    HoldingsParams,
    MetalParams,
    NotesParams,
    StatusParam,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id

_CENT = "usd_coin_0_01"
_DOLLAR = "usd_coin_1_00"
_NOTE_1 = "usd_note_1"


def _coin(
    db: Session, denomination: str | None, cost: Decimal, **overrides: object
) -> InventoryItem:
    """A coin with an exact cost basis: untaxed, so `total_cost` equals `cost`."""
    fields: dict[str, object] = {"item_cost": cost, "tax_rate": Decimal("0")}
    fields["denomination_id"] = (
        code_id(db, Denomination, denomination) if denomination is not None else None
    )
    fields.update(overrides)
    return build_bare_item(db, **fields)


def _note(
    db: Session, denomination: str | None, cost: Decimal, **overrides: object
) -> InventoryItem:
    """A note with an exact cost basis: untaxed, so `total_cost` equals `cost`."""
    fields: dict[str, object] = {
        "item_kind_id": code_id(db, ItemKind, "currency"),
        "item_cost": cost,
        "tax_rate": Decimal("0"),
    }
    if denomination is not None:
        fields["denomination_id"] = code_id(db, Denomination, denomination)
    fields.update(overrides)
    return build_bare_item(db, **fields)


def _row(result: ReportResult, kind_label: str, denom_label: str) -> dict[str, object]:
    return next(
        r
        for r in result.rows
        if r["kind"] == kind_label and r["denomination"] == denom_label
    )


def _subtotal(result: ReportResult, kind_label: str) -> dict[str, object]:
    return next(
        r
        for r in result.rows
        if r["kind"] == kind_label and r["denomination"] == f"All {kind_label}"
    )


# ---------------------------------------------------------------------------
# Rows, subtotals and totals
# ---------------------------------------------------------------------------


def test_rows_grouped_by_kind_then_denomination(db: Session) -> None:
    _coin(db, _CENT, Decimal("1.00"))
    _coin(db, _CENT, Decimal("2.00"))
    _coin(db, _DOLLAR, Decimal("50.00"))
    _note(db, _NOTE_1, Decimal("10.00"))

    result = CB_HOLDINGS.run(db, HoldingsParams())

    cent_row = _row(result, "Coin", "Cent")
    assert cent_row["items"] == 2
    assert cent_row["pieces"] == 2
    assert cent_row["total_cost"] == Decimal("3.00")

    dollar_row = _row(result, "Coin", "Dollar")
    assert dollar_row["items"] == 1
    assert dollar_row["total_cost"] == Decimal("50.00")

    note_row = _row(result, "Currency", "$1 Bill")
    assert note_row["items"] == 1
    assert note_row["total_cost"] == Decimal("10.00")


def test_denomination_ordered_by_its_own_sort_order_within_a_kind(db: Session) -> None:
    """Cent (sort 10) before Dollar (sort 60), regardless of insertion order."""
    _coin(db, _DOLLAR, Decimal("50.00"))
    _coin(db, _CENT, Decimal("1.00"))

    result = CB_HOLDINGS.run(db, HoldingsParams())
    coin_rows = [
        r
        for r in result.rows
        if r["kind"] == "Coin" and r["denomination"] != "All Coin"
    ]
    assert [r["denomination"] for r in coin_rows] == ["Cent", "Dollar"]


def test_kinds_are_ordered_by_item_kind_sort_order_not_by_label(db: Session) -> None:
    """Kinds sort by `item_kind.sort_order`, not alphabetically.

    `Bullion` < `Coin` alphabetically, but `item_kind.sort_order` says Coin
    (10) comes before Bullion (30) -- the controller ruled every vocabulary
    shows in its own `sort_order`, and this is the one place two kinds' rows
    could interleave if that were wrong.
    """
    _coin(db, _CENT, Decimal("1.00"))
    build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "bullion"),
        item_cost=Decimal("30.00"),
        tax_rate=Decimal("0"),
        denomination_id=None,
    )

    result = CB_HOLDINGS.run(db, HoldingsParams())
    kinds = [cast(str, r["kind"]) for r in result.rows]
    coin_positions = [i for i, k in enumerate(kinds) if k == "Coin"]
    bullion_positions = [i for i, k in enumerate(kinds) if k == "Bullion"]
    assert coin_positions and bullion_positions
    # Every Coin row (its denomination rows and its own subtotal) precedes
    # every Bullion row -- not just "Coin's label appears first" -- which is
    # what the subtotal loop's contiguity assumption actually depends on.
    assert max(coin_positions) < min(bullion_positions)


def test_no_denomination_is_labeled_and_sorts_last_within_its_kind(db: Session) -> None:
    _coin(db, _DOLLAR, Decimal("50.00"))
    _coin(db, None, Decimal("5.00"))

    result = CB_HOLDINGS.run(db, HoldingsParams())
    coin_rows = [
        r
        for r in result.rows
        if r["kind"] == "Coin" and r["denomination"] != "All Coin"
    ]
    assert [r["denomination"] for r in coin_rows] == ["Dollar", "No denomination"]

    no_denom_row = _row(result, "Coin", "No denomination")
    assert no_denom_row["items"] == 1
    assert no_denom_row["total_cost"] == Decimal("5.00")


def test_subtotals_equal_the_sum_of_their_rows_and_totals_the_sum_of_subtotals(
    db: Session,
) -> None:
    _coin(db, _CENT, Decimal("1.11"))
    _coin(db, _CENT, Decimal("2.22"))
    _coin(db, _DOLLAR, Decimal("3.33"))
    _note(db, _NOTE_1, Decimal("4.44"))
    _note(db, _NOTE_1, Decimal("5.55"))

    result = CB_HOLDINGS.run(db, HoldingsParams())

    coin_rows = [
        r
        for r in result.rows
        if r["kind"] == "Coin" and r["denomination"] != "All Coin"
    ]
    coin_subtotal = _subtotal(result, "Coin")
    assert coin_subtotal["items"] == sum(cast(int, r["items"]) for r in coin_rows)
    assert coin_subtotal["pieces"] == sum(cast(int, r["pieces"]) for r in coin_rows)
    assert coin_subtotal["total_cost"] == sum(
        (cast(Decimal, r["total_cost"]) for r in coin_rows), Decimal("0")
    )

    currency_rows = [
        r
        for r in result.rows
        if r["kind"] == "Currency" and r["denomination"] != "All Currency"
    ]
    currency_subtotal = _subtotal(result, "Currency")
    assert currency_subtotal["total_cost"] == sum(
        (cast(Decimal, r["total_cost"]) for r in currency_rows), Decimal("0")
    )

    assert result.totals is not None
    subtotal_rows = [
        r for r in result.rows if str(r["denomination"]).startswith("All ")
    ]
    assert result.totals["items"] == sum(cast(int, r["items"]) for r in subtotal_rows)
    assert result.totals["pieces"] == sum(cast(int, r["pieces"]) for r in subtotal_rows)
    assert result.totals["total_cost"] == sum(
        (cast(Decimal, r["total_cost"]) for r in subtotal_rows), Decimal("0")
    )
    assert result.totals["kind"] == "All kinds"


# ---------------------------------------------------------------------------
# Status and disposition parameters
# ---------------------------------------------------------------------------


def test_status_literal_matches_the_seeded_item_status_codes(db: Session) -> None:
    """A renamed or added seed code must fail this test, not silently match `all`."""
    seeded = set(db.scalars(select(ItemStatus.code)))
    assert set(get_args(StatusParam)) - {"all"} == seeded


def test_disposition_literal_matches_the_seeded_disposition_codes(db: Session) -> None:
    seeded = set(db.scalars(select(Disposition.code)))
    assert set(get_args(DispositionParam)) - {"all"} == seeded


def test_default_status_and_disposition_are_received_and_held(db: Session) -> None:
    _coin(db, _CENT, Decimal("1.00"))  # received/held, from build_bare_item's defaults
    _coin(
        db,
        _CENT,
        Decimal("9.00"),
        status_id=code_id(db, ItemStatus, "ordered"),
    )

    result = CB_HOLDINGS.run(db, HoldingsParams())
    assert _row(result, "Coin", "Cent")["items"] == 1


def test_status_all_removes_the_filter(db: Session) -> None:
    _coin(db, _CENT, Decimal("1.00"))
    _coin(db, _CENT, Decimal("9.00"), status_id=code_id(db, ItemStatus, "ordered"))

    result = CB_HOLDINGS.run(db, HoldingsParams(status="all"))
    assert _row(result, "Coin", "Cent")["items"] == 2


def test_status_parameter_selects_a_different_status(db: Session) -> None:
    _coin(db, _CENT, Decimal("1.00"))
    _coin(db, _CENT, Decimal("9.00"), status_id=code_id(db, ItemStatus, "ordered"))

    result = CB_HOLDINGS.run(db, HoldingsParams(status="ordered"))
    assert _row(result, "Coin", "Cent")["items"] == 1
    assert _row(result, "Coin", "Cent")["total_cost"] == Decimal("9.00")


def test_disposition_parameter_and_all(db: Session) -> None:
    _coin(db, _CENT, Decimal("1.00"))
    _coin(db, _CENT, Decimal("9.00"), disposition_id=code_id(db, Disposition, "sold"))

    held_only = CB_HOLDINGS.run(db, HoldingsParams())
    assert _row(held_only, "Coin", "Cent")["items"] == 1

    everything = CB_HOLDINGS.run(db, HoldingsParams(disposition="all"))
    assert _row(everything, "Coin", "Cent")["items"] == 2

    sold_only = CB_HOLDINGS.run(db, HoldingsParams(disposition="sold"))
    assert _row(sold_only, "Coin", "Cent")["items"] == 1
    assert _row(sold_only, "Coin", "Cent")["total_cost"] == Decimal("9.00")


# ---------------------------------------------------------------------------
# Live rows only
# ---------------------------------------------------------------------------


def test_a_deleted_item_is_excluded(db: Session) -> None:
    _coin(db, _CENT, Decimal("1.00"))
    deleted = _coin(db, _CENT, Decimal("9.00"))
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = CB_HOLDINGS.run(db, HoldingsParams())
    assert _row(result, "Coin", "Cent")["items"] == 1
    assert _row(result, "Coin", "Cent")["total_cost"] == Decimal("1.00")


def test_a_split_parent_is_excluded_its_live_children_are_counted(db: Session) -> None:
    parent = _coin(db, _CENT, Decimal("10.00"), piece_count=2)
    parent.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    _coin(db, _CENT, Decimal("5.00"), parent_item_id=parent.id)
    _coin(db, _CENT, Decimal("5.00"), parent_item_id=parent.id)

    result = CB_HOLDINGS.run(db, HoldingsParams())
    row = _row(result, "Coin", "Cent")
    assert row["items"] == 2
    assert row["total_cost"] == Decimal("10.00")


# ---------------------------------------------------------------------------
# Drills
# ---------------------------------------------------------------------------


def _parsed(path: str) -> tuple[str, dict[str, str]]:
    split = urlsplit(path)
    return split.path, {k: v[0] for k, v in parse_qs(split.query).items()}


def test_a_denomination_rows_drill_matches_the_search_count_for_currency(
    db: Session,
) -> None:
    _note(db, _NOTE_1, Decimal("10.00"))
    _note(db, _NOTE_1, Decimal("20.00"))
    _coin(db, _CENT, Decimal("1.00"))  # a coin the currency drill must not count

    result = CB_HOLDINGS.run(db, HoldingsParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["kind"] == "Currency" and r["denomination"] == "$1 Bill"
    )
    path, params = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/currency"
    _, total = inventory_search(db, CURRENCY_VIEW, params=dict(params))
    assert total == result.rows[idx]["items"]


def test_a_denomination_rows_drill_matches_the_search_count_for_a_coin_kind(
    db: Session,
) -> None:
    _coin(db, _CENT, Decimal("1.00"))
    _coin(db, _CENT, Decimal("2.00"))
    _coin(db, _DOLLAR, Decimal("50.00"))  # must not be swept into the Cent drill

    result = CB_HOLDINGS.run(db, HoldingsParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["kind"] == "Coin" and r["denomination"] == "Cent"
    )
    path, params = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    _, total = inventory_search(db, COIN_VIEW, params=dict(params))
    assert total == result.rows[idx]["items"]


def test_a_subtotal_rows_drill_matches_the_kinds_own_search_count(db: Session) -> None:
    _coin(db, _CENT, Decimal("1.00"))
    _coin(db, _DOLLAR, Decimal("2.00"))

    result = CB_HOLDINGS.run(db, HoldingsParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["kind"] == "Coin" and r["denomination"] == "All Coin"
    )
    path, params = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    _, total = inventory_search(db, COIN_VIEW, params=dict(params))
    assert total == result.rows[idx]["items"]


def test_a_no_denomination_rows_drill_uses_missing_denomination_and_matches(
    db: Session,
) -> None:
    """Denomination applies to coins, so the row's drill is `missing=denomination`."""
    _coin(db, None, Decimal("1.00"))
    _coin(db, _CENT, Decimal("2.00"))

    result = CB_HOLDINGS.run(db, HoldingsParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["kind"] == "Coin" and r["denomination"] == "No denomination"
    )
    _, params = _parsed(cast(str, result.drills[idx]))
    assert params.get("missing") == "denomination"
    _, total = inventory_search(db, COIN_VIEW, params=dict(params))
    assert total == result.rows[idx]["items"]


def test_a_no_denomination_row_has_no_drill_when_the_kind_cannot_carry_one(
    db: Session,
) -> None:
    """Bullion has no denomination at all -- `missing=denomination` would disagree."""
    build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "bullion"),
        item_cost=Decimal("30.00"),
        denomination_id=None,
    )

    result = CB_HOLDINGS.run(db, HoldingsParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["kind"] == "Bullion" and r["denomination"] == "No denomination"
    )
    assert result.drills[idx] is None


@pytest.mark.parametrize(
    ("params", "expected_items"),
    [
        # Neither filter applies: all three items count.
        (HoldingsParams(status="all", disposition="all"), 3),
        # Only the ordered/held item: status narrows, and the default
        # disposition="held" still applies since it was not widened.
        (HoldingsParams(status="ordered"), 1),
    ],
)
def test_a_denomination_drill_matches_the_search_with_non_default_params(
    db: Session, params: HoldingsParams, expected_items: int
) -> None:
    """The drill's own query string, not just the default one, must agree."""
    _coin(db, _CENT, Decimal("1.00"))  # received, held (build_bare_item's defaults)
    _coin(db, _CENT, Decimal("2.00"), status_id=code_id(db, ItemStatus, "ordered"))
    _coin(db, _CENT, Decimal("3.00"), disposition_id=code_id(db, Disposition, "sold"))

    result = CB_HOLDINGS.run(db, params)
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["kind"] == "Coin" and r["denomination"] == "Cent"
    )
    assert result.rows[idx]["items"] == expected_items
    path, query = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == expected_items


# ---------------------------------------------------------------------------
# No match
# ---------------------------------------------------------------------------


def test_nothing_matching_returns_empty_rows_no_totals_and_a_note(db: Session) -> None:
    _coin(db, _CENT, Decimal("1.00"), status_id=code_id(db, ItemStatus, "ordered"))

    result = CB_HOLDINGS.run(db, HoldingsParams())  # default status=received
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []
    assert result.notes


# ---------------------------------------------------------------------------
# cb_designs
# ---------------------------------------------------------------------------


def _designs_row(result: ReportResult, series_label: str) -> dict[str, object]:
    return next(r for r in result.rows if r["series"] == series_label)


def test_designs_groups_across_kinds_by_series_with_year_span_and_cost(
    db: Session,
) -> None:
    large_cent = code_id(db, Series, "large_cent")
    _coin(db, _CENT, Decimal("10.00"), series_id=large_cent, year_start=1850)
    _coin(db, _CENT, Decimal("20.00"), series_id=large_cent, year_start=1857)
    build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "bullion"),
        series_id=large_cent,
        year_start=1854,
        item_cost=Decimal("5.00"),
        tax_rate=Decimal("0"),
        denomination_id=None,
    )

    result = CB_DESIGNS.run(db, DesignsParams())
    row = _designs_row(result, "Large Cent")
    assert row["items"] == 3
    assert row["year_span"] == "1850-1857"
    assert row["total_cost"] == Decimal("35.00")


def test_designs_year_span_is_one_year_when_min_equals_max(db: Session) -> None:
    series = code_id(db, Series, "lincoln_cent")
    _coin(db, _CENT, Decimal("1.00"), series_id=series, year_start=1943)

    result = CB_DESIGNS.run(db, DesignsParams())
    assert _designs_row(result, "Lincoln Cent")["year_span"] == "1943"


def test_designs_year_span_is_empty_when_no_year_is_recorded(db: Session) -> None:
    series = code_id(db, Series, "lincoln_cent")
    _coin(db, _CENT, Decimal("1.00"), series_id=series, year_start=None)

    result = CB_DESIGNS.run(db, DesignsParams())
    assert _designs_row(result, "Lincoln Cent")["year_span"] == ""


def test_designs_no_series_sorts_last_and_currency_is_excluded(db: Session) -> None:
    lincoln = code_id(db, Series, "lincoln_cent")
    _coin(db, _CENT, Decimal("1.00"), series_id=lincoln, year_start=1950)
    _coin(db, _CENT, Decimal("2.00"), series_id=None)
    # Currency's series is optional and its own design identity is not a
    # series (MISSING_FIELDS["series"]) -- it never appears here, even with
    # a series recorded.
    _note(db, _NOTE_1, Decimal("3.00"), series_id=lincoln)

    result = CB_DESIGNS.run(db, DesignsParams())
    labels = [cast(str, r["series"]) for r in result.rows]
    assert labels[-1] == "No series"
    assert sum(cast(int, r["items"]) for r in result.rows) == 2


def test_designs_series_row_drill_matches_the_search_count(db: Session) -> None:
    series = code_id(db, Series, "lincoln_cent")
    _coin(db, _CENT, Decimal("1.00"), series_id=series)
    _coin(db, _CENT, Decimal("2.00"), series_id=series)
    _coin(db, _CENT, Decimal("3.00"), series_id=None)  # must not be swept in

    result = CB_DESIGNS.run(db, DesignsParams())
    idx = next(i for i, r in enumerate(result.rows) if r["series"] == "Lincoln Cent")
    path, query = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == result.rows[idx]["items"]


def test_designs_no_series_row_drill_uses_missing_series_and_matches(
    db: Session,
) -> None:
    _coin(db, _CENT, Decimal("1.00"), series_id=None)
    _coin(
        db,
        _CENT,
        Decimal("2.00"),
        series_id=code_id(db, Series, "lincoln_cent"),
    )

    result = CB_DESIGNS.run(db, DesignsParams())
    idx = next(i for i, r in enumerate(result.rows) if r["series"] == "No series")
    _, query = _parsed(cast(str, result.drills[idx]))
    assert query.get("missing") == "series"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == result.rows[idx]["items"]


def test_designs_totals_equal_the_sum_of_rows(db: Session) -> None:
    lincoln = code_id(db, Series, "lincoln_cent")
    large = code_id(db, Series, "large_cent")
    _coin(db, _CENT, Decimal("1.11"), series_id=lincoln)
    _coin(db, _CENT, Decimal("2.22"), series_id=large)
    _coin(db, _CENT, Decimal("3.33"), series_id=None)

    result = CB_DESIGNS.run(db, DesignsParams())
    assert result.totals is not None
    assert result.totals["items"] == sum(cast(int, r["items"]) for r in result.rows)
    assert result.totals["total_cost"] == sum(
        (cast(Decimal, r["total_cost"]) for r in result.rows), Decimal("0")
    )


def test_designs_excludes_deleted_and_split_items(db: Session) -> None:
    series = code_id(db, Series, "lincoln_cent")
    deleted = _coin(db, _CENT, Decimal("1.00"), series_id=series)
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    parent = _coin(db, _CENT, Decimal("10.00"), series_id=series, piece_count=2)
    parent.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    _coin(db, _CENT, Decimal("5.00"), series_id=series, parent_item_id=parent.id)

    result = CB_DESIGNS.run(db, DesignsParams())
    row = _designs_row(result, "Lincoln Cent")
    assert row["items"] == 1
    assert row["total_cost"] == Decimal("5.00")


# ---------------------------------------------------------------------------
# cb_notes
# ---------------------------------------------------------------------------


def _currency_note(
    db: Session,
    denomination: str | None,
    cost: Decimal,
    *,
    note_type: str | None = "frn",
    seal: str | None = None,
    district: str | None = None,
    series_year: int | None = None,
    series_letter: str | None = None,
    **overrides: object,
) -> InventoryItem:
    """A currency item with a `currency_detail` row, untaxed for an exact cost."""
    fields: dict[str, object] = {
        "item_kind_id": code_id(db, ItemKind, "currency"),
        "item_cost": cost,
        "tax_rate": Decimal("0"),
    }
    if denomination is not None:
        fields["denomination_id"] = code_id(db, Denomination, denomination)
    fields.update(overrides)
    item = build_bare_item(db, **fields)
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id,
            note_type_id=code_id(db, NoteType, note_type) if note_type else None,
            seal_color_id=code_id(db, SealColor, seal) if seal else None,
            fed_district_id=code_id(db, FedDistrict, district) if district else None,
            series_year=series_year,
            series_letter=series_letter,
        )
    )
    db.commit()
    return item


def _add_attribute(db: Session, item: InventoryItem, code: str) -> None:
    """Link `item` to the seeded `item_attribute` row named `code`."""
    db.add(
        ItemAttributeLink(
            inventory_item_id=item.id,
            item_attribute_id=code_id(db, ItemAttribute, code),
        )
    )
    db.commit()


def _add_error(db: Session, item: InventoryItem, code: str) -> None:
    """Record the seeded `error_type` row named `code` against `item`."""
    db.add(
        ItemError(
            inventory_item_id=item.id,
            error_type_id=code_id(db, ErrorType, code),
        )
    )
    db.commit()


def _notes_row(
    result: ReportResult, note_type: str, series_designation: str
) -> dict[str, object]:
    return next(
        r
        for r in result.rows
        if r["note_type"] == note_type and r["series_designation"] == series_designation
    )


def test_notes_groups_by_note_type_and_series_designation(db: Session) -> None:
    _currency_note(db, _NOTE_1, Decimal("10.00"), series_year=1935, series_letter="A")
    _currency_note(db, _NOTE_1, Decimal("20.00"), series_year=1935, series_letter="A")
    _currency_note(db, _NOTE_1, Decimal("30.00"), series_year=1935, series_letter=None)

    result = CB_NOTES.run(db, NotesParams())
    row_a = _notes_row(result, "Federal Reserve Note", "1935A")
    assert row_a["items"] == 2
    assert row_a["total_cost"] == Decimal("30.00")
    row_plain = _notes_row(result, "Federal Reserve Note", "1935")
    assert row_plain["items"] == 1


def test_notes_series_designation_is_no_series_year_when_absent(db: Session) -> None:
    _currency_note(db, _NOTE_1, Decimal("10.00"), series_year=None)

    result = CB_NOTES.run(db, NotesParams())
    row = _notes_row(result, "Federal Reserve Note", "No series year")
    assert row["items"] == 1


def test_notes_seal_colors_and_districts_are_comma_separated_sorted_labels(
    db: Session,
) -> None:
    _currency_note(
        db,
        _NOTE_1,
        Decimal("10.00"),
        series_year=1935,
        seal="red",
        district="B",
    )
    _currency_note(
        db,
        _NOTE_1,
        Decimal("20.00"),
        series_year=1935,
        seal="blue",
        district="A",
    )

    result = CB_NOTES.run(db, NotesParams())
    row = _notes_row(result, "Federal Reserve Note", "1935")
    assert row["seal_colors"] == "Blue Seal, Red Seal"
    assert row["fed_districts"] == "A, B"


def test_notes_counts_star_notes_and_fancy_serials(db: Session) -> None:
    plain = _currency_note(db, _NOTE_1, Decimal("10.00"), series_year=1935)
    star = _currency_note(db, _NOTE_1, Decimal("20.00"), series_year=1935)
    _add_attribute(db, star, "star")
    fancy = _currency_note(db, _NOTE_1, Decimal("30.00"), series_year=1935)
    _add_attribute(db, fancy, "fancy_serial")
    assert plain.id  # the plain note carries neither

    result = CB_NOTES.run(db, NotesParams())
    row = _notes_row(result, "Federal Reserve Note", "1935")
    assert row["items"] == 3
    assert row["star_notes"] == 1
    assert row["fancy_serials"] == 1


def test_notes_drill_requires_both_note_type_and_series_designation(
    db: Session,
) -> None:
    _currency_note(db, _NOTE_1, Decimal("10.00"), series_year=None)  # "No series year"

    result = CB_NOTES.run(db, NotesParams())
    row_idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["series_designation"] == "No series year"
    )
    assert result.drills[row_idx] is None


def test_notes_drill_matches_the_search_count(db: Session) -> None:
    _currency_note(db, _NOTE_1, Decimal("10.00"), series_year=1935, series_letter="A")
    _currency_note(db, _NOTE_1, Decimal("20.00"), series_year=1935, series_letter="A")
    _currency_note(
        db, _NOTE_1, Decimal("30.00"), series_year=1957
    )  # must not be swept in

    result = CB_NOTES.run(db, NotesParams())
    idx = next(
        i for i, r in enumerate(result.rows) if r["series_designation"] == "1935A"
    )
    path, query = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/currency"
    _, total = inventory_search(db, CURRENCY_VIEW, params=dict(query))
    assert total == result.rows[idx]["items"]


def test_notes_excludes_deleted_and_split_items(db: Session) -> None:
    deleted = _currency_note(db, _NOTE_1, Decimal("10.00"), series_year=1935)
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    parent = _currency_note(
        db, _NOTE_1, Decimal("30.00"), series_year=1935, piece_count=2
    )
    parent.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    _currency_note(
        db, _NOTE_1, Decimal("20.00"), series_year=1935, parent_item_id=parent.id
    )

    result = CB_NOTES.run(db, NotesParams())
    row = _notes_row(result, "Federal Reserve Note", "1935")
    assert row["items"] == 1
    assert row["total_cost"] == Decimal("20.00")


def test_notes_totals_equal_the_sum_of_rows(db: Session) -> None:
    a = _currency_note(
        db, _NOTE_1, Decimal("1.11"), series_year=1935, series_letter="A"
    )
    b = _currency_note(db, _NOTE_1, Decimal("2.22"), series_year=1957)
    _add_attribute(db, a, "star")
    _add_attribute(db, b, "fancy_serial")

    result = CB_NOTES.run(db, NotesParams())
    assert result.totals is not None
    assert result.totals["items"] == sum(cast(int, r["items"]) for r in result.rows)
    assert result.totals["total_cost"] == sum(
        (cast(Decimal, r["total_cost"]) for r in result.rows), Decimal("0")
    )
    assert result.totals["star_notes"] == sum(
        cast(int, r["star_notes"]) for r in result.rows
    )
    assert result.totals["fancy_serials"] == sum(
        cast(int, r["fancy_serials"]) for r in result.rows
    )


# ---------------------------------------------------------------------------
# cb_grades
# ---------------------------------------------------------------------------


def _graded_coin(
    db: Session,
    grade_code: str | None,
    cost: Decimal,
    *,
    strike: str | None = None,
    service: str | None = None,
    **overrides: object,
) -> InventoryItem:
    fields: dict[str, object] = {
        "item_cost": cost,
        "tax_rate": Decimal("0"),
        "grade_id": code_id(db, Grade, grade_code) if grade_code else None,
        "strike_type_id": code_id(db, StrikeType, strike) if strike else None,
        "grading_service_id": (
            code_id(db, GradingService, service) if service else None
        ),
    }
    fields.update(overrides)
    return build_bare_item(db, **fields)


def _grades_row(
    result: ReportResult, view: str, band: str, strike: str, service: str
) -> dict[str, object]:
    return next(
        r
        for r in result.rows
        if r["view"] == view
        and r["band"] == band
        and r["strike_type"] == strike
        and r["grading_service"] == service
    )


def test_grades_bands_split_on_numeric_value_boundaries(db: Session) -> None:
    _graded_coin(db, "1", Decimal("1.00"))
    _graded_coin(db, "45", Decimal("1.00"))  # top of the 1-49 band (no "49" grade)
    _graded_coin(db, "50", Decimal("1.00"))
    _graded_coin(db, "58", Decimal("1.00"))  # top of the 50-59 band (no "59" grade)
    _graded_coin(db, "60", Decimal("1.00"))
    _graded_coin(db, "64", Decimal("1.00"))
    _graded_coin(db, "65", Decimal("1.00"))
    _graded_coin(db, "70", Decimal("1.00"))
    _graded_coin(db, None, Decimal("1.00"))

    result = CB_GRADES.run(db, GradesParams())
    bands = {cast(str, r["band"]) for r in result.rows if r["view"] == "Coins"}
    assert bands == {"1-49", "50-59", "60-64", "65-70", "No numeric grade"}
    for band, expected in (
        ("1-49", 2),
        ("50-59", 2),
        ("60-64", 2),
        ("65-70", 2),
        ("No numeric grade", 1),
    ):
        total = sum(
            cast(int, r["items"])
            for r in result.rows
            if r["view"] == "Coins" and r["band"] == band
        )
        assert total == expected, band


def test_grades_no_strike_type_is_no_strike_type_and_no_service_is_raw(
    db: Session,
) -> None:
    """No competing row shares this (view, band): the drill is exact."""
    _graded_coin(db, "65", Decimal("1.00"))

    result = CB_GRADES.run(db, GradesParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["view"] == "Coins"
        and r["band"] == "65-70"
        and r["strike_type"] == "No strike type"
        and r["grading_service"] == "Raw"
    )
    assert result.rows[idx]["items"] == 1
    path, query = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == 1


def test_grades_splits_coins_and_currency(db: Session) -> None:
    _graded_coin(db, "65", Decimal("1.00"))
    note = _currency_note(db, _NOTE_1, Decimal("2.00"))
    note.grade_id = code_id(db, Grade, "N65")
    db.commit()

    result = CB_GRADES.run(db, GradesParams())
    views = {cast(str, r["view"]) for r in result.rows}
    assert views == {"Coins", "Currency"}


def test_grades_no_numeric_grade_row_has_no_drill(db: Session) -> None:
    _graded_coin(db, None, Decimal("1.00"))

    result = CB_GRADES.run(db, GradesParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["view"] == "Coins" and r["band"] == "No numeric grade"
    )
    assert result.drills[idx] is None


def test_grades_drill_matches_the_search_count_with_strike_and_service(
    db: Session,
) -> None:
    """Shape 1: every key set -- the drill is the group-by key, exactly."""
    _graded_coin(db, "65", Decimal("1.00"), strike="business", service="PCGS")
    _graded_coin(db, "66", Decimal("2.00"), strike="business", service="PCGS")
    _graded_coin(db, "65", Decimal("3.00"), strike="proof", service="PCGS")

    result = CB_GRADES.run(db, GradesParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["view"] == "Coins"
        and r["band"] == "65-70"
        and r["strike_type"] == "Business Strike"
        and r["grading_service"] == "PCGS"
    )
    assert result.rows[idx]["items"] == 2
    path, query = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == 2


def test_grades_drill_is_none_when_a_null_strike_type_has_a_competing_row(
    db: Session,
) -> None:
    """Shape 2: a NULL strike type competes with a row naming one.

    Both share the same view, band and service; omitting `strike_type=`
    from the search would also match the "business" row, so the "No
    strike type" row's own drill must be None rather than disagree.
    """
    _graded_coin(db, "65", Decimal("1.00"), service="PCGS")  # no strike recorded
    _graded_coin(db, "66", Decimal("2.00"), strike="business", service="PCGS")

    result = CB_GRADES.run(db, GradesParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["view"] == "Coins"
        and r["band"] == "65-70"
        and r["strike_type"] == "No strike type"
        and r["grading_service"] == "PCGS"
    )
    assert result.drills[idx] is None


def test_grades_drill_is_none_when_a_null_service_has_a_competing_row(
    db: Session,
) -> None:
    """Shape 3: a NULL grading service ("Raw") competes with a row naming one."""
    _graded_coin(db, "65", Decimal("1.00"), strike="business")  # no service recorded
    _graded_coin(db, "66", Decimal("2.00"), strike="business", service="PCGS")

    result = CB_GRADES.run(db, GradesParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["view"] == "Coins"
        and r["band"] == "65-70"
        and r["strike_type"] == "Business Strike"
        and r["grading_service"] == "Raw"
    )
    assert result.drills[idx] is None


def test_grades_totals_equal_the_sum_of_rows(db: Session) -> None:
    _graded_coin(db, "65", Decimal("1.11"))
    _graded_coin(db, None, Decimal("2.22"))

    result = CB_GRADES.run(db, GradesParams())
    assert result.totals is not None
    assert result.totals["items"] == sum(cast(int, r["items"]) for r in result.rows)
    assert result.totals["total_cost"] == sum(
        (cast(Decimal, r["total_cost"]) for r in result.rows), Decimal("0")
    )


def test_grades_excludes_deleted_and_split_items(db: Session) -> None:
    deleted = _graded_coin(db, "65", Decimal("1.00"))
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    parent = _graded_coin(db, "65", Decimal("3.00"), piece_count=2)
    parent.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    _graded_coin(db, "65", Decimal("2.00"), parent_item_id=parent.id)

    result = CB_GRADES.run(db, GradesParams())
    row = _grades_row(result, "Coins", "65-70", "No strike type", "Raw")
    assert row["items"] == 1
    assert row["total_cost"] == Decimal("2.00")


# ---------------------------------------------------------------------------
# cb_metal
# ---------------------------------------------------------------------------


def _metal_row(result: ReportResult, metal: str, form: str) -> dict[str, object]:
    return next(r for r in result.rows if r["metal"] == metal and r["form"] == form)


def test_metal_ounces_is_fine_weight_times_piece_count(db: Session) -> None:
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.500000"),
        piece_count=2,
        item_cost=Decimal("100.00"),
        tax_rate=Decimal("0"),
    )

    result = CB_METAL.run(db, MetalParams())
    row = _metal_row(result, "Silver", "Coin")
    assert row["items"] == 1
    assert row["ounces"] == Decimal("1.000000")


def test_metal_form_is_coin_bullion_form_label_or_kind_label(db: Session) -> None:
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.100000"),
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )
    build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "bullion"),
        metal_id=code_id(db, Metal, "silver"),
        bullion_form_id=code_id(db, BullionForm, "silver_eagle"),
        fine_weight_ozt=Decimal("1.000000"),
        item_cost=Decimal("30.00"),
        tax_rate=Decimal("0"),
        denomination_id=None,
    )
    build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "bullion"),
        metal_id=code_id(db, Metal, "silver"),
        bullion_form_id=None,
        fine_weight_ozt=Decimal("1.000000"),
        item_cost=Decimal("30.00"),
        tax_rate=Decimal("0"),
        denomination_id=None,
    )
    build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "medal"),
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("1.000000"),
        item_cost=Decimal("15.00"),
        tax_rate=Decimal("0"),
        denomination_id=None,
    )

    result = CB_METAL.run(db, MetalParams())
    assert _metal_row(result, "Silver", "Coin")["items"] == 1
    assert _metal_row(result, "Silver", "Silver Eagle")["items"] == 1
    assert _metal_row(result, "Silver", "No bullion form")["items"] == 1
    assert _metal_row(result, "Silver", "Medal")["items"] == 1


def test_metal_melt_uses_the_latest_price_and_missing_price_is_a_note(
    db: Session,
) -> None:
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("1.000000"),
        item_cost=Decimal("20.00"),
        tax_rate=Decimal("0"),
    )
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "gold"),
        fine_weight_ozt=Decimal("1.000000"),
        item_cost=Decimal("500.00"),
        tax_rate=Decimal("0"),
    )
    db.add(
        MetalPrice(
            metal_id=code_id(db, Metal, "silver"),
            quoted_at=datetime(2026, 1, 1, tzinfo=UTC),
            price_per_ozt=Decimal("20.0000"),
        )
    )
    db.add(
        MetalPrice(
            metal_id=code_id(db, Metal, "silver"),
            quoted_at=datetime(2026, 6, 1, tzinfo=UTC),
            price_per_ozt=Decimal("30.0000"),
        )
    )
    db.commit()

    result = CB_METAL.run(db, MetalParams())
    silver = _metal_row(result, "Silver", "Coin")
    assert silver["melt"] == Decimal("30.00")
    gold = _metal_row(result, "Gold", "Coin")
    assert gold["melt"] is None
    assert result.notes == [
        "Melt value totals only metals with a recorded price: Gold."
    ]
    assert result.totals is not None
    assert result.totals["melt"] == Decimal("30.00")


def test_metal_no_metal_row_is_covered_by_the_melt_note(db: Session) -> None:
    build_bare_item(
        db,
        metal_id=None,
        fine_weight_ozt=Decimal("1.000000"),
        item_cost=Decimal("20.00"),
        tax_rate=Decimal("0"),
    )

    result = CB_METAL.run(db, MetalParams())
    row = _metal_row(result, "No metal", "Coin")
    assert row["melt"] is None
    assert result.notes == [
        "Melt value totals only metals with a recorded price: No metal."
    ]


def test_metal_totals_equal_the_sum_of_rows_melt_over_priced_rows_only(
    db: Session,
) -> None:
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("1.000000"),
        item_cost=Decimal("20.00"),
        tax_rate=Decimal("0"),
    )
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "gold"),
        fine_weight_ozt=Decimal("1.000000"),
        item_cost=Decimal("500.00"),
        tax_rate=Decimal("0"),
    )
    db.add(
        MetalPrice(
            metal_id=code_id(db, Metal, "silver"),
            quoted_at=datetime(2026, 1, 1, tzinfo=UTC),
            price_per_ozt=Decimal("30.0000"),
        )
    )
    db.commit()

    result = CB_METAL.run(db, MetalParams())
    assert result.totals is not None
    assert result.totals["items"] == sum(cast(int, r["items"]) for r in result.rows)
    assert result.totals["ounces"] == sum(
        (cast(Decimal, r["ounces"]) for r in result.rows), Decimal("0")
    )
    assert result.totals["total_cost"] == sum(
        (cast(Decimal, r["total_cost"]) for r in result.rows), Decimal("0")
    )
    priced_melt = sum(
        (cast(Decimal, r["melt"]) for r in result.rows if r["melt"] is not None),
        Decimal("0"),
    )
    assert result.totals["melt"] == priced_melt
    assert result.totals["melt"] == Decimal("30.00")  # gold's melt excluded, not 0


def test_metal_drill_matches_the_search_count(db: Session) -> None:
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.500000"),
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.500000"),
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )

    result = CB_METAL.run(db, MetalParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["metal"] == "Silver" and r["form"] == "Coin"
    )
    assert result.drills[idx] is not None
    path, query = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == result.rows[idx]["items"]


def test_metal_drill_is_none_when_other_items_of_the_same_metal_have_no_fine_weight(
    db: Session,
) -> None:
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.500000"),
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )
    # Same metal and kind, but no fine weight recorded -- the coin search's
    # `metal=` filter cannot tell the two apart, so the count would disagree.
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=None,
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )

    result = CB_METAL.run(db, MetalParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["metal"] == "Silver" and r["form"] == "Coin"
    )
    assert result.drills[idx] is None


def test_metal_no_metal_row_drill_uses_missing_metal_and_matches(db: Session) -> None:
    build_bare_item(
        db,
        metal_id=None,
        fine_weight_ozt=Decimal("0.500000"),
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )

    result = CB_METAL.run(db, MetalParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["metal"] == "No metal" and r["form"] == "Coin"
    )
    assert result.drills[idx] is not None
    path, query = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    assert query.get("missing") == "metal"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == result.rows[idx]["items"]


def test_metal_excludes_deleted_and_split_items(db: Session) -> None:
    deleted = build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.500000"),
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    parent = build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.500000"),
        item_cost=Decimal("30.00"),
        tax_rate=Decimal("0"),
        piece_count=2,
    )
    parent.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    build_bare_item(
        db,
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.500000"),
        item_cost=Decimal("20.00"),
        tax_rate=Decimal("0"),
        parent_item_id=parent.id,
    )

    result = CB_METAL.run(db, MetalParams())
    row = _metal_row(result, "Silver", "Coin")
    assert row["items"] == 1
    assert row["total_cost"] == Decimal("20.00")


# ---------------------------------------------------------------------------
# cb_attributes
# ---------------------------------------------------------------------------


def _attr_row(
    result: ReportResult, mark: str, label: str, view: str
) -> dict[str, object]:
    return next(
        r
        for r in result.rows
        if r["mark"] == mark and r["label"] == label and r["view"] == view
    )


def test_attributes_lists_attributes_then_errors_ordered_by_label(
    db: Session,
) -> None:
    coin1 = _coin(db, _CENT, Decimal("1.00"))
    _add_attribute(db, coin1, "star")  # currency-only, but linkable regardless
    coin2 = _coin(db, _CENT, Decimal("2.00"))
    _add_error(db, coin2, "off_center")

    result = CB_ATTRIBUTES.run(db, AttributesParams())
    marks = [cast(str, r["mark"]) for r in result.rows]
    assert marks.index("Attribute") < marks.index("Error")


def test_attributes_counts_items_carrying_the_mark(db: Session) -> None:
    a = _coin(db, _CENT, Decimal("1.00"))
    b = _coin(db, _CENT, Decimal("2.00"))
    _add_attribute(db, a, "mule")
    _add_attribute(db, b, "mule")

    result = CB_ATTRIBUTES.run(db, AttributesParams())
    row = _attr_row(result, "Attribute", "Mule", "Coins")
    assert row["items"] == 2


def test_attributes_splits_rows_per_view_when_a_mark_appears_on_both(
    db: Session,
) -> None:
    coin = _coin(db, _CENT, Decimal("1.00"))
    _add_attribute(db, coin, "mule")
    note = _currency_note(db, _NOTE_1, Decimal("2.00"))
    _add_attribute(db, note, "mule")

    result = CB_ATTRIBUTES.run(db, AttributesParams())
    coin_row = _attr_row(result, "Attribute", "Mule", "Coins")
    note_row = _attr_row(result, "Attribute", "Mule", "Currency")
    assert coin_row["items"] == 1
    assert note_row["items"] == 1


def test_attributes_removed_attribute_is_excluded(db: Session) -> None:
    coin = _coin(db, _CENT, Decimal("1.00"))
    link = ItemAttributeLink(
        inventory_item_id=coin.id,
        item_attribute_id=code_id(db, ItemAttribute, "mule"),
        removed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db.add(link)
    db.commit()

    result = CB_ATTRIBUTES.run(db, AttributesParams())
    assert not [r for r in result.rows if r["label"] == "Mule"]


def test_attributes_error_split_rows_per_view(db: Session) -> None:
    coin = _coin(db, _CENT, Decimal("1.00"))
    _add_error(db, coin, "other")
    note = _currency_note(db, _NOTE_1, Decimal("2.00"))
    _add_error(db, note, "other")

    result = CB_ATTRIBUTES.run(db, AttributesParams())
    coin_row = _attr_row(result, "Error", "Other", "Coins")
    note_row = _attr_row(result, "Error", "Other", "Currency")
    assert coin_row["items"] == 1
    assert note_row["items"] == 1


def test_attributes_drill_matches_the_search_count(db: Session) -> None:
    a = _coin(db, _CENT, Decimal("1.00"))
    b = _coin(db, _CENT, Decimal("2.00"))
    _add_attribute(db, a, "mule")
    _add_attribute(db, b, "mule")

    result = CB_ATTRIBUTES.run(db, AttributesParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["mark"] == "Attribute" and r["label"] == "Mule" and r["view"] == "Coins"
    )
    path, query = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == 2


def test_attributes_error_drill_matches_the_search_count(db: Session) -> None:
    a = _coin(db, _CENT, Decimal("1.00"))
    _add_error(db, a, "off_center")

    result = CB_ATTRIBUTES.run(db, AttributesParams())
    idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["mark"] == "Error" and r["label"] == "Off Center"
    )
    path, query = _parsed(cast(str, result.drills[idx]))
    assert path == "/inventory/coins"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == 1


def test_attributes_has_no_total_because_an_item_may_carry_several(
    db: Session,
) -> None:
    coin = _coin(db, _CENT, Decimal("1.00"))
    _add_attribute(db, coin, "mule")

    result = CB_ATTRIBUTES.run(db, AttributesParams())
    assert result.totals is None
    assert result.notes


def test_attributes_excludes_deleted_and_split_items(db: Session) -> None:
    deleted = _coin(db, _CENT, Decimal("1.00"))
    _add_attribute(db, deleted, "mule")
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    parent = _coin(db, _CENT, Decimal("10.00"), piece_count=2)
    _add_attribute(db, parent, "mule")
    parent.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    child = _coin(db, _CENT, Decimal("5.00"), parent_item_id=parent.id)
    _add_attribute(db, child, "mule")

    result = CB_ATTRIBUTES.run(db, AttributesParams())
    row = _attr_row(result, "Attribute", "Mule", "Coins")
    assert row["items"] == 1
