"""Import engine and profile tests.

The **engine** is durable and tested thoroughly. The **profile** is disposable,
so it is tested at the level of aggregate behaviour and rule *ordering* -- not
one test per correction rule, which would be effort spent on code that is going
to be deleted.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.importers.engine import COMMIT, DRY_RUN, ImportEngine
from app.importers.models import ImportBatch, ImportIssue, ImportRow
from app.importers.profile import ERROR, UNKNOWN, Classification, RawRow, RowResult
from app.importers.profiles.collection_v1 import CollectionV1Profile

HEADERS = [
    "Ordered", "Order Number", "Denom", "Year", "Rating", "Price",
    "Link", "Description", "Vendor", "Shipping", "Grading#", "Value", "Comment",
]


def make_row(n: int = 2, **overrides: str) -> RawRow:
    values = {h: None for h in HEADERS}
    values.update(overrides)
    return RawRow(row_number=n, values=values)


class FakeSource:
    """A source that needs no file, so engine tests stay fast."""

    kind = "fake"
    path = "<fake>"

    def __init__(self, rows: list[RawRow], sha: str = "a" * 64) -> None:
        self._rows = rows
        self.sha256 = sha

    def read_rows(self, limit=None):
        for i, r in enumerate(self._rows):
            if limit is not None and i >= limit:
                break
            yield r


@pytest.fixture
def profile() -> CollectionV1Profile:
    return CollectionV1Profile()


# --------------------------------------------------------------------------
# Classification -- ordering is the invariant worth protecting
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "denom,expected",
    [
        ("0.25", "coin"),
        ("1", "coin"),
        ("2.5", "coin"),
        ("$1 Bill", "currency"),
        ("1 Bill", "currency"),
        ("Dollar Bill", "currency"),
        ("10c Bill", "currency"),
        ("5 Rupees", "currency"),
        ("20 Pound", "currency"),
        ("Silver Eagle", "bullion"),
        ("Silver Round 1oz", "bullion"),
        ("Copper Bar 1oz", "bullion"),
        ("Mint Set", "set"),
        ("Proof Set", "set"),
        ("Medal", "medal"),
        ("Token", "token"),
        ("", UNKNOWN),
        ("Mixed", UNKNOWN),
    ],
)
def test_classification(profile: CollectionV1Profile, denom: str, expected: str) -> None:
    assert profile.inspect(make_row(Denom=denom)).classification.kind == expected


def test_bullion_is_tested_before_number_then_word(profile: CollectionV1Profile) -> None:
    """The single easiest rule to break by reordering.

    "1oz Copper Round" matches "a number followed by a word", which is the
    currency rule. Bullion must win, or a third of the collection is misfiled.
    """
    for denom in ("1oz Copper Round", "20x 1oz Copper", "5 oz Silver Bar"):
        assert profile.inspect(make_row(Denom=denom)).classification.kind == "bullion"


def test_sets_are_tested_before_currency(profile: CollectionV1Profile) -> None:
    assert profile.inspect(make_row(Denom="3-Bill Set")).classification.kind == "set"


def test_unclassified_rows_are_flagged_for_review(profile: CollectionV1Profile) -> None:
    result = profile.inspect(make_row(Denom="Pirate Money"))
    assert result.classification.kind == UNKNOWN
    assert result.needs_review
    assert any(i.rule == "unclassified" for i in result.issues)


def test_correction_map_is_logged_not_silent(profile: CollectionV1Profile) -> None:
    result = profile.inspect(make_row(Denom="$20 Blll"))
    assert result.classification.kind == "currency"
    issue = next(i for i in result.issues if i.rule == "denomination-corrected")
    assert issue.raw_value == "$20 Blll" and issue.proposed == "$20 Bill"


# --------------------------------------------------------------------------
# Field rules that encode real damage or real ambiguity
# --------------------------------------------------------------------------


def test_scientific_notation_identifier_is_an_error(profile: CollectionV1Profile) -> None:
    """Excel already destroyed these; the importer can only detect them."""
    result = profile.inspect(make_row(Denom="0.25", **{"Grading#": "5.0157E+14"}))
    issue = next(
        i for i in result.issues if i.rule == "identifier-lost-to-scientific-notation"
    )
    assert issue.severity == ERROR
    assert result.needs_review


def test_grading_column_routes_by_kind(profile: CollectionV1Profile) -> None:
    """The same column holds a note's serial or a certificate serial."""
    note = profile.inspect(make_row(Denom="$1 Bill", **{"Grading#": "L10861665*"}))
    coin = profile.inspect(make_row(Denom="0.25", **{"Grading#": "45141114"}))
    assert note.fields["serial_number"] == "L10861665*"
    assert coin.fields["cert_number"] == "45141114"


def test_value_column_carries_amount_or_status(profile: CollectionV1Profile) -> None:
    assert profile.inspect(make_row(Denom="1", Value="50")).fields["numismatic_value"] == Decimal("50")
    assert profile.inspect(make_row(Denom="1", Value="x")).fields["status_marker"] == "received"
    assert profile.inspect(make_row(Denom="1", Value="Canceled")).fields["status_marker"] == "canceled"
    unknown = profile.inspect(make_row(Denom="1", Value="???"))
    assert any(i.rule == "value-not-understood" for i in unknown.issues)


def test_series_letter_is_not_a_mint_mark(profile: CollectionV1Profile) -> None:
    note = profile.inspect(make_row(Denom="$1 Bill", Year="2017-A")).fields
    coin = profile.inspect(make_row(Denom="0.25", Year="1921-D")).fields
    assert note.get("series_letter") == "A" and "mint_marks" not in note
    assert coin.get("mint_marks") == ["D"] and "series_letter" not in coin


def test_year_range_and_multiplier(profile: CollectionV1Profile) -> None:
    ranged = profile.inspect(make_row(Denom="0.25", Year="1999-2008")).fields
    assert (ranged["year_start"], ranged["year_end"]) == (1999, 2008)
    assert profile.inspect(make_row(Denom="20x 1oz Copper")).fields["storage_quantity"] == 20
    assert profile.inspect(make_row(Denom="0.25")).fields["storage_quantity"] == 1


def test_money_is_decimal_never_float(profile: CollectionV1Profile) -> None:
    fields = profile.inspect(make_row(Denom="1", Price="19.99", Shipping="0")).fields
    assert isinstance(fields["price"], Decimal)
    assert fields["price"] == Decimal("19.99")


# --------------------------------------------------------------------------
# Engine -- durable, tested properly
# --------------------------------------------------------------------------


def test_dry_run_touches_no_database(profile: CollectionV1Profile, db: Session) -> None:
    """Dry run is the default and must work with no session at all."""
    engine = ImportEngine(profile, session=None)
    report = engine.run(FakeSource([make_row(2, Denom="0.25")]), mode=DRY_RUN)
    assert report.rows == 1
    assert report.batch_id is None
    assert db.query(ImportBatch).count() == 0
    assert db.query(ImportRow).count() == 0


def test_commit_requires_a_session(profile: CollectionV1Profile) -> None:
    with pytest.raises(ValueError):
        ImportEngine(profile, session=None).run(FakeSource([]), mode=COMMIT)


def test_commit_writes_batch_rows_and_issues(
    profile: CollectionV1Profile, db: Session
) -> None:
    rows = [
        make_row(2, Denom="0.25", Price="10"),
        make_row(3, Denom="Pirate Money"),          # unclassified -> issue
        make_row(4, Denom="$1 Bill", **{"Grading#": "5.0157E+14"}),  # error
    ]
    report = ImportEngine(profile, session=db).run(FakeSource(rows), mode=COMMIT)

    batch = db.get(ImportBatch, report.batch_id)
    assert batch is not None
    assert batch.row_count == 3
    assert batch.finished_at is not None
    assert batch.profile_name == "collection_v1"

    stored = db.query(ImportRow).filter(ImportRow.batch_id == batch.id).all()
    assert len(stored) == 3
    assert {r.item_kind for r in stored} == {"coin", UNKNOWN, "currency"}
    assert db.query(ImportIssue).count() >= 2


def test_raw_row_is_stored_verbatim(profile: CollectionV1Profile, db: Session) -> None:
    """Nothing is coerced on the way in -- that is the whole point of staging."""
    row = make_row(2, Denom="$20 Blll", Price="1,450.00", Value="x")
    report = ImportEngine(profile, session=db).run(FakeSource([row]), mode=COMMIT)
    stored = db.query(ImportRow).filter(ImportRow.batch_id == report.batch_id).one()
    assert stored.raw["Denom"] == "$20 Blll", "the typo must survive into staging"
    assert stored.raw["Price"] == "1,450.00", "formatting must not be normalised away"


def test_limit_stops_early(profile: CollectionV1Profile) -> None:
    rows = [make_row(i, Denom="0.25") for i in range(2, 12)]
    report = ImportEngine(profile).run(FakeSource(rows), mode=DRY_RUN, limit=4)
    assert report.rows == 4


def test_report_counts_reconcile(profile: CollectionV1Profile) -> None:
    rows = [
        make_row(2, Denom="0.25"),
        make_row(3, Denom="Silver Eagle"),
        make_row(4, Denom="$1 Bill"),
        make_row(5, Denom="Mixed"),
    ]
    report = ImportEngine(profile).run(FakeSource(rows), mode=DRY_RUN)
    assert report.rows == 4
    assert sum(report.kinds.values()) == report.rows, "every row lands in exactly one kind"
    assert report.classified == 3
    assert report.unclassified_values["Mixed"] == 1


def test_report_renders_without_error(profile: CollectionV1Profile) -> None:
    report = ImportEngine(profile).run(
        FakeSource([make_row(2, Denom="Mixed")]), mode=DRY_RUN
    )
    text = report.render()
    assert "UNCLASSIFIED VALUES" in text and "collection_v1" in text


# --------------------------------------------------------------------------
# Reporting -- reviewable without a database
# --------------------------------------------------------------------------


def test_issue_records_carry_the_source_row_number(profile: CollectionV1Profile) -> None:
    """A typo is only actionable if you know which row to fix."""
    rows = [make_row(2, Denom="0.25"), make_row(4711, Denom="$20 Blll")]
    report = ImportEngine(profile).run(FakeSource(rows), mode=DRY_RUN)
    fix = next(r for r in report.corrections() if r.raw_value == "$20 Blll")
    assert fix.row_number == 4711
    assert fix.proposed == "$20 Bill"


def test_unclassified_values_record_their_rows(profile: CollectionV1Profile) -> None:
    rows = [make_row(10, Denom="Mixed"), make_row(20, Denom="Mixed"), make_row(30, Denom="0.25")]
    report = ImportEngine(profile).run(FakeSource(rows), mode=DRY_RUN)
    assert report.unclassified_rows["Mixed"] == [10, 20]


def test_write_all_produces_reviewable_files(profile: CollectionV1Profile, tmp_path) -> None:
    import csv as _csv

    from app.importers import reporting

    rows = [
        make_row(2, Denom="0.25", Price="10"),
        make_row(3, Denom="$20 Blll"),
        make_row(4, Denom="Pirate Money"),
        make_row(5, Denom="1", **{"Grading#": "5.0157E+14"}),
    ]
    report = ImportEngine(profile).run(FakeSource(rows), mode=DRY_RUN)
    paths = reporting.write_all(report, tmp_path)

    for path in paths.values():
        assert path.exists(), f"{path} was not written"

    with paths["corrections"].open(encoding="utf-8-sig") as fh:
        corrections = list(_csv.DictReader(fh))
    typo = next(r for r in corrections if r["raw_value"] == "$20 Blll")
    assert typo["suggested_fix"] == "$20 Bill"
    assert typo["rows"] == "3", "the exact source row must be named"

    with paths["unclassified"].open(encoding="utf-8-sig") as fh:
        unclassified = list(_csv.DictReader(fh))
    assert any(r["raw_value"] == "Pirate Money" and r["rows"] == "4" for r in unclassified)

    with paths["issues"].open(encoding="utf-8-sig") as fh:
        issues = list(_csv.DictReader(fh))
    err = next(r for r in issues if r["severity"] == "error")
    assert err["row_number"] == "5"
    # the whole source row travels with the issue, for context
    assert "src::Denom" in err and err["src::Denom"] == "1"


def test_report_names_rows_for_corrections(profile: CollectionV1Profile) -> None:
    rows = [make_row(77, Denom="$2Bill")]
    text = ImportEngine(profile).run(FakeSource(rows), mode=DRY_RUN).render()
    assert "SUGGESTED SOURCE CORRECTIONS" in text
    assert "rows 77" in text
