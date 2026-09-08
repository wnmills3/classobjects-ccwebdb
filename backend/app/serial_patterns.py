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
