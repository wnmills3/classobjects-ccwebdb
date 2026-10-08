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
exactly eight digits. A shorter one is an incomplete transcription, and
reading patterns out of it produces confident nonsense: "59" has two distinct
digits, so a naive binary test calls it a binary note. It is not a note at
all, it is a truncated field. Pattern designations therefore require a
full-length serial, and a short one is reported as incomplete instead.

The pass takes back what it derived and the serial no longer earns: a serial
corrected from a radar to an ordinary number loses the radar this module gave
it. A designation a person set is theirs, and one a person removed stays
removed. Nothing here runs on a save; the pass is run by hand.

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
    ItemAttribute,
    ItemAttributeLink,
    ProvenanceSource,
)

#: US small-size serials carry exactly this many digits. Patterns are only
#: meaningful against a complete one.
SERIAL_DIGITS = 8

#: `item_attribute_link.derived_by` for the links this module adds.
RULE = "serial_pattern"

#: Small-size notes begin here. Before 1928 US currency was large-size and
#: obsolete/broken-bank issues earlier still, and neither follows the
#: letters-at-the-ends convention -- an 1852 note may legitimately carry a
#: serial that a modern shape check would reject.
SMALL_SIZE_FROM = 1928

#: A low serial is one padded with at least this many leading zeros, and a
#: high serial is one whose first digit is this. Both are the owner's
#: definitions, which are positional: comparing the numeric value against a
#: ceiling is a different question.
LOW_SERIAL_ZEROS = 4
HIGH_SERIAL_FIRST_DIGIT = "9"


def digits_of(serial: str) -> str:
    """The numeric part, with the prefix/suffix letters and star removed."""
    return re.sub(r"\D", "", serial or "")


def is_incomplete(serial: str) -> bool:
    """True when the serial is too short to have been fully transcribed."""
    return len(digits_of(serial)) < SERIAL_DIGITS


def _positional_designations(d: str) -> set[str]:
    """Designations earned by where the serial sits in the print run.

    Positional rather than about what the digits spell, so these stack with
    the pattern designations rather than replacing them -- a note can be low
    *and* a double quad, and 00003333 is.
    """
    found: set[str] = set()
    if d.startswith("0" * LOW_SERIAL_ZEROS):
        found.add("low_serial")
    if d.startswith(HIGH_SERIAL_FIRST_DIGIT):
        found.add("high_serial")
    return found


def _pattern_designations(d: str) -> set[str]:
    """Designations earned by what the digits spell."""
    found: set[str] = set()

    distinct = len(set(d))
    if distinct == 1:
        found.add("solid_serial")
    elif distinct == 2:
        found.add("binary")
    elif distinct == 3:
        found.add("trinary")

    if d == d[::-1]:
        found.add("radar")
    if d.startswith(d[SERIAL_DIGITS // 2 :]):
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

    return found


def analyse(serial: str) -> set[str]:
    """Every designation this serial earns, as `item_attribute` codes."""
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

    found |= _positional_designations(d)
    found |= _pattern_designations(d)

    # `fancy_serial` is the umbrella over *digit patterns* only. A star note is
    # a replacement note, which is a different question on a grading form and
    # a different suffix on the catalog number: a star alone says nothing of
    # the digits, so it does not make a serial fancy.
    if found - {"star", "low_serial", "high_serial"}:
        found.add("fancy_serial")
    return found


#: A small-size US serial: one or two prefix letters, eight digits, one suffix
#: letter, with a star replacing either letter on a replacement note.
WELL_FORMED = re.compile(r"^(?:\*|[A-Z]{1,2})\d{8}[*A-Z]$")

#: A letter with digits on both sides. Not the shape of a small-size US
#: serial, so `check` warns: it is usually the suffix letter typed one
#: position early.
INTERNAL_LETTER = re.compile(r"\d[A-Z]\d")

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

    `error` means the serial cannot be what was typed; no rule here finds
    one, since eight digits cannot exceed 99,999,999. `warning` means it is
    unusual and worth a second look -- a collection holds genuine oddities,
    and a system that would not let its owner record what is in their hand
    records fiction instead. What `check` returns; nothing acts on either,
    since no save calls `check`.
    """

    severity: str
    message: str


def _shape_issues(cleaned: str) -> list[SerialIssue]:
    """What the arrangement of letters and digits says about the serial."""
    # An internal letter is usually a transposition. Letters belong at the
    # ends -- one or two in front, one behind -- so a serial with one in the
    # middle is warned about: S97535476A entered as S9753547A6.
    if INTERNAL_LETTER.search(cleaned):
        return [
            SerialIssue(
                "warning",
                "a letter appears among the digits. On a small-size US note "
                "that is usually the suffix letter typed one position early "
                "-- S9753547A6 for S97535476A -- so check it against the "
                "note. Save anyway if that is what is printed.",
            )
        ]
    if WELL_FORMED.match(cleaned):
        return []

    digits = digits_of(cleaned)
    if len(digits) < SERIAL_DIGITS:
        return [
            SerialIssue(
                "warning",
                f"only {len(digits)} digits; a US small-size serial has "
                f"{SERIAL_DIGITS}, so one may have been dropped.",
            )
        ]
    return [
        SerialIssue(
            "warning",
            "does not match the usual shape of one or two prefix "
            "letters, eight digits and a suffix letter.",
        )
    ]


def _range_issues(digits: str, series_year: int | None) -> list[SerialIssue]:
    """What the serial's numeric value says about it."""
    if len(digits) != SERIAL_DIGITS:
        return []
    value = int(digits)
    if value > ADVISORY_CEILING:
        year = f" for series {series_year}" if series_year else ""
        return [
            SerialIssue(
                "warning",
                f"above {ADVISORY_CEILING:,}{year}; some print runs ended "
                "there rather than at 99,999,999, so this is worth "
                "confirming against the note.",
            )
        ]
    return []


def check(
    serial: str, series_year: int | None = None, country: str | None = "US"
) -> list[SerialIssue]:
    """Everything questionable about a serial, worst first.

    Called by nothing in the application: data entry neither refuses nor
    warns on a serial, and the inventory search's `malformed_serial` check
    (`app.issues`) is what lists an internal letter. `_shape_issues`,
    `_range_issues`, `SerialIssue`, `INTERNAL_LETTER` and `ADVISORY_CEILING`
    serve only this function.

    Shape rules apply to **US** notes only. A US small-size serial is letters
    at the ends and eight digits between, but world notes are not: many
    formats interleave letters and digits legitimately, so applying this to a
    Bank of Canada or Bundesbank note would refuse a correct entry. Passing a
    country other than "US" checks nothing structural.
    """
    if not serial or not serial.strip():
        return []
    if country is not None and country.upper() != "US":
        return []
    if series_year is not None and series_year < SMALL_SIZE_FROM:
        # Large-size and obsolete issues predate the convention entirely.
        return []

    cleaned = serial.strip().upper()
    issues = _shape_issues(cleaned) + _range_issues(digits_of(cleaned), series_year)
    issues.sort(key=lambda i: 0 if i.severity == "error" else 1)
    return issues


def run(db: Session, *, commit: bool) -> tuple[Counter, list[tuple[str, str]]]:
    """Report, and optionally record, the designations every serial earns.

    A designation this module added, still in force, that the serial no
    longer earns -- the serial was corrected -- is taken back with it. One a
    person set, or removed, is never touched.
    """
    attribute_ids = {
        code: ident
        for ident, code in db.execute(select(ItemAttribute.id, ItemAttribute.code))
    }
    codes = {ident: code for code, ident in attribute_ids.items()}
    # The links in force that this module added, by item: the only ones it
    # may take back.
    mine: dict[int, set[int]] = {}
    for item_id, derived_id in db.execute(
        select(
            ItemAttributeLink.inventory_item_id,
            ItemAttributeLink.item_attribute_id,
        ).where(
            ItemAttributeLink.derived_by == RULE,
            ItemAttributeLink.removed_at.is_(None),
        )
    ).tuples():
        mine.setdefault(item_id, set()).add(derived_id)
    # Every link, removed ones included: a designation a person took away
    # is not put back.
    existing: set[tuple[int, int]] = set(
        db.execute(
            select(
                ItemAttributeLink.inventory_item_id,
                ItemAttributeLink.item_attribute_id,
            )
        )
        .tuples()
        .all()
    )

    rows = db.execute(
        select(InventoryItem.id, InventoryItem.item_code, CurrencyDetail.serial_number)
        .join(CurrencyDetail, CurrencyDetail.inventory_item_id == InventoryItem.id)
        .where(InventoryItem.split_at.is_(None))
    ).all()

    stats: Counter = Counter()
    incomplete: list[tuple[str, str]] = []
    to_add: list[ItemAttributeLink] = []
    to_remove: list[tuple[int, int]] = []

    for item_id, item_code, serial in rows:
        earned = analyse(serial) if serial else set()
        earned_ids = {attribute_ids[c] for c in earned if c in attribute_ids}
        for stale_id in sorted(mine.get(item_id, set()) - earned_ids):
            stats[f"stale:{codes[stale_id]}"] += 1
            to_remove.append((item_id, stale_id))
        if not serial:
            continue
        if is_incomplete(serial):
            stats["incomplete_serial"] += 1
            incomplete.append((item_code, serial))
        to_add += _missing_links(item_id, earned, attribute_ids, existing, stats)

    if commit and (to_add or to_remove):
        _write(db, to_add, to_remove, stats)

    return stats, incomplete


def _missing_links(
    item_id: int,
    earned: set[str],
    attribute_ids: dict[str, int],
    existing: set[tuple[int, int]],
    stats: Counter,
) -> list[ItemAttributeLink]:
    """The links to add for the designations an item earns and lacks.

    Each designation is counted in `stats`: unknown to the vocabulary,
    already linked (a removed link included), or missing.
    """
    links: list[ItemAttributeLink] = []
    for code in earned:
        attribute_id = attribute_ids.get(code)
        if attribute_id is None:
            stats[f"unknown_attribute:{code}"] += 1
            continue
        if (item_id, attribute_id) in existing:
            stats[f"already:{code}"] += 1
            continue
        stats[f"missing:{code}"] += 1
        links.append(
            ItemAttributeLink(
                inventory_item_id=item_id,
                item_attribute_id=attribute_id,
                source=ProvenanceSource.derived,
                derived_by=RULE,
            )
        )
    return links


def _write(
    db: Session,
    to_add: list[ItemAttributeLink],
    to_remove: list[tuple[int, int]],
    stats: Counter,
) -> None:
    """Delete the stale links, add the missing ones, commit, and count both."""
    for key in to_remove:
        stale = db.get(ItemAttributeLink, key)
        if stale is not None:
            db.delete(stale)
    db.add_all(to_add)
    db.commit()
    if to_add:
        stats["written"] = len(to_add)
    if to_remove:
        stats["taken_back"] = len(to_remove)


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
    stale = {k[len("stale:") :]: v for k, v in stats.items() if k.startswith("stale:")}
    if stale:
        print("\ndesignations derived here that the serial no longer earns:")
        for code, count in sorted(stale.items(), key=lambda kv: -kv[1]):
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
    if stats.get("taken_back"):
        print(f"\ntook back {stats['taken_back']} designations")
    if not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
