"""Re-kind the banknotes that were imported as coins, and two sets likewise.

The workbook's denomination column held a bare "1" or "2" on these rows where
it usually says "$1 Bill", so the importer read each as a coin: a $1 or $2
coin, or no denomination at all where no $2 coin was known. Everything that
follows from "coin" followed. 1935 and 1957 Silver Certificates were matched
to the Peace dollar and given its silver -- 24 ozt fine across the collection
that it does not hold; a series letter ("1935-D") was read as a mint mark;
and the note's serial number, which the `Grading#` column holds for a
banknote, was filed as a grading-certificate number, because for a coin that
is what the column holds.

**How they are found.** That last mistake is also the reliable sign. A
grading certificate is digits (PCGS, NGC) or an assay reference; a US
banknote serial is a letter or two, eight digits and a letter or star --
`F06566560R`, `*01935354A`. So an item recorded as a coin that holds a
certificate of that shape is a banknote. The owner gave the hint
(2026-09-23): "currency typically has the 8 character serial number from the
old Grading# column". Only kind `coin` is searched: three 1995 notes recorded
as a set (CC-005781..783) are left for the owner rather than guessed at.

`NAMED_NOTES` adds the notes the shape cannot find -- those with no serial
recorded, or one of an older format -- each read before being listed.

For each banknote:

    kind              currency, with the note detail row in place of the coin's
    denomination      the note of the same face value: a $1 coin -> a $1 note;
                      with none recorded, the face value as typed ("2")
    coin-only fields  series, metal, composition, fineness and weights cleared;
                      the misread mint goes with the coin row
    series year/letter read from the year as typed: "1935-D" -> 1935, D
    serial number     moved from the certificate row it was filed as
    seal colour       only where the rating says it: "Red Seal", "Blue Seal"

then `classifier_defaults.refresh_items`, which fills what the note's facts
decide (its class, for one), exactly as saving the item in the editor would.
Every change is written to the change log under the person named by `--by`.

    python -m app.kind_repair                           report, touching nothing
    python -m app.kind_repair --commit --by EMAIL       apply
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import field_changes
from .classifier_defaults import refresh_items
from .database import SessionLocal
from .field_sources import RATING, forget, record_derived
from .item_kinds import match_detail_to_kind
from .models import (
    Denomination,
    InventoryItem,
    ItemCertification,
    ItemKind,
    Mint,
    SealColor,
    User,
)
from .models.base import ReferenceMixin
from .routers.inventory import field_values

__all__ = ["NAMED_NOTES", "SETS", "Repair", "run"]

#: A US banknote serial: a prefix letter (two on a web-press or later note),
#: eight digits -- nine where one was typed twice, which is kept as typed for
#: review -- and a suffix letter or a star. A star may replace the prefix.
SERIAL = r"^\*?[A-Z]{1,2}[0-9]{8,9}[A-Z*]?\*?$"

#: Banknotes the serial shape cannot find, each read before being listed.
NAMED_NOTES: tuple[str, ...] = (
    # $1 Silver Certificates, series 1957, no serial recorded
    "CC-002427",
    "CC-002428",
    # $2 notes, series 1953 and 1953-B, no serial recorded
    "CC-002434",
    "CC-002435",
    "CC-004541",
    # A lot of 100 consecutive $2 notes, series 2017-A, no serial recorded
    "CC-005818",
    # $2 1953 star note: the star stands in for the prefix, *01935354A
    "CC-005942",
    # 1929 notes with six-digit serials: $5 New York, $10 Hartford
    "CC-006802",
    "CC-006804",
)

#: Items that are sets, not single coins: a 1978-S proof set, and a National
#: Parks $2 note and quarter collection. Only the kind changes.
SETS: tuple[str, ...] = ("CC-000102", "CC-005817")

#: The fields the change log records, in the editor's own terms.
LOGGED: tuple[str, ...] = (
    "item_kind",
    "denomination",
    "series",
    "metal",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
    "note_type",
    "seal_color",
    "fed_district",
    "signature_combination",
    "series_year",
    "series_letter",
    "serial_number",
)

#: Columns a coin carries and a banknote cannot.
_COIN_COLUMNS: tuple[str, ...] = (
    "series_id",
    "metal_id",
    "composition_id",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
)

#: "1935-D" -> 1935, D. A letter needs the hyphen: "1957 Silver Certificate"
#: is series 1957, not 1957-S.
_SERIES = re.compile(r"^\s*(\d{4})(?:-([A-Za-z])\b)?")

#: A seal colour, as the rating column writes it.
_SEAL = re.compile(r"\b(red|blue|green|brown|gold|yellow)\s+seal\b", re.IGNORECASE)


@dataclass
class Repair:
    """What happened, or would happen, to one item."""

    item_code: str
    #: (field, before, after), in the order they were found.
    changes: list[tuple[str, Any, Any]] = field(default_factory=list)
    #: Why the item was left alone, when it was.
    skipped: str | None = None
    #: Fine metal the item carried and no longer does, in ozt.
    fine_removed: Decimal = Decimal(0)


def _code_id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    """The id of a reference code this repair cannot do without."""
    found = db.scalar(select(model.id).where(model.code == code))
    if found is None:
        raise LookupError(f"{model.__tablename__} {code!r} is not in the database")
    return found


def _code_of(
    db: Session, model: type[ReferenceMixin], row_id: int | None
) -> str | None:
    """The code of a reference row, for the report."""
    if row_id is None:
        return None
    return db.scalar(select(model.code).where(model.id == row_id))


def series_of(
    year_raw: str | None, year_start: int | None
) -> tuple[int | None, str | None]:
    """A note's series year and letter, from the year as it was typed."""
    found = _SERIES.match(year_raw or "")
    if found is None:
        return year_start, None
    letter = found.group(2)
    return int(found.group(1)), letter.upper() if letter else None


def seal_of(rating: str | None) -> str | None:
    """The seal colour a rating names, if it names exactly one."""
    colours = {m.lower() for m in _SEAL.findall(rating or "")}
    return colours.pop() if len(colours) == 1 else None


def note_denomination(face: Decimal | None, typed: str | None) -> str | None:
    """The note denomination of a face value: the coin's, else as typed."""
    if face is None:
        try:
            face = Decimal((typed or "").strip().lstrip("$"))
        except ArithmeticError:
            return None
    if face <= 0 or face != face.to_integral_value():
        return None
    return f"usd_note_{int(face)}"


def _to_note(
    db: Session, item: InventoryItem, denomination: int, repair: Repair
) -> None:
    """Make one coin-kinded item the banknote it is."""
    coin = item.coin_detail
    mint = _code_of(db, Mint, coin.mint_id) if coin is not None else None
    if mint is not None:
        repair.changes.append(("mint", mint, None))
    if item.composition_id is not None:
        # A fact row, with no code of its own: named by id in the report.
        repair.changes.append(("composition", f"#{item.composition_id}", None))
    if item.fine_weight_ozt:
        repair.fine_removed = item.fine_weight_ozt * item.piece_count

    for column in _COIN_COLUMNS:
        setattr(item, column, None)
    item.item_kind_id = _code_id(db, ItemKind, "currency")
    item.denomination_id = denomination
    match_detail_to_kind(db, item)
    note = item.currency_detail
    assert note is not None  # match_detail_to_kind has just made it

    note.series_year, note.series_letter = series_of(item.year_raw, item.year_start)

    certificates = db.scalars(
        select(ItemCertification).where(ItemCertification.inventory_item_id == item.id)
    ).all()
    if len(certificates) == 1:
        note.serial_number = certificates[0].cert_number.strip().upper()
        repair.changes.append(("certificate", certificates[0].cert_number, None))
        db.delete(certificates[0])
    elif certificates:
        repair.changes.append(
            ("certificate", f"{len(certificates)} numbers, left for review", None)
        )

    if (seal := seal_of(item.grade_raw)) is not None:
        note.seal_color_id = _code_id(db, SealColor, seal)
        record_derived(db, item.id, ["seal_color_id"], RATING)

    # What a person has now set is theirs: no pass refills the coin's values.
    forget(db, [item.id], [*_COIN_COLUMNS, "item_kind_id", "denomination_id"])


def serial_bearing(db: Session) -> list[str]:
    """Every item recorded as a coin that holds a banknote serial."""
    return list(
        db.scalars(
            select(InventoryItem.item_code)
            .distinct()
            .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
            .join(
                ItemCertification,
                ItemCertification.inventory_item_id == InventoryItem.id,
            )
            .where(
                ItemKind.code == "coin",
                InventoryItem.deleted_at.is_(None),
                ItemCertification.cert_number.op("~*")(SERIAL),
            )
        )
    )


def run(
    db: Session,
    *,
    commit: bool,
    user_id: int | None,
    named: tuple[str, ...] = NAMED_NOTES,
    sets: tuple[str, ...] = SETS,
) -> list[Repair]:
    """Repair every listed item; commit only when asked, else roll back.

    The dry run performs the whole repair, the refresh of derived values
    included, and then rolls it back -- so what it reports is what a commit
    would write, not an estimate of it. `named` and `sets` default to the
    lists above; the tests pass their own.
    """
    wanted = {*serial_bearing(db), *named, *sets}
    items = {
        item.item_code: item
        for item in db.scalars(
            select(InventoryItem).where(InventoryItem.item_code.in_(wanted))
        )
    }
    set_id = _code_id(db, ItemKind, "set")
    currency_id = _code_id(db, ItemKind, "currency")
    repairs: list[Repair] = []
    touched: list[tuple[InventoryItem, Repair, dict[str, Any]]] = []

    for code in sorted(wanted):
        repair = Repair(code)
        repairs.append(repair)
        item = items.get(code)
        target = set_id if code in sets else currency_id
        if item is None or item.deleted_at is not None:
            repair.skipped = "no such item"
            continue
        if item.item_kind_id == target:
            repair.skipped = "already done"
            continue
        before = field_values(db, item)
        if code in sets:
            item.item_kind_id = set_id
            match_detail_to_kind(db, item)
            forget(db, [item.id], ["item_kind_id"])
        else:
            face = item.denomination.face_value if item.denomination else None
            code_of_note = note_denomination(face, item.denom_raw)
            note_id = (
                db.scalar(
                    select(Denomination.id).where(Denomination.code == code_of_note)
                )
                if code_of_note
                else None
            )
            if note_id is None:
                repair.skipped = f"no note denomination for {item.denom_raw!r}"
                continue
            _to_note(db, item, note_id, repair)
        touched.append((item, repair, before))

    refresh_items(db, [item.id for item, _, _ in touched])
    for item, repair, before in touched:
        after = field_values(db, item)
        repair.changes[:0] = [
            (name, before.get(name), after.get(name))
            for name in LOGGED
            if not field_changes.same_value(before.get(name), after.get(name))
        ]
        field_changes.record(db, item.id, before, after, LOGGED, user_id=user_id)

    if commit:
        db.commit()
    else:
        db.rollback()
    return repairs


def _print(repairs: list[Repair], *, commit: bool) -> None:
    done = [r for r in repairs if r.skipped is None]
    for repair in repairs:
        if repair.skipped:
            print(f"{repair.item_code}  skipped: {repair.skipped}")
            continue
        print(repair.item_code)
        for name, old, new in repair.changes:
            print(f"    {name:<22} {old!s:<24} -> {new}")
    fine = sum((r.fine_removed for r in done), Decimal(0))
    print(f"\n{len(done)} items {'repaired' if commit else 'to repair'}", end="")
    print(f", {len(repairs) - len(done)} skipped; {fine} ozt fine silver removed")
    if not commit:
        print("\n(dry run -- nothing written; pass --commit --by EMAIL)")


def main(argv: list[str] | None = None) -> None:
    """Report or apply the repair."""
    parser = argparse.ArgumentParser(prog="kind_repair", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="apply the repair")
    parser.add_argument("--by", help="email of the person the change log names")
    args = parser.parse_args(argv)
    if args.commit and not args.by:
        parser.error("--commit needs --by EMAIL: the change log names who made it")

    with SessionLocal() as db:
        user_id = None
        if args.by:
            user_id = db.scalar(select(User.id).where(User.email == args.by))
            if user_id is None:
                parser.error(f"no user with email {args.by!r}")
        repairs = run(db, commit=args.commit, user_id=user_id)
    _print(repairs, commit=args.commit)


if __name__ == "__main__":
    main()
