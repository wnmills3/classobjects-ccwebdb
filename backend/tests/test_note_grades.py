"""Paper money is graded on its own scale, not a coin's.

PMG and PCGS both grade banknotes on the same points, Poor 1 to 70, in
their own words -- "Choice Uncirculated 64", "Very Fine 30" -- with their own
paper-quality designations, EPQ and PPQ. A note given a coin grade (MS65) is
described in the wrong vocabulary.
"""

from __future__ import annotations

from typing import Any

from app.models import CurrencyDetail, Grade, ItemKind
from app.seed import SAMPLE_CATALOG
from fastapi.testclient import TestClient
from sqlalchemy import Row, text
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item

#: The points the graders use, Poor 1 to 70 (PCGS Banknote grades 1-3).
NOTE_NUMBERS = {1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 25, 30, 35, 40, 45, 50, 53, 55, 58}
NOTE_NUMBERS |= set(range(60, 71))


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
    """`app.seed`'s demo notes follow the same rule as every other note.

    Its 1957-B Silver Certificate must carry the note scale's grade, not the
    coin scale's UNC.
    """
    scale_of: dict[str, str | None] = dict(
        db.execute(
            text(
                "SELECT g.code, s.code FROM grade g "
                "LEFT JOIN grade_scale s ON s.id = g.grade_scale_id"
            )
        )
        .tuples()
        .all()
    )
    notes = [entry for entry in SAMPLE_CATALOG if entry.get("item_kind") == "currency"]

    assert notes
    for entry in notes:
        assert scale_of.get(entry["grade"]) == "note", entry["title"]
