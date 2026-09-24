"""Reading more of a rating: the phase-4 importer rules and the pass over stored items.

docs/specs/item-attributes-design.md, section 4. The ratings here are ones
the live collection actually carries (2026-09-16).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from app.field_sources import hold
from app.importers.engine import COMMIT, ImportEngine
from app.importers.models import ImportRow
from app.importers.profiles.collection_v1 import CollectionV1Profile
from app.importers.rating import bare_grade, designation_for, parse_condition
from app.models import (
    Authenticity,
    Grade,
    GradeDesignation,
    GradingService,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ItemFieldReview,
    ItemFieldSource,
    ReferenceMixin,
    StrikeType,
)
from app.rating_pass import run, write_csv
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_importer import FakeSource, make_row

ItemFactory = Callable[..., InventoryItem]


def _id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def _code(db: Session, model: type[ReferenceMixin], row_id: int | None) -> str | None:
    return None if row_id is None else db.get_one(model, row_id).code


def _attributes(db: Session, item_id: int) -> set[str]:
    return set(
        db.scalars(
            select(ItemAttribute.code)
            .join(ItemAttributeLink)
            .where(
                ItemAttributeLink.inventory_item_id == item_id,
                ItemAttributeLink.removed_at.is_(None),
            )
        )
    )


# --- the parser -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rating", "grade", "designation", "service"),
    [
        ("PR70DCAMPCGS  First Strike", "PR70", "DCAM", "PCGS"),
        ("SP68PCGS", "SP68", None, "PCGS"),
        ("SP69ICG", "SP69", None, "ICG"),
        ("P70DCAM PCGS", "PR70", "DCAM", "PCGS"),
        ("PF69 ULTRA CAMEO NGC", "PR69", "Ultra Cameo", "NGC"),
        ("PF70 ULTRACAMEO NGC", "PR70", "Ultra Cameo", "NGC"),
        ("PF69 UCAM NGC", "PR69", "UCAM", "NGC"),
        ("Gem DPL Private", None, "DPL", None),
        ("MS66 NGC FT", "MS66", "FT", "NGC"),
        ("MS66 6FS", "MS66", "6FS", None),
        # 66 Full Steps, not 6FS.
        ("MS66FS", "MS66", "FS", None),
        ("Early Release NGCX 10 Miles", None, None, None),
    ],
)
def test_a_rating_run_together_or_in_ngcs_words_is_read(
    rating: str, grade: str | None, designation: str | None, service: str | None
) -> None:
    parsed = parse_condition(rating)
    assert (parsed.grade, parsed.designation, parsed.service) == (
        grade,
        designation,
        service,
    )


@pytest.mark.parametrize(
    ("rating", "bare"),
    [
        ("69DCAM PCGS", "69"),
        ("69UCAM NGC", "69"),
        ("64 PCGS", "64"),
        ("66+ ICG", "66+"),
        ("66RB PCGS", "66"),
        ("70DCAM PCI", "70"),
        # An ordinal, not a red copper graded 3.
        ("3RD", None),
        ("23RD Anniversary", None),
        ("2 Graded PCGS", None),
        ("MS64 PCGS", None),
        ("1 of 2025", None),
    ],
)
def test_a_bare_number_is_one_beside_a_designation_or_grader(
    rating: str, bare: str | None
) -> None:
    assert parse_condition(rating).bare_number == bare


@pytest.mark.parametrize(
    ("rating", "service", "attributes"),
    [
        ("MS69 CAC FDOI", None, ("first_day_of_issue", "cac")),
        ("CAC PR69DCAM", None, ("cac",)),
        ("MS65 CAC Gold", None, ("cac_gold",)),
        # CACG's own label: graded by CACG, not stickered (decision 4).
        ("MS70 CAC First Delivery", "CACG", ("first_delivery",)),
        ("MS70 CAC First Day of Delivery NGC", "CACG", ("first_delivery",)),
        ("MS64 CACG", "CACG", ()),
        ("MS70 NGC Early Release", "NGC", ("early_releases",)),
        ("MS70 PCGS First Release Red", "PCGS", ("first_releases",)),
        ("MS70 PCGS First Strike 1 of 500", "PCGS", ("first_strike",)),
        ("No Motto", None, ("no_motto",)),
        ("godless", None, ("no_motto",)),
        # FS is never First Strike.
        ("MS70 FS NGC", "NGC", ()),
    ],
)
def test_attributes_and_the_cac_rule(
    rating: str, service: str | None, attributes: tuple[str, ...]
) -> None:
    parsed = parse_condition(rating)
    assert (parsed.service, parsed.attributes) == (service, attributes)


def test_genuine_is_an_attribute_and_an_authenticity() -> None:
    parsed = parse_condition("PCGS Genuine")
    assert (parsed.attributes, parsed.authenticity) == (("genuine",), "genuine")
    assert parse_condition("MS65").authenticity is None


@pytest.mark.parametrize(
    ("rating", "strike"),
    [
        ("Reverse PF70", "reverse_proof"),
        ("MS69 NGC Rev Proof Indian Chief", "reverse_proof"),
        ("Rev. PR70", "reverse_proof"),
        ("Enhanced Reverse Proof PF70", "enhanced_reverse_proof"),
        ("PF70 Proof", None),
        ("Reverse struck through", None),
    ],
)
def test_a_strike_named_in_words(rating: str, strike: str | None) -> None:
    assert parse_condition(rating).strike_type == strike


@pytest.mark.parametrize(
    ("rating", "context", "expected"),
    [
        ("69DCAM PCGS", "", ("69", "proof", "designation")),
        ("64 PL NGC", "", ("64", "business", "designation")),
        (
            "64 PCGS",
            "1958 Washington PCGS MS64",
            ("64", "business", "description grade"),
        ),
        ("67 ANACS", "1960 quarter ANACS PF67", ("67", "proof", "description grade")),
        (
            "68 PCGS",
            "2009 Kennedy PCGS SP68 Satin",
            ("68", "specimen", "description grade"),
        ),
        ("65 PCGS", "1955 5pc Proof Set PCGS", ("65", "proof", "description")),
        ("66 NGC", "2007-D Jefferson $1 NGC BU", ("66", "business", "description")),
        ("66+ ICG", "UNC ICG", ("66+", "business", "description")),
    ],
)
def test_the_strike_of_a_bare_number_is_settled_by(
    rating: str, context: str, expected: tuple[str, str, str]
) -> None:
    bare = bare_grade(parse_condition(rating), context)
    assert bare is not None
    assert (bare.grade, bare.strike_type, bare.by) == expected


@pytest.mark.parametrize(
    "context",
    [
        # Nothing to go on: a 67 is not MS67 by default.
        "1961 US WASHINGTON SILVER 25 Cents",
        # A lot that is both.
        "LOT OF 4 NGC Quarters 1958 NGC PF 68 1966 Ms65",
        "Proof and uncirculated set",
        # The same number written as both.
        "PR62 or MS62",
    ],
)
def test_a_bare_number_nothing_settles_is_left_for_a_person(context: str) -> None:
    assert bare_grade(parse_condition("62 NGC"), context) is None


def test_fs_is_full_steps_only_on_a_jefferson_nickel() -> None:
    parsed = parse_condition("MS70 FS NGC")
    assert designation_for(parsed, "2025 Silver Eagle NGC MS70") is None
    assert designation_for(parsed, "1950-D Jefferson Nickel") == "FS"
    assert designation_for(parse_condition("MS66 6FS"), "") == "6FS"


# --- the importer -----------------------------------------------------------------


def _import(db: Session, **cells: str) -> InventoryItem:
    row = make_row(2, **cells)
    report = ImportEngine(CollectionV1Profile(), session=db).run(
        FakeSource([row]), mode=COMMIT
    )
    staged = db.query(ImportRow).filter(ImportRow.batch_id == report.batch_id).one()
    return db.get_one(InventoryItem, staged.inventory_item_id)


def test_the_importer_grades_a_bare_number_and_reads_the_designation(
    db: Session,
) -> None:
    item = _import(db, Denom="1", Year="2019", Rating="69UCAM NGC")
    assert item.grade is not None and item.grade.code == "69"
    assert _code(db, StrikeType, item.strike_type_id) == "proof"
    # UCAM is an alias of DCAM.
    assert _code(db, GradeDesignation, item.grade_designation_id) == "DCAM"
    assert _code(db, GradingService, item.grading_service_id) == "NGC"


def test_the_importer_leaves_an_undecided_bare_number_ungraded(db: Session) -> None:
    item = _import(
        db, Denom="0.25", Year="1961", Rating="67 ANACS", Description="Washington"
    )
    assert item.grade_id is None
    assert item.attributes["strike_undecided"] == "67"
    assert item.attributes["rating_unparsed"] == "67 ANACS"


def test_the_importer_links_a_coins_attributes(db: Session) -> None:
    item = _import(db, Denom="1", Year="2024", Rating="MS70 CAC First Delivery")
    assert _code(db, GradingService, item.grading_service_id) == "CACG"
    assert _attributes(db, item.id) == {"first_delivery"}
    link = db.scalar(
        select(ItemAttributeLink).where(ItemAttributeLink.inventory_item_id == item.id)
    )
    assert link is not None and link.derived_by == "import"


def test_the_importer_records_genuine(db: Session) -> None:
    item = _import(db, Denom="1", Year="1921", Rating="PCGS Genuine")
    assert _code(db, Authenticity, item.authenticity_id) == "genuine"
    assert _attributes(db, item.id) == {"genuine"}
    assert item.grade_id is None
    assert "rating_unparsed" not in item.attributes


def test_the_importer_keeps_a_misfit_attribute_for_review(db: Session) -> None:
    item = _import(db, Denom="$1 Bill", Year="1957", Rating="Stamped FDOI")
    assert _attributes(db, item.id) == set()
    assert item.attributes["attribute_not_for_kind"] == ["first_day_of_issue"]


def test_the_importer_does_not_read_fs_on_a_silver_eagle(db: Session) -> None:
    item = _import(
        db,
        Denom="1",
        Year="2025",
        Rating="MS70 FS NGC",
        Description="Silver American Eagle",
    )
    assert item.grade_designation_id is None
    assert item.attributes["designation_not_read"] == "FS"


def test_the_importer_names_a_reverse_proof(db: Session) -> None:
    item = _import(db, Denom="1", Year="2023", Rating="Morgan Reverse PF70")
    assert _code(db, StrikeType, item.strike_type_id) == "reverse_proof"
    assert item.grade is not None and item.grade.code == "70"


# --- the pass over stored items ------------------------------------------------------


def _ungraded(
    make_item: ItemFactory, rating: str, description: str = ""
) -> InventoryItem:
    return make_item(
        grade_raw=rating, description=description, grade_id=None, strike_type_id=None
    )


def test_the_pass_fills_a_bare_number_from_the_description(
    db: Session, make_item: ItemFactory
) -> None:
    item = _ungraded(make_item, "64 PCGS", "1958 Washington PCGS MS64")

    report = run(db, commit=True)

    db.refresh(item)
    assert item.grade is not None and item.grade.code == "64"
    assert _code(db, StrikeType, item.strike_type_id) == "business"
    assert _code(db, GradingService, item.grading_service_id) == "PCGS"
    derived = dict(
        db.execute(
            select(ItemFieldSource.field_name, ItemFieldSource.derived_by).where(
                ItemFieldSource.inventory_item_id == item.id
            )
        )
        .tuples()
        .all()
    )
    assert derived == {
        "grade_id": "rating",
        "strike_type_id": "rating",
        "grading_service_id": "rating",
    }
    assert ("grade_id", "fill", "description grade") in report.counts()


def test_a_dry_run_writes_nothing(db: Session, make_item: ItemFactory) -> None:
    item = _ungraded(make_item, "69DCAM PCGS")
    report = run(db, commit=False)
    db.refresh(item)
    assert item.grade_id is None
    assert item.grade_designation_id is None
    assert {c.field for c in report.changes} == {
        "grade_id",
        "strike_type_id",
        "grade_designation_id",
        "grading_service_id",
    }


def test_the_pass_reports_what_it_cannot_settle(
    db: Session, make_item: ItemFactory
) -> None:
    item = _ungraded(make_item, "67 ANACS", "1961 US WASHINGTON SILVER 25 Cents")
    report = run(db, commit=True)
    db.refresh(item)
    assert item.grade_id is None
    assert report.undecided == [(item.item_code, "67 ANACS")]


def test_the_pass_leaves_what_a_person_confirmed_or_emptied(
    db: Session, make_item: ItemFactory
) -> None:
    confirmed = _ungraded(make_item, "69DCAM PCGS")
    emptied = _ungraded(make_item, "69DCAM PCGS")
    db.add(ItemFieldReview(inventory_item_id=confirmed.id, field_name="grade_id"))
    hold(db, [emptied.id], ["grade_designation_id"])
    db.commit()

    report = run(db, commit=True)

    db.refresh(confirmed)
    db.refresh(emptied)
    assert confirmed.grade_id is None
    assert confirmed.grade_designation_id is not None
    assert emptied.grade_designation_id is None
    assert emptied.grade_id is not None
    skipped = {(c.item_code, c.field) for c in report.changes if c.action == "skip"}
    assert skipped == {
        (confirmed.item_code, "grade_id"),
        (emptied.item_code, "grade_designation_id"),
    }


def test_the_pass_leaves_a_recorded_value_alone(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item(
        grade_raw="PR70DCAMPCGS",
        grade_designation_id=_id(db, GradeDesignation, "CAM"),
    )
    run(db, commit=True)
    db.refresh(item)
    assert _code(db, GradeDesignation, item.grade_designation_id) == "CAM"
    assert _code(db, GradingService, item.grading_service_id) == "PCGS"
    # The fixture's MS64 is a business strike; PR70 in the rating is a reading
    # of the grade, not a strike named in words, so it corrects nothing.
    assert _code(db, StrikeType, item.strike_type_id) == "business"


def test_the_pass_corrects_a_strike_the_rating_names(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item(
        grade_raw="PF70 Reverse Proof",
        strike_type_id=_id(db, StrikeType, "proof"),
    )
    report = run(db, commit=True)
    db.refresh(item)
    assert _code(db, StrikeType, item.strike_type_id) == "reverse_proof"
    assert [(c.field, c.before, c.after, c.action) for c in report.changes] == [
        ("strike_type_id", "proof", "reverse_proof", "correct")
    ]


def test_a_confirmed_strike_is_not_corrected(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item(
        grade_raw="PF70 Reverse Proof",
        strike_type_id=_id(db, StrikeType, "proof"),
    )
    db.add(ItemFieldReview(inventory_item_id=item.id, field_name="strike_type_id"))
    db.commit()
    run(db, commit=True)
    db.refresh(item)
    assert _code(db, StrikeType, item.strike_type_id) == "proof"


def test_the_pass_clears_fs_only_where_it_is_not_full_steps(
    db: Session, make_item: ItemFactory
) -> None:
    fs = _id(db, GradeDesignation, "FS")
    eagle = make_item(
        grade_raw="MS70 FS NGC",
        description="Silver Eagle",
        grade_designation_id=fs,
    )
    nickel = make_item(
        grade_raw="MS65 FS",
        description="1950-D Jefferson nickel",
        grade_designation_id=fs,
    )
    run(db, commit=True)
    db.refresh(eagle)
    db.refresh(nickel)
    assert eagle.grade_designation_id is None
    assert nickel.grade_designation_id == fs


def test_the_pass_adds_attributes_but_not_removed_ones(
    db: Session, make_item: ItemFactory
) -> None:
    fresh = make_item(grade_raw="MS69 CAC FDOI")
    removed = make_item(grade_raw="MS69 CAC FDOI")
    db.add(
        ItemAttributeLink(
            inventory_item_id=removed.id,
            item_attribute_id=_id(db, ItemAttribute, "cac"),
            removed_at=removed.created_at,
        )
    )
    db.commit()

    run(db, commit=True)

    assert _attributes(db, fresh.id) == {"cac", "first_day_of_issue"}
    assert _attributes(db, removed.id) == {"first_day_of_issue"}
    link = db.scalar(
        select(ItemAttributeLink).where(
            ItemAttributeLink.inventory_item_id == fresh.id,
        )
    )
    assert link is not None and link.derived_by == "rating"


def test_the_pass_marks_genuine_only_over_the_default(
    db: Session, make_item: ItemFactory
) -> None:
    unverified = make_item(grade_raw="NGC Genuine")
    counterfeit = make_item(grade_raw="NGC Genuine")
    counterfeit.authenticity_id = _id(db, Authenticity, "counterfeit")
    db.commit()
    run(db, commit=True)
    db.refresh(unverified)
    db.refresh(counterfeit)
    assert _code(db, Authenticity, unverified.authenticity_id) == "genuine"
    assert _code(db, Authenticity, counterfeit.authenticity_id) == "counterfeit"


def test_the_pass_leaves_a_notes_grade_to_the_note_scale(
    db: Session, make_item: ItemFactory
) -> None:
    """A note is given the note scale's 65, never the coin grade, and no strike.

    As the importer reads it (PCGS writes a note "MS65 PPQ"). The pass used
    to leave every note's grade alone, assuming the importer had read it --
    untrue for a note imported as a coin and re-kinded since.
    """
    note = make_item(
        kind="currency", grade_raw="MS65 PCGS", grade_id=None, strike_type_id=None
    )
    run(db, commit=True)
    db.refresh(note)
    assert _code(db, Grade, note.grade_id) == "N65"
    assert note.strike_type_id is None


def test_the_pass_reads_a_re_kinded_notes_rating(
    db: Session, make_item: ItemFactory
) -> None:
    """A re-kinded note rated 67 EPQ Radar gets N67, EPQ, and PMG implied."""
    note = make_item(
        kind="currency",
        grade_raw="67 EPQ Radar",
        grade_id=None,
        grade_designation_id=None,
        grading_service_id=None,
    )
    run(db, commit=True)
    db.refresh(note)
    assert _code(db, Grade, note.grade_id) == "N67"
    assert _code(db, GradeDesignation, note.grade_designation_id) == "EPQ"
    assert _code(db, GradingService, note.grading_service_id) == "PMG"


def test_ppq_implies_pcgs_and_a_named_grader_wins(
    db: Session, make_item: ItemFactory
) -> None:
    ppq = make_item(
        kind="currency", grade_raw="65 PPQ", grade_id=None, grading_service_id=None
    )
    glued = make_item(
        kind="currency",
        grade_raw="B-A Narrow (Misprint) PMG55",
        grade_id=None,
        grading_service_id=None,
    )
    run(db, commit=True)
    db.refresh(ppq)
    db.refresh(glued)
    assert _code(db, GradingService, ppq.grading_service_id) == "PCGS"
    assert _code(db, GradingService, glued.grading_service_id) == "PMG"
    assert _code(db, Grade, glued.grade_id) == "N55"


def test_the_report_is_written_for_review(
    db: Session, make_item: ItemFactory, tmp_path: Path
) -> None:
    _ungraded(make_item, "69DCAM PCGS")
    report = run(db, commit=False)
    path = tmp_path / "logs" / "rating_pass.csv"
    write_csv(report.changes, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "item_code,field,before,after,action,how,rating"
    assert len(lines) == 1 + len(report.changes)
