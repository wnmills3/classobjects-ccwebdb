"""Import engine and profile tests.

The **engine** is durable and tested thoroughly. The **profile** is disposable,
so it is tested at the level of aggregate behaviour and rule *ordering* -- not
one test per correction rule, which would be effort spent on code that is going
to be deleted.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from app.importers.engine import COMMIT, DRY_RUN, ImportEngine
from app.importers.loader import parse_condition
from app.importers.models import ImportBatch, ImportIssue, ImportRow
from app.importers.profile import ERROR, UNKNOWN, RawRow
from app.importers.profiles.collection_v1 import CollectionV1Profile
from app.importers.profiling import ColumnProfile
from app.order_repair import identify
from sqlalchemy.orm import Session

HEADERS = [
    "Ordered",
    "Order Number",
    "Denom",
    "Year",
    "Rating",
    "Price",
    "Link",
    "Description",
    "Vendor",
    "Shipping",
    "Grading#",
    "Value",
    "Comment",
]


def make_row(n: int = 2, **overrides: str) -> RawRow:
    values = dict.fromkeys(HEADERS)
    values.update(overrides)
    return RawRow(row_number=n, values=values)


class FakeSource:
    """A source that needs no file, so engine tests stay fast."""

    kind = "fake"
    path = "<fake>"

    def __init__(self, rows: list[RawRow], sha: str = "a" * 64) -> None:
        """A source that yields the rows it was handed."""
        self._rows = rows
        self.sha256 = sha

    def read_rows(self, limit: int | None = None) -> Iterator[RawRow]:
        """Yield the rows, at most `limit` of them."""
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
        ("Zzz Nonexistent", UNKNOWN),
    ],
)
def test_classification(
    profile: CollectionV1Profile, denom: str, expected: str
) -> None:
    assert profile.inspect(make_row(Denom=denom)).classification.kind == expected


def test_bullion_is_tested_before_number_then_word(
    profile: CollectionV1Profile,
) -> None:
    """The single easiest rule to break by reordering.

    "1oz Copper Round" matches "a number followed by a word", which is the
    currency rule. Bullion must win, or a third of the collection is misfiled.
    """
    for denom in ("1oz Copper Round", "20x 1oz Copper", "5 oz Silver Bar"):
        assert profile.inspect(make_row(Denom=denom)).classification.kind == "bullion"


def test_sets_are_tested_before_currency(profile: CollectionV1Profile) -> None:
    assert profile.inspect(make_row(Denom="3-Bill Set")).classification.kind == "set"


def test_unclassified_rows_are_flagged_for_review(profile: CollectionV1Profile) -> None:
    result = profile.inspect(make_row(Denom="Zzz Nonexistent"))
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


def test_scientific_notation_identifier_is_an_error(
    profile: CollectionV1Profile,
) -> None:
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
    assert profile.inspect(make_row(Denom="1", Value="50")).fields[
        "numismatic_value"
    ] == Decimal("50")
    assert (
        profile.inspect(make_row(Denom="1", Value="x")).fields["status_marker"]
        == "received"
    )
    assert (
        profile.inspect(make_row(Denom="1", Value="Canceled")).fields["status_marker"]
        == "canceled"
    )
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
    assert (
        profile.inspect(make_row(Denom="20x 1oz Copper")).fields["storage_quantity"]
        == 20
    )
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
        make_row(3, Denom="Zzz Nonexistent"),  # unclassified -> issue
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
        make_row(5, Denom="Zzz Nonexistent"),
    ]
    report = ImportEngine(profile).run(FakeSource(rows), mode=DRY_RUN)
    assert report.rows == 4
    assert sum(report.kinds.values()) == report.rows, (
        "every row lands in exactly one kind"
    )
    assert report.classified == 3
    assert report.unclassified_values["Zzz Nonexistent"] == 1


def test_report_renders_without_error(profile: CollectionV1Profile) -> None:
    report = ImportEngine(profile).run(
        FakeSource([make_row(2, Denom="Zzz Nonexistent")]), mode=DRY_RUN
    )
    text = report.render()
    assert "UNCLASSIFIED VALUES" in text and "collection_v1" in text


# --------------------------------------------------------------------------
# Reporting -- reviewable without a database
# --------------------------------------------------------------------------


def test_issue_records_carry_the_source_row_number(
    profile: CollectionV1Profile,
) -> None:
    """A typo is only actionable if you know which row to fix."""
    rows = [make_row(2, Denom="0.25"), make_row(4711, Denom="$20 Blll")]
    report = ImportEngine(profile).run(FakeSource(rows), mode=DRY_RUN)
    fix = next(r for r in report.corrections() if r.raw_value == "$20 Blll")
    assert fix.row_number == 4711
    assert fix.proposed == "$20 Bill"


def test_unclassified_values_record_their_rows(profile: CollectionV1Profile) -> None:
    rows = [
        make_row(10, Denom="Zzz Nonexistent"),
        make_row(20, Denom="Zzz Nonexistent"),
        make_row(30, Denom="0.25"),
    ]
    report = ImportEngine(profile).run(FakeSource(rows), mode=DRY_RUN)
    assert report.unclassified_rows["Zzz Nonexistent"] == [10, 20]


def test_write_all_produces_reviewable_files(
    profile: CollectionV1Profile, tmp_path: Path
) -> None:
    import csv as _csv

    from app.importers import reporting

    rows = [
        make_row(2, Denom="0.25", Price="10"),
        make_row(3, Denom="$20 Blll"),
        make_row(4, Denom="Zzz Nonexistent"),
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
    assert any(
        r["raw_value"] == "Zzz Nonexistent" and r["rows"] == "4" for r in unclassified
    )

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


# --------------------------------------------------------------------------
# Column profiling
# --------------------------------------------------------------------------


def _profiled(rows: list[dict[str, str]]) -> list[ColumnProfile]:
    from app.importers import profiling

    report = ImportEngine(CollectionV1Profile()).run(FakeSource(rows), mode=DRY_RUN)
    return {p.name: p for p in profiling.profile_columns(report)}


def test_normalise_collapses_case_space_and_punctuation() -> None:
    from app.importers.profiling import normalise

    assert normalise("$20 Blll") == normalise("$20blll") == "20blll"
    assert normalise("Silver Eagle") == normalise("SilverEagle")


def test_repeating_column_is_recommended_as_a_reference_table() -> None:
    from app.importers.profiling import REFERENCE

    rows = [make_row(i, Denom="0.25", Rating="UNC") for i in range(2, 60)]
    assert _profiled(rows)["Rating"].recommendation == REFERENCE


def test_nearly_unique_long_column_is_free_text() -> None:
    from app.importers.profiling import FREE_TEXT

    rows = [
        make_row(i, Denom="0.25", Description=f"a distinct long description number {i}")
        for i in range(2, 60)
    ]
    assert _profiled(rows)["Description"].recommendation == FREE_TEXT


def test_empty_column_is_flagged_for_dropping() -> None:
    from app.importers.profiling import EMPTY

    rows = [make_row(i, Denom="0.25") for i in range(2, 10)]
    assert _profiled(rows)["Comment"].recommendation == EMPTY


def test_variants_point_the_rare_spelling_at_the_dominant_one() -> None:
    """The mechanism that finds typos without a hand-written typo list."""
    rows = [make_row(i, Denom="0.25", Rating="UNC") for i in range(2, 40)]
    rows.append(make_row(99, Denom="0.25", Rating="Unc"))
    variants = _profiled(rows)["Rating"].variants
    assert len(variants) == 1
    assert variants[0].rare_value == "Unc"
    assert variants[0].likely_intended == "UNC"


def test_a_spelling_that_does_not_dominate_is_not_flagged() -> None:
    """Two common spellings are a real distinction, not a typo."""
    rows = [make_row(i, Denom="0.25", Rating="UNC") for i in range(2, 22)]
    rows += [make_row(i, Denom="0.25", Rating="Unc") for i in range(30, 50)]
    assert _profiled(rows)["Rating"].variants == []


def test_known_good_values_are_not_flagged() -> None:
    """A 1/4 oz Gold Eagle has a $25 face value; it is not a typo for 2.5."""
    from app.importers import profiling
    from app.importers.profiles.collection_v1 import KNOWN_GOOD

    rows = [make_row(i, Denom="2.5") for i in range(2, 40)]
    rows.append(make_row(99, Denom="25"))
    report = ImportEngine(CollectionV1Profile()).run(FakeSource(rows), mode=DRY_RUN)

    without = {p.name: p for p in profiling.profile_columns(report)}
    assert any(v.rare_value == "25" for v in without["Denom"].variants), (
        "without the known-good list the detector should still propose it"
    )

    with_known = {p.name: p for p in profiling.profile_columns(report, KNOWN_GOOD)}
    assert not any(v.rare_value == "25" for v in with_known["Denom"].variants)


def test_trailing_plus_is_never_proposed_for_removal() -> None:
    """UNC+ and MS64+ are grades, not misspellings of UNC and MS64."""
    from app.importers import profiling

    rows = [make_row(i, Denom="0.25", Rating="UNC") for i in range(2, 40)]
    rows += [make_row(i, Denom="0.25", Rating="UNC+") for i in range(50, 52)]
    report = ImportEngine(CollectionV1Profile()).run(FakeSource(rows), mode=DRY_RUN)
    variants = {p.name: p for p in profiling.profile_columns(report)}["Rating"].variants
    assert not any(v.rare_value == "UNC+" for v in variants)


def test_uncertain_and_range_years_are_not_flagged() -> None:
    from app.importers import profiling
    from app.importers.profiles.collection_v1 import KNOWN_GOOD

    rows = [make_row(i, Denom="0.25", Year="2024-") for i in range(2, 40)]
    rows += [
        make_row(90, Denom="0.25", Year="2024?"),
        make_row(91, Denom="0.25", Year="1980's"),
    ]
    report = ImportEngine(CollectionV1Profile()).run(FakeSource(rows), mode=DRY_RUN)
    variants = {p.name: p for p in profiling.profile_columns(report, KNOWN_GOOD)}[
        "Year"
    ].variants
    flagged = {v.rare_value for v in variants}
    assert "2024?" not in flagged and "1980's" not in flagged


# --------------------------------------------------------------------------
# Weight extraction
# --------------------------------------------------------------------------


def weight_of(profile: CollectionV1Profile, denom: str) -> None:
    return profile.inspect(make_row(Denom=denom)).fields.get("weight_ozt")


@pytest.mark.parametrize(
    "denom,expected",
    [
        ("Silver Round 1oz", "1.000000"),
        ("Copper Round 5oz", "5.000000"),
        ("Gold Maple 1/10 oz", "0.100000"),
        ("Silver Square 10g", "0.321507"),
        ("Gold Nugget 1.5gm", "0.048226"),
        ("1Kilo Silver", "32.150747"),
        ("Titanium 1lb", "14.583333"),
        ("Silver Coins 9.3303oz", "9.330300"),
    ],
)
def test_weight_converts_to_troy_ounces(
    profile: CollectionV1Profile, denom: str, expected: str
) -> None:
    assert weight_of(profile, denom) == Decimal(expected)


def test_leading_point_is_a_fraction_not_a_whole_number(
    profile: CollectionV1Profile,
) -> None:
    """'.5 oz' is a half ounce. Reading it as 5 would be ten times the metal."""
    assert weight_of(profile, "Silver Round .5 oz") == Decimal("0.500000")
    assert weight_of(profile, "Silver Round .5oz") == Decimal("0.500000")
    assert weight_of(profile, "Copper 1/2 Kilo") == Decimal("16.075373")


def test_weight_is_decimal_never_float(profile: CollectionV1Profile) -> None:
    """It multiplies into money, so it must be exact."""
    value = weight_of(profile, "Silver Round 1oz")
    assert isinstance(value, Decimal)
    # three tenth-ounce coins are exactly three tenths, not 0.30000000000000004
    tenth = weight_of(profile, "Gold Maple 1/10 oz")
    assert tenth * 3 == Decimal("0.300000")


def test_equivalent_units_agree(profile: CollectionV1Profile) -> None:
    """1000 g and 1 kilo are the same mass and must convert identically."""
    assert weight_of(profile, "1000gm Silver") == weight_of(profile, "1Kilo Silver")


def test_weight_is_per_piece_not_per_lot(profile: CollectionV1Profile) -> None:
    result = profile.inspect(make_row(Denom="20x 1oz Copper"))
    assert result.fields["weight_ozt"] == Decimal("1.000000")
    assert result.fields["storage_quantity"] == 20


def test_denominations_without_a_weight_have_none(
    profile: CollectionV1Profile,
) -> None:
    for denom in ("0.25", "Mint Set", "$1 Bill", "Silver Eagle"):
        assert weight_of(profile, denom) is None


def test_two_weights_in_one_denomination_go_to_review(
    profile: CollectionV1Profile,
) -> None:
    result = profile.inspect(make_row(Denom="Lot: 1oz Silver + 5oz Copper"))
    assert result.fields.get("weight_ozt") is None
    assert any(i.rule == "weight-ambiguous" for i in result.issues)


def test_original_weight_text_is_preserved(profile: CollectionV1Profile) -> None:
    """weight_raw exists because 'oz' may mean troy or avoirdupois."""
    fields = profile.inspect(make_row(Denom="Copper 1/2 Kilo")).fields
    assert fields["weight_raw"] == "1/2Kilo"
    assert fields["weight_unit_raw"] == "kilo"


# ---------------------------------------------------------------------------
# Condition decomposition
#
# "PR69DCAM PCGS" is four facts, not one string. Storing it whole makes
# "every MS65-and-better Morgan" unanswerable.
# ---------------------------------------------------------------------------


def test_a_condition_string_is_split_into_its_separate_facts() -> None:
    parsed = parse_condition("#14 PR69DCAM PCGS")
    assert parsed.grade == "PR69"
    assert parsed.designation == "DCAM"
    assert parsed.service == "PCGS"
    assert parsed.catalog_number == "14"


def test_a_grade_is_found_inside_surrounding_prose() -> None:
    # Nearly half the collection writes the grade alongside a description.
    assert parse_condition("MS70 American Bald Eagle").grade == "MS70"
    assert parse_condition("#17 Clad Roosevelt Gem Proof").grade == "GEM_PROOF"


def test_out_of_range_numbers_are_not_grades() -> None:
    # The Sheldon scale runs 1-70 with a valid band per prefix. Without that
    # check, prose yields "F73" and "G63" and they become permanent rows in a
    # vocabulary meant to be shared with other installations.
    for text in ("F73", "G63", "AU8", "PR10", "P70"):
        assert parse_condition(text).grade is None, text


def test_separator_variants_reach_the_same_seeded_grade() -> None:
    # GEM/BU, GEM BU and GEM_BU are one grade written three ways. Treating
    # them as three would make every query know about all three.
    for text in ("GEM/BU", "GEM BU", "Gem-BU"):
        assert parse_condition(text).grade == "GEM_BU", text


def test_a_plus_is_a_real_distinction_and_survives() -> None:
    # The owner said so explicitly: UNC+ is not UNC.
    assert parse_condition("UNC+").grade == "UNC+"
    assert parse_condition("MS64+").grade == "MS64+"
    assert parse_condition("BU++").grade == "BU++"


def test_equivalent_grade_spellings_normalise() -> None:
    # PF and PR are the same thing; so are EF and XF.
    assert parse_condition("PF70").grade == "PR70"
    assert parse_condition("EF40").grade == "XF40"
    assert parse_condition("MS-64").grade == "MS64"


def test_note_features_are_not_grades() -> None:
    # Star Note describes the note, not its condition. Putting it in the
    # grade column is what makes condition unqueryable.
    parsed = parse_condition("Red Seal Star Note")
    assert parsed.grade is None
    assert parsed.seal_color == "red"
    assert parsed.note_attributes == ("star",)


def test_a_designation_is_found_when_no_space_precedes_it() -> None:
    # \b never fires between "9" and "D", so a plain word boundary misses it.
    assert parse_condition("PR69DCAM").designation == "DCAM"
    # ...but CAM must still not be found inside an ordinary word.
    assert parse_condition("Scam artist").designation is None


def test_a_description_that_is_not_a_grade_is_declined() -> None:
    # Declining is the safe direction: the text is kept verbatim in grade_raw
    # and flagged, rather than inventing a grade called "ACADIANP".
    for text in ("5-Coin Mint Set", "Doubling (check P mark)", "Acadian Provinces"):
        assert parse_condition(text).grade is None, text


def test_the_item_link_is_read_and_kept_apart_from_the_seller_page() -> None:
    """Two URLs per row, and only one carries the transaction id.

    `Vendor` is the seller's own page -- ebay.com/usr/sylvinac -- and says who
    sold it. `Link` is the item -- ebay.com/itm/315248803796 -- and is the only
    place the venue's transaction id appears. The profile read the first and
    ignored the second, so `identify()` was handed a seller page, matched
    nothing, and 2,557 rows whose purchase was perfectly recoverable got no
    order at all.
    """
    profile = CollectionV1Profile()
    fields = profile.inspect(
        make_row(
            Vendor="https://www.ebay.com/usr/sylvinac",
            Link="https://www.ebay.com/itm/315248803796",
        )
    ).fields

    assert fields["vendor_url"] == "https://www.ebay.com/usr/sylvinac"
    assert fields["vendor_name"] == "ebay.com"
    assert fields["listing_url"] == "https://www.ebay.com/itm/315248803796"


def test_a_lot_id_is_recoverable_from_the_link_the_profile_emits() -> None:
    """The end of the chain: what the profile emits must satisfy identify().

    Testing the two halves separately is what let them disagree -- identify()
    had tests, the profile had tests, and nothing checked that the profile fed
    identify() the field it reads.
    """
    profile = CollectionV1Profile()
    fields = profile.inspect(
        make_row(
            Vendor="https://hibid.com/",
            Link="https://hibid.com/lot/226778844/1886-morgan-silver-dollar",
        )
    ).fields

    assert identify(fields["listing_url"]) == ("226778844", True)
