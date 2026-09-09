"""Derive note designations from the serial number itself.

PMG and PCGS give special pedigree designations to particular serial numbers --
radars, repeaters, binaries, solids, ladders, low serials, and star
(replacement) notes -- and a grading submission asks the sender to declare
them. They carry real premiums, so a note whose designation is unrecorded is a
note sold too cheaply.

Every one of these is a property of the serial, so it is derived rather than
typed. `star` is the exception in mechanism only: it comes from the asterisk
that is part of the serial rather than from the digits.

**The eight-digit rule matters more than it looks.** A US small-size serial has
exactly eight digits, and 954 of this collection's 1,015 are that long. The
rest are incomplete transcriptions, and reading patterns out of them produces
confident nonsense: "59" has two distinct digits, so a naive binary test calls
it a binary note. It is not a note at all, it is a truncated field. Pattern
designations therefore require a full-length serial, and a short one is
reported as incomplete instead.

`consecutive` is deliberately never derived. It describes a *run* of notes --
three sequential serials bought together -- which no single serial can show.

    python -m app.serial_patterns            report
    python -m app.serial_patterns --commit   apply the designations
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import (
    CurrencyDetail,
    InventoryItem,
    ItemNoteAttribute,
    NoteAttribute,
)

#: US small-size serials carry exactly this many digits. Patterns are only
#: meaningful against a complete one.
SERIAL_DIGITS = 8

#: Small-size notes begin here. Before 1928 US currency was large-size and
#: obsolete/broken-bank issues earlier still, and neither follows the
#: letters-at-the-ends convention -- an 1852 note may legitimately carry a
#: serial that a modern shape check would reject.
SMALL_SIZE_FROM = 1928

#: A low serial is one padded with at least this many leading zeros, and a
#: high serial is one whose first digit is this. Both are the owner's
#: definitions, which are positional -- an earlier version compared the
#: numeric value against a ceiling, which is a different question and got
#: 00000494 wrong.
LOW_SERIAL_ZEROS = 4
HIGH_SERIAL_FIRST_DIGIT = "9"


def digits_of(serial: str) -> str:
    """The numeric part, with the prefix/suffix letters and star removed."""
    return re.sub(r"\D", "", serial or "")


def is_incomplete(serial: str) -> bool:
    """True when the serial is too short to have been fully transcribed."""
    return len(digits_of(serial)) < SERIAL_DIGITS


def analyse(serial: str) -> set[str]:
    """Every designation this serial earns, as `note_attribute` codes."""
    found: set[str] = set()
    if not serial:
        return found

    # The star is part of the serial, not of its digits, and is positional:
    # a replacement note carries it at the start or the end, never inside.
    if "*" in serial:
        found.add("star")

    d = digits_of(serial)
    if len(d) != SERIAL_DIGITS:
        return found  # incomplete: say nothing rather than something wrong

    # Both are positional: where the serial sits in the print run, not what
    # its digits spell. They stack with the pattern designations rather than
    # replacing them -- a note can be low *and* a double quad, and 00003333 is.
    if d.startswith("0" * LOW_SERIAL_ZEROS):
        found.add("low_serial")
    if d.startswith(HIGH_SERIAL_FIRST_DIGIT):
        found.add("high_serial")

    distinct = len(set(d))
    if distinct == 1:
        found.add("solid_serial")
    elif distinct == 2:
        found.add("binary")
    elif distinct == 3:
        found.add("trinary")
    if d == d[::-1]:
        found.add("radar")
    if d[: SERIAL_DIGITS // 2] == d[SERIAL_DIGITS // 2 :]:
        found.add("repeater")
    # Four of one digit then four of another -- 00005555. Named separately
    # from `binary` because collectors and grading forms treat the arrangement
    # as the point, not merely the count of distinct digits.
    half = SERIAL_DIGITS // 2
    if len(set(d[:half])) == 1 and len(set(d[half:])) == 1 and d[0] != d[half]:
        found.add("double_quad")

    ascending = "".join(str((int(d[0]) + i) % 10) for i in range(SERIAL_DIGITS))
    descending = "".join(str((int(d[0]) - i) % 10) for i in range(SERIAL_DIGITS))
    if d in (ascending, descending):
        found.add("ladder")

    month, day, year = d[:2], d[2:4], d[4:]
    if 1 <= int(month) <= 12 and 1 <= int(day) <= 31 and 1850 <= int(year) <= 2030:
        found.add("birthday")

    # `fancy_serial` is the umbrella over *digit patterns* only. A star note is
    # a replacement note, which is a different question on a grading form and
    # a different suffix on the catalogue number -- sweeping it in here would
    # have labelled all 189 stars as fancy serials.
    if found - {"star", "low_serial", "high_serial"}:
        found.add("fancy_serial")
    return found


#: A small-size US serial: one or two prefix letters, eight digits, one suffix
#: letter, with a star replacing either letter on a replacement note.
WELL_FORMED = re.compile(r"^(?:\*|[A-Z]{1,2})\d{8}[*A-Z]$")

#: A letter with digits on both sides. Structurally impossible on a real
#: note, so this is the one thing data entry refuses outright.
INTERNAL_LETTER = re.compile(r"\d[A-Z]\d")

#: The highest an eight-digit serial can be. Structural, not a print run.
MAX_SERIAL = 99_999_999

#: Advisory only. Some print runs ended at 96,000,000 rather than 99,999,999,
#: so a serial above this is worth a second look -- but the real limit varies
#: by series, denomination and era, and this project has no sourced table of
#: them. The BEP publishes production figures and they are US government work,
#: so such a table could be built; until it is, this is a prompt to check
#: rather than a statement that the note cannot exist.
ADVISORY_CEILING = 96_000_000


@dataclass(frozen=True)
class SerialIssue:
    """Something wrong with a serial, and how wrong.

    `error` means the serial cannot be what was typed, so data entry refuses
    it. `warning` means it is unusual and worth a second look, and entry
    proceeds if the person insists -- a collection holds genuine oddities, and
    a system that would not let its owner record what is in their hand records
    fiction instead.
    """

    severity: str
    message: str


def check(
    serial: str, series_year: int | None = None, country: str | None = "US"
) -> list[SerialIssue]:
    """Everything questionable about a serial, worst first.

    Shape rules apply to **US** notes only. A US small-size serial is letters
    at the ends and eight digits between, but world notes are not: many
    formats interleave letters and digits legitimately, so applying this to a
    Bank of Canada or Bundesbank note would refuse a correct entry. Passing a
    country other than "US" checks nothing structural.
    """
    issues: list[SerialIssue] = []
    if not serial or not serial.strip():
        return issues
    if country is not None and country.upper() != "US":
        return issues
    if series_year is not None and series_year < SMALL_SIZE_FROM:
        # Large-size and obsolete issues predate the convention entirely.
        return issues
    cleaned = serial.strip().upper()

    # An internal letter is a transposition, not a variant. Letters belong at
    # the ends -- one or two in front, one behind -- and a serial with one in
    # the middle is a typo every time: S97535476A entered as S9753547A6.
    if INTERNAL_LETTER.search(cleaned):
        issues.append(
            SerialIssue(
                "warning",
                "a letter appears among the digits. On a small-size US note "
                "that is usually the suffix letter typed one position early "
                "-- S9753547A6 for S97535476A -- so check it against the "
                "note. Save anyway if that is what is printed.",
            )
        )
    elif not WELL_FORMED.match(cleaned):
        digits = digits_of(cleaned)
        if len(digits) < SERIAL_DIGITS:
            issues.append(
                SerialIssue(
                    "warning",
                    f"only {len(digits)} digits; a US small-size serial has "
                    f"{SERIAL_DIGITS}, so one may have been dropped.",
                )
            )
        else:
            issues.append(
                SerialIssue(
                    "warning",
                    "does not match the usual shape of one or two prefix "
                    "letters, eight digits and a suffix letter.",
                )
            )

    digits = digits_of(cleaned)
    if len(digits) == SERIAL_DIGITS:
        value = int(digits)
        if value > MAX_SERIAL:
            issues.append(
                SerialIssue(
                    "error",
                    f"above {MAX_SERIAL:,}, which no eight-digit serial reaches.",
                )
            )
        elif value > ADVISORY_CEILING:
            year = f" for series {series_year}" if series_year else ""
            issues.append(
                SerialIssue(
                    "warning",
                    f"above {ADVISORY_CEILING:,}{year}; some print runs ended "
                    "there rather than at 99,999,999, so this is worth "
                    "confirming against the note.",
                )
            )

    issues.sort(key=lambda i: 0 if i.severity == "error" else 1)
    return issues


def run(db: Session, *, commit: bool) -> tuple[Counter, list[tuple[str, str]]]:
    """Report, and optionally record, the designations every serial earns."""
    attribute_ids = {
        code: ident
        for ident, code in db.execute(select(NoteAttribute.id, NoteAttribute.code))
    }
    existing: set[tuple[int, int]] = set(
        db.execute(
            select(
                ItemNoteAttribute.inventory_item_id,
                ItemNoteAttribute.note_attribute_id,
            )
        ).all()
    )

    rows = db.execute(
        select(InventoryItem.id, InventoryItem.item_code, CurrencyDetail.serial_number)
        .join(CurrencyDetail, CurrencyDetail.inventory_item_id == InventoryItem.id)
        .where(InventoryItem.split_at.is_(None))
    ).all()

    stats: Counter = Counter()
    incomplete: list[tuple[str, str]] = []
    to_add: list[ItemNoteAttribute] = []

    for item_id, item_code, serial in rows:
        if not serial:
            continue
        if is_incomplete(serial):
            stats["incomplete_serial"] += 1
            incomplete.append((item_code, serial))
        for code in analyse(serial):
            attribute_id = attribute_ids.get(code)
            if attribute_id is None:
                stats[f"unknown_attribute:{code}"] += 1
                continue
            if (item_id, attribute_id) in existing:
                stats[f"already:{code}"] += 1
                continue
            stats[f"missing:{code}"] += 1
            to_add.append(
                ItemNoteAttribute(
                    inventory_item_id=item_id, note_attribute_id=attribute_id
                )
            )

    if commit and to_add:
        db.add_all(to_add)
        db.commit()
        stats["written"] = len(to_add)

    return stats, incomplete


def main(argv: list[str] | None = None) -> int:
    """Report or apply the derived designations."""
    parser = argparse.ArgumentParser(prog="serial_patterns", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="record them")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        stats, incomplete = run(db, commit=args.commit)

    missing = {
        k[len("missing:") :]: v for k, v in stats.items() if k.startswith("missing:")
    }
    if missing:
        print("designations the data does not carry:")
        for code, count in sorted(missing.items(), key=lambda kv: -kv[1]):
            print(f"  {code:<16}{count:>5}")
    if stats.get("incomplete_serial"):
        print(
            f"\nincomplete serials (fewer than {SERIAL_DIGITS} digits): "
            f"{stats['incomplete_serial']}"
        )
        for code, serial in incomplete[:8]:
            print(f"  {code}  {serial!r}")
    if stats.get("written"):
        print(f"\nrecorded {stats['written']} designations")
    elif not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
