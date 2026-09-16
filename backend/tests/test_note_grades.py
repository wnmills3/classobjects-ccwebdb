"""Paper money is graded on its own scale, not a coin's.

PMG and PCGS both grade banknotes on the same points, Poor 1 to 70, in
their own words -- "Choice Uncirculated 64", "Very Fine 30" -- with their own
paper-quality designations, EPQ and PPQ. A note given a coin grade (MS65) is
described in the wrong vocabulary, and a note whose grade the importer could
not read ("64 EPQ Funnyback") was not described at all.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.importers.engine import COMMIT, ImportEngine
from app.importers.models import ImportRow
from app.importers.profiles.collection_v1 import CollectionV1Profile
from app.models import CurrencyDetail, Grade, ItemKind
from app.seed import SAMPLE_CATALOG
from fastapi.testclient import TestClient
from sqlalchemy import Row, func, select, text
from sqlalchemy.orm import Session

from tests.test_importer import FakeSource, make_row
from tests.test_schema import code_id, make_item

#: The points the graders use, Poor 1 to 70 (PCGS Banknote grades 1-3).
NOTE_NUMBERS = {1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 25, 30, 35, 40, 45, 50, 53, 55, 58}
NOTE_NUMBERS |= set(range(60, 71))


@pytest.fixture
def profile() -> CollectionV1Profile:
    return CollectionV1Profile()


def note_scale(db: Session) -> list[Row[Any]]:
    """Every grade on the paper-money scale: code, label, number."""
    return list(
        db.execute(
            text(
                "SELECT g.code, g.label, g.numeric_value FROM grade g "
                "JOIN grade_scale s ON s.id = g.grade_scale_id WHERE s.code = 'note'"
            )
        ).all()
    )


def committed(profile: CollectionV1Profile, db: Session, **values: str) -> Row[Any]:
    """Import one row for real and return the grade and designation it got."""
    report = ImportEngine(profile, session=db).run(
        FakeSource([make_row(2, **values)]), mode=COMMIT
    )
    staged = db.query(ImportRow).filter(ImportRow.batch_id == report.batch_id).one()
    return db.execute(
        text(
            "SELECT g.code AS grade, gd.code AS designation FROM inventory_item i "
            "LEFT JOIN grade g ON g.id = i.grade_id "
            "LEFT JOIN grade_designation gd ON gd.id = i.grade_designation_id "
            "WHERE i.id = :id"
        ),
        {"id": staged.inventory_item_id},
    ).one()


# ---------------------------------------------------------------------------
# The scale
# ---------------------------------------------------------------------------


def test_the_note_scale_is_the_graders_points_from_poor_1_to_70(
    db: Session,
) -> None:
    numbered = {n: label for _code, label, n in note_scale(db) if n is not None}

    assert set(numbered) == NOTE_NUMBERS
    # The graders' own words, checked where they are least guessable.
    assert numbered[70] == "Gem Unc 70"
    assert numbered[69] == "Superb Gem Unc 69"
    assert numbered[65] == "Gem Uncirculated 65"
    assert numbered[58] == "Choice About Unc 58"
    assert numbered[4] == "Good 4"


def test_every_note_grade_has_a_number(db: Session) -> None:
    """A bare "UNC" on a note is the bottom of its range, N60 (the owner).

    The words once had rows of their own, which no range search could find.
    """
    assert {code for code, _label, n in note_scale(db) if n is None} == set()


# ---------------------------------------------------------------------------
# Reading ratings from the spreadsheet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rating", "grade", "designation"),
    [
        ("64 EPQ Funnyback", "N64", "EPQ"),
        ("50 PPQ Back to Front Overprint", "N50", "PPQ"),
        ("VF-30", "N30", None),
        # PCGS writes coin-style prefixes on its banknote labels.
        ("MS65 PPQ", "N65", "PPQ"),
        ("UNC", "N60", None),
        # Five $2 notes, not a grade of 5: 5 is not a point on the scale.
        ("UNC 5 2s", "N60", None),
        ("AU+", "N50", None),
        # A seal colour says nothing about condition.
        ("Blue Seal", None, None),
    ],
)
def test_a_note_rating_is_read_onto_the_note_scale(
    profile: CollectionV1Profile,
    db: Session,
    rating: str,
    grade: str | None,
    designation: str | None,
) -> None:
    row = committed(profile, db, Denom="$1 Bill", Rating=rating)
    assert (row.grade, row.designation) == (grade, designation)


def test_a_coin_keeps_its_coin_grade(profile: CollectionV1Profile, db: Session) -> None:
    assert committed(profile, db, Denom="0.25", Rating="MS65").grade == "65"


def test_reading_a_note_grade_never_invents_a_vocabulary_row(
    profile: CollectionV1Profile, db: Session
) -> None:
    """A note's grade is chosen, not made: "AU+" once became a grade row of its own."""
    before = db.scalar(select(func.count()).select_from(Grade))
    committed(profile, db, Denom="$1 Bill", Rating="AU+ Star Note")
    assert db.scalar(select(func.count()).select_from(Grade)) == before


# ---------------------------------------------------------------------------
# Showing it
# ---------------------------------------------------------------------------


def test_the_currency_inventory_shows_a_grade_by_its_label(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A note's grade code is `N64`; what the owner reads is its label."""
    item = make_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        grade_id=code_id(db, Grade, "N64"),
    )
    db.add(CurrencyDetail(inventory_item_id=item.id))
    db.commit()

    body = client.get("/api/inventory/currency/search", headers=admin_headers).json()
    row = next(r for r in body["rows"] if r["id"] == item.id)
    assert row["grade_label"] == "Choice Uncirculated 64"


def test_the_demo_catalogue_grades_its_banknotes_on_the_note_scale(db: Session) -> None:
    """`app.seed`'s demo notes follow the same rule as imported ones.

    Its 1957-B Silver Certificate carried the coin scale's UNC, and was the one
    note left on a coin scale after a rebuild.
    """
    scale_of = dict(
        db.execute(
            text(
                "SELECT g.code, s.code FROM grade g "
                "LEFT JOIN grade_scale s ON s.id = g.grade_scale_id"
            )
        ).all()
    )
    notes = [entry for entry in SAMPLE_CATALOG if entry.get("item_kind") == "currency"]

    assert notes
    for entry in notes:
        assert scale_of.get(entry["grade"]) == "note", entry["title"]
