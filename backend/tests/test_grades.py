"""Grades as a strike type and a number (docs/specs/item-attributes-design.md).

``PR69+`` is a proof graded 69+, ``MS65`` a business strike graded 65, and an
adjectival grade the bottom of its range: the owner's ladder puts BU at 60,
BU+ at 63 and BU++ at 65. A plus ranks half a point above its number.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from app import grades
from app.importers.engine import COMMIT, ImportEngine
from app.importers.models import ImportRow
from app.importers.profiles.collection_v1 import CollectionV1Profile
from app.models import Grade, GradeScale, ItemKind, Listing, StrikeType
from fastapi.testclient import TestClient
from sqlalchemy import Row, select, text
from sqlalchemy.orm import Session

from tests.test_importer import FakeSource, make_row
from tests.test_schema import code_id, make_item

MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "e4b8c1d27f63_strike_type_and_number_grades.py"
)


# ---------------------------------------------------------------------------
# Taking a grade apart
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text_", "strike", "grade"),
    [
        ("MS65", "business", "65"),
        ("PR69+", "proof", "69+"),
        ("PF-70", "proof", "70"),
        ("SP64", "specimen", "64"),
        ("AU-58", "business", "58"),
        ("VF20+", "business", "20+"),
        # The owner's ladder: plain, Choice, Gem.
        ("UNC", "business", "60"),
        ("BU", "business", "60"),
        ("UNC+", "business", "63"),
        ("BU+", "business", "63"),
        ("CHOICE_BU", "business", "63"),
        ("BU++", "business", "65"),
        ("GEM_BU", "business", "65"),
        ("GEM BU", "business", "65"),
        ("GEM_BU++", "business", "65+"),
        ("PROOF", "proof", "63"),
        ("CHOICE_PROOF", "proof", "63"),
        ("GEM_PROOF", "proof", "65"),
        # Circulated words: the bottom of the range, and a plus above it.
        ("AU", "business", "55"),
        ("AU+", "business", "55+"),
        ("AU++", "business", "55+"),
        ("XF", "business", "40"),
        ("VF+", "business", "20+"),
        ("VG+", "business", "8+"),
        # Already apart, or not a coin grade.
        ("65", None, "65"),
        ("64+", None, "64+"),
        ("N64", None, "N64"),
        ("N_UNC", None, "N60"),
        ("N_AU", None, "N50"),
        ("CIRC", None, "CIRC"),
    ],
)
def test_a_grade_is_taken_apart(text_: str, strike: str | None, grade: str) -> None:
    assert grades.split(text_) == grades.Split(strike, grade)


@pytest.mark.parametrize("text_", ["", "Blue Seal", "MS", "BU+++X", "NOT_A_GRADE"])
def test_what_is_not_a_grade_is_not_split(text_: str) -> None:
    assert grades.split(text_) is None


def test_a_strike_type_the_client_names_wins() -> None:
    assert grades.split_fields("MS65", "sms") == ("65", "sms")
    assert grades.split_fields("MS65", None) == ("65", "business")
    # Unknown grades pass through for the code lookup to refuse.
    assert grades.split_fields("MS64PL", None) == ("MS64PL", None)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("grade_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_migration_splits_old_codes_the_way_the_app_does() -> None:
    """The migration keeps its own frozen copy; today the two must agree."""
    migration = _load_migration()
    old_codes = [
        "MS65", "PR69+", "PF70", "SP64", "AU58", "BU", "BU+", "BU++", "UNC",
        "UNC+", "CHOICE_BU", "GEM_BU", "GEM_UNC", "PROOF", "CHOICE_PROOF",
        "GEM_PROOF", "AU", "AU+", "XF", "VF", "F", "VG", "G", "N_UNC", "N_AU",
    ]  # fmt: skip
    for code in old_codes:
        parts = migration._split(code)
        expected = grades.split(code)
        assert parts is not None and expected is not None, code
        strike, number, plus, note_code = parts
        if note_code:
            assert (None, note_code) == (expected.strike_type, expected.grade), code
        else:
            got = grades.Split(strike, grades.number_code(number, plus))
            assert got == expected, code
    # The test database is built with the app's function, the real one by the
    # migration's.
    assert migration._grade_display_sql() == grades.GRADE_DISPLAY_SQL


# ---------------------------------------------------------------------------
# Showing it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prefix", "suffix", "number", "plus", "shown"),
    [
        (None, None, 65, False, "MS65"),
        (None, None, 58, True, "AU58+"),
        (None, None, 45, False, "XF45"),
        (None, None, 20, False, "VF20"),
        (None, None, 12, False, "F12"),
        (None, None, 8, False, "VG8"),
        (None, None, 4, False, "G4"),
        (None, None, 3, False, "AG3"),
        (None, None, 2, False, "FR2"),
        (None, None, 1, False, "PO1"),
        ("PR", None, 69, True, "PR69+"),
        ("SP", None, 64, False, "SP64"),
        ("PR", "Reverse Proof", 70, False, "PR70 Reverse Proof"),
        (None, "SMS", 67, False, "MS67 SMS"),
    ],
)
def test_a_grade_is_shown_as_collectors_write_it(
    db: Session,
    prefix: str | None,
    suffix: str | None,
    number: int,
    plus: bool,
    shown: str,
) -> None:
    assert grades.display(prefix, suffix, number, plus, "x", sheldon=True) == shown
    # The database composes it the same way.
    in_sql = db.execute(
        text("SELECT grade_display(:p, :s, :n, :plus, 'x', true)"),
        {"p": prefix, "s": suffix, "n": number, "plus": plus},
    ).scalar_one()
    assert in_sql == shown


def test_a_note_grade_and_an_unnumbered_one_show_their_label(db: Session) -> None:
    assert grades.display(None, None, 64, False, "Choice Unc 64", sheldon=False) == (
        "Choice Unc 64"
    )
    assert grades.display(None, None, None, False, "Circulated", sheldon=True) == (
        "Circulated"
    )
    assert (
        db.execute(
            text("SELECT grade_display(NULL, NULL, NULL, false, 'Circulated', true)")
        ).scalar_one()
        == "Circulated"
    )


def test_every_seeded_strike_type_shows_as_the_app_shows_it(db: Session) -> None:
    for strike in db.scalars(select(StrikeType)):
        in_sql = db.execute(
            text("SELECT grade_display(:p, :s, 66, true, 'x', true)"),
            {"p": strike.prefix, "s": strike.suffix},
        ).scalar_one()
        assert in_sql == grades.display(
            strike.prefix, strike.suffix, 66, True, "x", sheldon=True
        ), strike.code


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_a_plus_ranks_between_its_number_and_the_next(db: Session) -> None:
    rank = dict(db.execute(select(Grade.code, Grade.grade_rank)).all())
    assert rank["64"] < rank["64+"] < rank["65"]
    assert rank["64+"] == Decimal("64.5")


def test_coin_grades_are_numbers_on_the_sheldon_scale(db: Session) -> None:
    sheldon = db.scalar(select(GradeScale.id).where(GradeScale.code == "sheldon"))
    rows = db.execute(select(Grade).where(Grade.grade_scale_id == sheldon)).scalars()
    for row in rows:
        assert row.code == grades.number_code(row.numeric_value, row.is_plus)


# ---------------------------------------------------------------------------
# Setting it through the API
# ---------------------------------------------------------------------------


def test_a_compound_grade_sent_to_an_item_is_split(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}", json={"grade": "PR69+"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text

    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert (body["grade"], body["strike_type"]) == ("69+", "proof")
    assert body["grade_display"] == "PR69+"


def test_a_number_alone_leaves_the_strike_type(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(
        db,
        grade_id=code_id(db, Grade, "65"),
        strike_type_id=code_id(db, StrikeType, "proof"),
    )
    client.patch(
        f"/api/inventory/{item.id}", json={"grade": "66"}, headers=admin_headers
    )
    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert body["grade_display"] == "PR66"


def test_an_unknown_strike_type_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"strike_type": "wishful"},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "strike_type" in response.text


# ---------------------------------------------------------------------------
# Importing it
# ---------------------------------------------------------------------------


def _imported(db: Session, rating: str) -> Row[Any]:
    report = ImportEngine(CollectionV1Profile(), session=db).run(
        FakeSource([make_row(2, Denom="0.25", Rating=rating)]), mode=COMMIT
    )
    staged = db.query(ImportRow).filter(ImportRow.batch_id == report.batch_id).one()
    return db.execute(
        text(
            "SELECT g.code AS grade, stk.code AS strike FROM inventory_item i "
            "LEFT JOIN grade g ON g.id = i.grade_id "
            "LEFT JOIN strike_type stk ON stk.id = i.strike_type_id "
            "WHERE i.id = :id"
        ),
        {"id": staged.inventory_item_id},
    ).one()


@pytest.mark.parametrize(
    ("rating", "grade", "strike"),
    [
        ("PR69+", "69+", "proof"),
        ("MS65", "65", "business"),
        ("BU++", "65", "business"),
        ("AU+", "55+", "business"),
        ("PROOF", "63", "proof"),
    ],
)
def test_an_imported_rating_is_split(
    db: Session, rating: str, grade: str, strike: str
) -> None:
    row = _imported(db, rating)
    assert (row.grade, row.strike) == (grade, strike)


def test_an_unlisted_number_grade_is_added_as_derived(db: Session) -> None:
    """61+ is a real point on the scale even though no grader uses it."""
    row = _imported(db, "MS61+")
    added = db.scalar(select(Grade).where(Grade.code == "61+"))
    assert row.grade == "61+"
    assert added is not None
    assert (added.numeric_value, added.is_plus) == (61, True)
    assert added.source.value == "derived"
    assert added.grade_scale is not None and added.grade_scale.code == "sheldon"


# ---------------------------------------------------------------------------
# Searching it
# ---------------------------------------------------------------------------


@pytest.fixture
def graded(db: Session) -> dict[str, int]:
    """One coin at each of a spread of grades, keyed by how it shows."""
    coin = code_id(db, ItemKind, "coin")
    made = {}
    for grade, strike in [
        ("55", "business"),
        ("55+", "business"),
        ("58", "business"),
        ("60", "business"),
        ("62+", "business"),
        ("63", "business"),
        ("64+", "business"),
        ("65", "business"),
        ("66+", "business"),
        ("67", "business"),
        ("65", "proof"),
        ("65", "sms"),
        ("65", None),
    ]:
        item = make_item(
            db,
            item_kind_id=coin,
            grade_id=code_id(db, Grade, grade),
            strike_type_id=code_id(db, StrikeType, strike) if strike else None,
        )
        made[f"{strike}:{grade}"] = item.id
    return made


def _found(
    client: TestClient, headers: dict[str, str], graded: dict[str, int], **params: str
) -> set[str]:
    response = client.get(
        "/api/inventory/coins/search",
        params={**params, "limit": 200},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    by_id = {v: k for k, v in graded.items()}
    return {by_id[row["id"]] for row in response.json()["rows"] if row["id"] in by_id}


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        # The owner's examples: a number is exact, % takes the plus too.
        ("55", {"business:55"}),
        ("55+", {"business:55+"}),
        ("55%", {"business:55", "business:55+"}),
        # The ladder: BU 60-62, BU+ 63-64, BU++ 65-66, BU% all of them.
        ("BU", {"business:60", "business:62+"}),
        ("BU+", {"business:63", "business:64+"}),
        ("BU++", {"business:65", "sms:65", "None:65", "business:66+"}),
        (
            "BU%",
            {
                "business:60",
                "business:62+",
                "business:63",
                "business:64+",
                "business:65",
                "sms:65",
                "None:65",
                "business:66+",
            },
        ),
        # A prefix is the strike the grade shows.
        ("PR65", {"proof:65"}),
        ("MS65", {"business:65", "sms:65", "None:65"}),
        ("65", {"business:65", "proof:65", "sms:65", "None:65"}),
        ("PROOF", {"proof:65"}),
        # A word is its whole range; a plus after it, the plus grades in it.
        ("AU", {"business:55", "business:55+", "business:58"}),
        ("AU+", {"business:55+"}),
    ],
)
def test_a_grade_search_term(
    client: TestClient,
    admin_headers: dict[str, str],
    graded: dict[str, int],
    term: str,
    expected: set[str],
) -> None:
    assert _found(client, admin_headers, graded, grade=term) == expected


def test_grade_bounds_read_a_plus(
    client: TestClient, admin_headers: dict[str, str], graded: dict[str, int]
) -> None:
    found = _found(client, admin_headers, graded, grade_min="64+", grade_max="66")
    assert found == {
        "business:64+",
        "business:65",
        "proof:65",
        "sms:65",
        "None:65",
    }
    # 66% includes 66+.
    wider = _found(client, admin_headers, graded, grade_min="64+", grade_max="66%")
    assert wider == found | {"business:66+"}


def test_the_strike_type_is_a_filter_and_a_column(
    client: TestClient, admin_headers: dict[str, str], graded: dict[str, int]
) -> None:
    assert _found(client, admin_headers, graded, strike_type="proof") == {"proof:65"}
    response = client.get(
        "/api/inventory/coins/search",
        params={"strike_type": "proof", "facets": "true"},
        headers=admin_headers,
    ).json()
    assert response["rows"][0]["grade"] == "PR65"
    assert response["rows"][0]["strike_type"] == "proof"
    assert {f["value"] for f in response["facets"]["strike_type"]} == {"proof"}


@pytest.mark.parametrize("term", ["MS55", "BU+++", "XYZ", "65++"])
def test_a_term_that_is_not_a_grade_is_refused(
    client: TestClient, admin_headers: dict[str, str], term: str
) -> None:
    response = client.get(
        "/api/inventory/coins/search", params={"grade": term}, headers=admin_headers
    )
    assert response.status_code == 422
    assert "not a grade" in response.json()["detail"]


def test_the_catalogue_shows_the_composed_grade(
    client: TestClient, make_listing: Callable[..., Listing]
) -> None:
    """A compound grade set on the item is read back composed, via the shop."""
    listing = make_listing(title="Proof Kennedy", grade="PR69+", price=Decimal("20.00"))

    response = client.get(f"/api/catalog/{listing.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["grade"], body["strike_type"], body["grade_display"]) == (
        "69+",
        "proof",
        "PR69+",
    )
