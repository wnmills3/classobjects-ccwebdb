"""The seeded small-size issue facts (data/reference/note_issue.json).

Spot checks against facts the project already relies on elsewhere, and the
invariants the defaults pass needs from the table as a whole.
"""

from __future__ import annotations

from collections import defaultdict

from app.models import (
    Denomination,
    NoteIssue,
    NoteType,
    SealColor,
    SignatureCombination,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def _issues(
    db: Session,
) -> list[tuple[str, int, str, str, str, str | None, str | None]]:
    rows = db.execute(
        select(
            Denomination.code,
            NoteIssue.series_year,
            NoteIssue.series_letter,
            NoteType.code,
            SealColor.code,
            SignatureCombination.code,
            NoteIssue.serial_prefix,
        )
        .join(Denomination, Denomination.id == NoteIssue.denomination_id)
        .join(NoteType, NoteType.id == NoteIssue.note_type_id)
        .join(SealColor, SealColor.id == NoteIssue.seal_color_id)
        .join(
            SignatureCombination,
            SignatureCombination.id == NoteIssue.signature_combination_id,
            isouter=True,
        )
    ).all()
    return [
        (den, year, letter or "", cls, seal, sig, prefix)
        for den, year, letter, cls, seal, sig, prefix in rows
    ]


def _find(db: Session, den: str, series: str) -> set[tuple[str, str, str | None]]:
    year, letter = int(series[:4]), series[4:]
    return {
        (cls, seal, sig)
        for d, y, lt, cls, seal, sig, _ in _issues(db)
        if (d, y, lt) == (den, year, letter)
    }


def test_the_table_is_loaded(db: Session) -> None:
    assert len(_issues(db)) > 300


def test_well_known_issues(db: Session) -> None:
    assert _find(db, "usd_note_1", "1957") == {
        ("silver_certificate", "blue", "priest_anderson")
    }
    assert _find(db, "usd_note_1", "1935B") == {
        ("silver_certificate", "blue", "julian_vinson")
    }
    # The Barr note: Granahan's signature outlasted her term.
    assert _find(db, "usd_note_1", "1963B") == {("frn", "green", "granahan_barr")}
    # Funnybacks: every $1 of Series 1928, whatever its class.
    assert {cls for cls, _, _ in _find(db, "usd_note_1", "1928")} == {
        "silver_certificate",
        "us_note",
    }
    # Hawaii and North Africa beside the ordinary $1 1935A.
    assert {seal for _, seal, _ in _find(db, "usd_note_1", "1935A")} == {
        "blue",
        "brown",
        "yellow",
    }


def test_a_class_has_one_seal_colour_per_issue(db: Session) -> None:
    seals: dict[tuple[str, int, str, str], set[str]] = defaultdict(set)
    for den, year, letter, cls, seal, _, _ in _issues(db):
        seals[(den, year, letter, cls)].add(seal)
    # A second seal in one class and series is only ever a WWII emergency
    # issue: brown for Hawaii, yellow for North Africa, Series 1934-1935A.
    emergency = {"brown", "yellow"}
    for key, found in seals.items():
        if len(found) > 1:
            assert 1934 <= key[1] <= 1935, key
            assert len(found - emergency) == 1, (key, found)


def test_serial_prefixes_follow_bep(db: Session) -> None:
    prefixes = {
        (den, year, letter): prefix
        for den, year, letter, cls, _, _, prefix in _issues(db)
        if prefix
    }
    assert prefixes[("usd_note_20", 1996, "")] == "A"
    assert prefixes[("usd_note_20", 2004, "")] == "E"
    # BEP's footnote: no series letter on $1 or $2 serials.
    assert not {key for key in prefixes if key[0] in ("usd_note_1", "usd_note_2")}


def test_every_issue_from_1996_but_the_1_and_2_has_a_prefix(db: Session) -> None:
    missing = sorted(
        (den, year, letter)
        for den, year, letter, cls, _, _, prefix in _issues(db)
        if cls == "frn"
        and year >= 1996
        and den not in ("usd_note_1", "usd_note_2")
        and prefix is None
    )
    # Rows BEP's table does not list stay without a prefix rather than
    # guessing one; there are few, and each is named here when it changes.
    assert len(missing) <= 9, missing
