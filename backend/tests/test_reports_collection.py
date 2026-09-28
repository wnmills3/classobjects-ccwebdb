"""`cb_holdings`: what the collection is made of, by kind and denomination."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast, get_args
from urllib.parse import parse_qs, urlsplit

import pytest
from app.inventory_search import COIN_VIEW, CURRENCY_VIEW
from app.inventory_search import search as inventory_search
from app.models import Denomination, Disposition, InventoryItem, ItemKind, ItemStatus
from app.reports.base import ReportResult
from app.reports.collection import (
    CB_HOLDINGS,
    DispositionParam,
    HoldingsParams,
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
