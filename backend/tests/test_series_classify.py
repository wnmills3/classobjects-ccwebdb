"""Classifying items into design series from denomination, year and letter.

Each test builds its own items on top of the seeded vocabulary, so the ranges
under test are the ones that ship in data/reference/series.json.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

from app.inventory_search import CURRENCY_VIEW, search
from app.models import (
    CurrencyDetail,
    Denomination,
    InventoryItem,
    PurchaseOrder,
    ReferenceMixin,
    SealColor,
    Series,
    SeriesYearRange,
    Vendor,
)
from app.seeding import seed_all
from app.series_classify import classify, run
from app.series_match import run as match_run
from sqlalchemy import select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]

DIME, QUARTER, DOLLAR = "usd_coin_0_10", "usd_coin_0_25", "usd_coin_1_00"
NOTE_1, NOTE_5 = "usd_note_1", "usd_note_5"


def _id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def _series_code(db: Session, item: InventoryItem) -> str | None:
    db.refresh(item)
    if item.series_id is None:
        return None
    return db.get_one(Series, item.series_id).code


def _coin(
    db: Session, make_item: ItemFactory, denomination: str, year: int, **extra: object
) -> InventoryItem:
    extra.setdefault("title", "Plain coin")
    return make_item(
        denomination_id=_id(db, Denomination, denomination), year_start=year, **extra
    )


def _note(
    db: Session,
    make_item: ItemFactory,
    denomination: str,
    series: int,
    letter: str | None = None,
    *,
    seal: str | None = None,
    **extra: object,
) -> InventoryItem:
    extra.setdefault("title", "Plain note")
    item = make_item(
        kind="currency",
        denomination_id=_id(db, Denomination, denomination),
        year_start=series,
        **extra,
    )
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id,
            series_year=series,
            series_letter=letter,
            seal_color_id=_id(db, SealColor, seal) if seal else None,
        )
    )
    db.commit()
    return item


def _case(db: Session, item: InventoryItem) -> tuple[str, tuple[str, ...]] | None:
    report = classify(db)
    for case in report.review:
        if case.item_code == item.item_code:
            return case.reason, case.designs
    return None


# -- the facts decide ---------------------------------------------------------


def test_a_single_candidate_is_assigned(db: Session, make_item: ItemFactory) -> None:
    # Nothing in the text: the denomination and year are enough.
    dime = _coin(db, make_item, DIME, 1942, description="10C MS65")

    run(db, commit=True)

    assert _series_code(db, dime) == "winged_liberty_head_dime"


def test_a_dry_run_writes_nothing(db: Session, make_item: ItemFactory) -> None:
    dime = _coin(db, make_item, DIME, 1942)

    report = run(db, commit=False)

    assert report.assignments.get(dime.id) == _id(
        db, Series, "winged_liberty_head_dime"
    )
    assert _series_code(db, dime) is None


def test_an_item_with_a_series_is_never_touched(
    db: Session, make_item: ItemFactory
) -> None:
    # The "wrong" series on purpose: a hand correction always stands.
    barber = _id(db, Series, "barber_dime")
    dime = _coin(db, make_item, DIME, 1942, series_id=barber)

    run(db, commit=True)

    assert _series_code(db, dime) == "barber_dime"


def test_a_re_run_changes_nothing(db: Session, make_item: ItemFactory) -> None:
    dime = _coin(db, make_item, DIME, 1942)
    run(db, commit=True)

    again = classify(db)

    assert dime.id not in again.assignments
    assert _series_code(db, dime) == "winged_liberty_head_dime"


def test_a_year_span_is_not_a_single_year(db: Session, make_item: ItemFactory) -> None:
    dime = _coin(db, make_item, DIME, 1942, year_end=1944)

    run(db, commit=True)

    assert _series_code(db, dime) is None


# -- boundaries, gaps and conflicts ------------------------------------------


def test_a_boundary_year_goes_to_review(db: Session, make_item: ItemFactory) -> None:
    dime = _coin(db, make_item, DIME, 1916)

    assert _case(db, dime) == ("boundary", ("barber_dime", "winged_liberty_head_dime"))
    run(db, commit=True)
    assert _series_code(db, dime) is None


def test_the_text_resolves_a_boundary_year(db: Session, make_item: ItemFactory) -> None:
    dime = _coin(db, make_item, DIME, 1916, description="Nice Mercury, full bands")

    run(db, commit=True)

    assert _series_code(db, dime) == "winged_liberty_head_dime"


def test_the_morgan_gap_is_not_morgan(db: Session, make_item: ItemFactory) -> None:
    # Without the 1905-1920 gap, every dollar since 1878 would be a Morgan.
    dollar = _coin(db, make_item, DOLLAR, 1910)

    run(db, commit=True)

    assert _series_code(db, dollar) is None
    assert _case(db, dollar) is None


def test_1921_is_both_morgan_and_peace(db: Session, make_item: ItemFactory) -> None:
    dollar = _coin(db, make_item, DOLLAR, 1921)

    assert _case(db, dollar) == ("boundary", ("morgan_dollar", "peace_dollar"))


def test_text_naming_an_impossible_design_is_a_conflict(
    db: Session, make_item: ItemFactory
) -> None:
    # Measured in the collection: $1 notes rated "funnyback" but recorded as
    # Series 1923, a large-size note. The rating or the year is wrong; the
    # pass must say so rather than pick one.
    note = _note(db, make_item, NOTE_1, 1923, grade_raw="VF funnyback")

    assert _case(db, note) == ("conflict", ("funnyback",))
    run(db, commit=True)
    assert _series_code(db, note) is None


def test_lot_text_is_not_evidence_about_a_piece(
    db: Session, make_item: ItemFactory
) -> None:
    # Measured in the collection: a lot's pieces carry the lot's listing,
    # which names designs the piece is not. The 1943 cent is a Lincoln cent
    # whatever the lot says; the same words on a piece alone are a conflict.
    vendor = Vendor(name="Lot seller")
    db.add(vendor)
    db.flush()
    order, single = (
        PurchaseOrder(
            vendor_id=vendor.id, order_number=number, ordered_on=date(2024, 12, 1)
        )
        for number in ("LOT-1", "ONE-1")
    )
    db.add_all([order, single])
    db.flush()
    lot = "Collection: Large Cents, Morgan dollars"
    pieces = [
        _coin(
            db,
            make_item,
            "usd_coin_0_01",
            1943,
            description=lot,
            purchase_order_id=order.id,
        ),
        _coin(
            db,
            make_item,
            "usd_coin_0_05",
            1943,
            description=lot,
            purchase_order_id=order.id,
        ),
    ]
    # In an order of its own, so an item is not taken for its own sibling.
    alone = _coin(
        db,
        make_item,
        "usd_coin_0_01",
        1944,
        description=lot,
        purchase_order_id=single.id,
    )

    run(db, commit=True)

    assert _series_code(db, pieces[0]) == "lincoln_cent"
    assert _series_code(db, pieces[1]) == "jefferson_nickel"
    assert _series_code(db, alone) is None
    assert _case(db, alone) == ("conflict", ("large_cent", "morgan_dollar"))


def test_a_commemorative_needs_its_word(db: Session, make_item: ItemFactory) -> None:
    # Measured: dozens of commemorative halves would otherwise be Kennedys.
    worded = _coin(
        db, make_item, "usd_coin_0_50", 1992, title="1992 Olympic Commemorative Half"
    )
    unworded = _coin(db, make_item, "usd_coin_0_50", 1992)
    gold = _coin(db, make_item, DOLLAR, 1852, title="1852 Rare Gold Dollar AU")
    # 1886: gold dollars were still struck, trade dollars no longer.
    silver = _coin(db, make_item, DOLLAR, 1886)

    run(db, commit=True)

    assert _series_code(db, worded) == "commemorative_half"
    assert _series_code(db, unworded) == "kennedy_half"
    assert _series_code(db, gold) == "gold_dollar"
    assert _series_code(db, silver) == "morgan_dollar"


def test_a_lot_of_commemoratives_sends_its_pieces_to_review(
    db: Session, make_item: ItemFactory
) -> None:
    vendor = Vendor(name="Commem seller")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(
        vendor_id=vendor.id, order_number="LOT-2", ordered_on=date(2024, 12, 1)
    )
    db.add(order)
    db.flush()
    lot = "Lot of 3 Washington/Carver Commemorative Half Dollars"
    pieces = [
        _coin(
            db,
            make_item,
            "usd_coin_0_50",
            year,
            description=lot,
            purchase_order_id=order.id,
        )
        for year in (1952, 1953)
    ]

    run(db, commit=True)

    assert _series_code(db, pieces[0]) is None
    assert _case(db, pieces[0]) == (
        "boundary",
        ("commemorative_half", "franklin_half"),
    )


def test_a_series_the_facts_rule_out_is_reported_not_changed(
    db: Session, make_item: ItemFactory
) -> None:
    # Real case: "2010-D Franklin Pierce $1.00 Coin" matched as a Franklin half.
    franklin = _id(db, Series, "franklin_half")
    dollar = _coin(db, make_item, DOLLAR, 2010, series_id=franklin)
    agrees = _coin(db, make_item, "usd_coin_0_50", 1955, series_id=franklin)

    assert _case(db, dollar) == ("disagrees", ("franklin_half",))
    assert _case(db, agrees) is None
    run(db, commit=True)
    assert _series_code(db, dollar) == "franklin_half"


def test_text_naming_a_candidate_as_well_is_not_a_conflict(
    db: Session, make_item: ItemFactory
) -> None:
    # "Lincoln" is also a cent design; the facts say this can only be the
    # presidential dollar, and the text agrees.
    dollar = _coin(
        db,
        make_item,
        DOLLAR,
        2010,
        title="2010-D Abraham Lincoln Presidential Dollar",
    )

    run(db, commit=True)

    assert _series_code(db, dollar) == "presidential_dollar"


# -- notes ------------------------------------------------------------------


def test_every_1928_dollar_is_a_funnyback(db: Session, make_item: ItemFactory) -> None:
    # The owner's decision: the red-seal United States Note counts too.
    red = _note(db, make_item, NOTE_1, 1928, seal="red")
    blue = _note(db, make_item, NOTE_1, 1928, "B", seal="blue")

    run(db, commit=True)

    assert _series_code(db, red) == "funnyback"
    assert _series_code(db, blue) == "funnyback"


def test_the_series_letter_decides_a_barr_note(
    db: Session, make_item: ItemFactory
) -> None:
    barr = _note(db, make_item, NOTE_1, 1963, "B")
    not_barr = _note(db, make_item, NOTE_1, 1963, "A")
    plain = _note(db, make_item, NOTE_1, 1963)

    run(db, commit=True)

    assert _series_code(db, barr) == "barr_note"
    assert _series_code(db, not_barr) is None
    assert _series_code(db, plain) is None


def test_a_needs_evidence_design_is_not_assigned_without_it(
    db: Session, make_item: ItemFactory
) -> None:
    # A $5 1934A is also every ordinary note of that series.
    ordinary = _note(db, make_item, NOTE_5, 1934, "A", seal="blue")

    report = run(db, commit=True)

    assert _series_code(db, ordinary) is None
    assert report.counts["ordinary"] >= 1
    assert _case(db, ordinary) is None


def test_the_seal_is_evidence(db: Session, make_item: ItemFactory) -> None:
    brown = _note(db, make_item, NOTE_5, 1934, "A", seal="brown")
    yellow = _note(db, make_item, NOTE_5, 1934, "A", seal="yellow")

    run(db, commit=True)

    assert _series_code(db, brown) == "hawaii"
    assert _series_code(db, yellow) == "north_africa"


def test_the_text_is_evidence(db: Session, make_item: ItemFactory) -> None:
    note = _note(db, make_item, NOTE_5, 1934, description="HAWAII overprint, VF")

    run(db, commit=True)

    assert _series_code(db, note) == "hawaii"


def test_a_classified_funnyback_is_found_by_its_nickname(
    db: Session, make_item: ItemFactory
) -> None:
    note = _note(db, make_item, NOTE_1, 1934, description="Silver certificate")
    run(db, commit=True)

    rows, _ = search(db, CURRENCY_VIEW, params={}, query="funnyback")

    assert note.item_code in {row["item_code"] for row in rows}


# -- series_match stays within its inventory --------------------------------


def test_a_coin_saying_hawaii_is_not_a_hawaii_note(
    db: Session, make_item: ItemFactory
) -> None:
    quarter = _coin(db, make_item, QUARTER, 2008, title="2008-P Hawaii State Quarter")

    match_run(db, commit=True)

    assert _series_code(db, quarter) == "state_quarters"


def test_series_match_leaves_notes_to_the_facts(
    db: Session, make_item: ItemFactory
) -> None:
    note = _note(db, make_item, NOTE_1, 1923, description="Funnyback")

    match_run(db, commit=True)

    assert _series_code(db, note) is None


# -- seeding ----------------------------------------------------------------


def _ranges(db: Session, code: str) -> set[tuple[int, int | None]]:
    rows = db.execute(
        select(SeriesYearRange.year_start, SeriesYearRange.year_end)
        .join(Series, Series.id == SeriesYearRange.series_id)
        .where(Series.code == code)
    ).all()
    return {(start, end) for start, end in rows}


def test_seeded_ranges_load_and_reload_unchanged(db: Session) -> None:
    assert _ranges(db, "morgan_dollar") == {(1878, 1904), (1921, 1921), (2021, None)}

    stats = seed_all(db, only=["series_year_range"])

    counter = stats["series_year_range"]
    assert counter["created"] == counter["updated"] == counter["removed"] == 0
    assert counter["unchanged"] >= 3


def test_seeded_ranges_follow_the_file(db: Session, tmp_path: Path) -> None:
    # A design named in the file gets exactly the file's ranges; one the file
    # does not name keeps its own.
    (tmp_path / "ranges.json").write_text(
        json.dumps(
            {
                "series_year_range": [
                    {"series": "morgan_dollar", "year_start": 1878, "year_end": 1921}
                ]
            }
        ),
        encoding="utf-8",
    )
    peace_before = _ranges(db, "peace_dollar")

    seed_all(db, tmp_path, only=["series_year_range"])

    assert _ranges(db, "morgan_dollar") == {(1878, 1921)}
    assert _ranges(db, "peace_dollar") == peace_before


# -- Series 1929 National Bank Notes: a design with a note class ---------------


def _class(db: Session, item: InventoryItem, code: str) -> None:
    from app.models import NoteType

    detail = db.execute(
        select(CurrencyDetail).where(CurrencyDetail.inventory_item_id == item.id)
    ).scalar_one()
    detail.note_type_id = _id(db, NoteType, code)
    db.commit()


def test_a_1929_national_needs_its_bank_named_or_its_class(
    db: Session, make_item: ItemFactory
) -> None:
    # Measured on live: the 1929 notes name their bank in the rating, and the
    # brown seal is on both classes.
    named = _note(
        db,
        make_item,
        "usd_note_10",
        1929,
        seal="brown",
        grade_raw="T1 National City Bank of New York 1461",
    )
    classed = _note(db, make_item, "usd_note_20", 1929, seal="brown")
    _class(db, classed, "national_bank_note")
    reserve = _note(
        db, make_item, "usd_note_10", 1929, seal="brown", grade_raw="Fed Res Boston"
    )

    run(db, commit=True)

    assert _series_code(db, named) == "national_bank_note_1929"
    assert _series_code(db, classed) == "national_bank_note_1929"
    # A brown seal alone says nothing: Federal Reserve Bank Notes have one too.
    assert _series_code(db, reserve) is None


def test_a_federal_reserve_bank_note_is_never_a_national(
    db: Session, make_item: ItemFactory
) -> None:
    # Even when its text says "Brown Seal", the owner's own nickname.
    note = _note(
        db, make_item, "usd_note_10", 1929, seal="brown", grade_raw="Brown Seal"
    )
    _class(db, note, "frbn")

    run(db, commit=True)

    assert _series_code(db, note) is None
    assert _case(db, note) == ("conflict", ("national_bank_note_1929",))


def test_a_national_recorded_as_another_class_is_reported(
    db: Session, make_item: ItemFactory
) -> None:
    national = _id(db, Series, "national_bank_note_1929")
    note = _note(db, make_item, "usd_note_10", 1929, seal="brown", series_id=national)
    _class(db, note, "frbn")

    assert _case(db, note) == ("disagrees", ("national_bank_note_1929",))


def test_brown_seal_finds_the_1929_nationals(
    db: Session, make_item: ItemFactory
) -> None:
    national = _id(db, Series, "national_bank_note_1929")
    note = _note(db, make_item, "usd_note_5", 1929, seal="brown", series_id=national)

    rows, _ = search(db, CURRENCY_VIEW, params={}, query="brown seal")

    assert note.item_code in {row["item_code"] for row in rows}
