"""Assign a design series from the facts: denomination, year and series letter.

`app.series_match` reads what a description says. Most of the collection says
nothing -- "1942-S 10C MS65" is a Mercury dime whether or not anyone wrote the
word -- and no note's description calls it a Funnyback. The facts decide those:
a design is issued at one denomination in known years, so a dime of 1942 can
only be a Winged Liberty Head.

**Candidates** are the designs for the item's inventory (coin or currency)
whose ranges cover its denomination and year, and its letter for a note.
**Evidence** is the text -- title, description and rating, read with
`series_match`'s vocabulary -- or, for a note, a seal color matching the
design's. A title and description shared with other pieces of the same order
are the lot's, not the piece's, so for those only the rating counts. Then:

    one candidate                         assign
    one, needs evidence, and has it       assign
    one, needs evidence, and lacks it     leave: an ordinary note of that series
    several, and the text names one       assign that one
    several, otherwise                    review: a boundary year
    text names only designs it cannot be  review: a conflict, nothing assigned

A hand correction or a `series_match` result always stands, and a series a
person emptied (`held`) stays empty. Writes are recorded as `derived`.

`refresh_series` runs this for one item on every save, and also takes back a
series it or an accepted entry suggestion wrote once the facts no longer
support it; `suggest_series` answers the entry forms from the facts alone.

    python -m app.series_classify            report, touching nothing
    python -m app.series_classify --commit   write the assignments

Design and decisions: docs/specs/series-classification-design.md.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, Select, exists, func, or_, select, update
from sqlalchemy.orm import QueryableAttribute, Session, aliased

from .database import SessionLocal
from .field_sources import (
    HELD,
    SERIES_CLASSIFY,
    SUGGESTION,
    forget,
    record_derived_many,
)
from .models import (
    CurrencyDetail,
    Denomination,
    InventoryItem,
    ItemFieldSource,
    ItemKind,
    Series,
    SeriesYearRange,
)
from .series_match import build_rules, inventory_of, match, record_series
from .short_titles import SHORT_TITLE
from .years import single_year

#: A range as the pass uses it: denomination, first year, last year (None is
#: still issued), and the allowed series letters (None is any).
Span = tuple[int, int, int | None, str | None]

#: Where a series came from, when a refresh may take it back: this pass's own
#: guess, or a suggestion the entry form made and the person left alone.
RETRACTABLE = (SERIES_CLASSIFY, SUGGESTION)


@dataclass(frozen=True)
class Design:
    """A series, with the facts that say which items it can be."""

    id: int
    code: str
    applies_to: str
    needs_evidence: bool
    seal_color_id: int | None
    spans: tuple[Span, ...]
    #: The note class the design belongs to, when it names one.
    note_type_id: int | None = None

    def covers(self, denomination_id: int, year: int, letter: str | None) -> bool:
        """Whether an item of this denomination, year and letter can be this."""
        for span_denomination, start, end, letters in self.spans:
            if span_denomination != denomination_id:
                continue
            if year < start or (end is not None and year > end):
                continue
            if SeriesYearRange.allows_letter(letters, letter):
                return True
        return False


@dataclass(frozen=True)
class Case:
    """One item the pass would not decide, and why."""

    item_code: str
    reason: str
    designs: tuple[str, ...]


@dataclass
class Report:
    """What the pass found: assignments, and the cases left for a person."""

    assignments: dict[int, int] = field(default_factory=dict)
    assigned: Counter = field(default_factory=Counter)
    counts: Counter = field(default_factory=Counter)
    review: list[Case] = field(default_factory=list)


def load_designs(db: Session) -> list[Design]:
    """Every design with facts to classify by.

    A design with range rows is matched on them, each falling back to the
    design's own denomination. One with none is matched on its own span. A
    design with no denomination either way -- the bullion and multi-face gold
    designs -- has no facts, and is left to be matched by name.
    """
    ranges: dict[int, list[SeriesYearRange]] = {}
    for record in db.execute(select(SeriesYearRange)).scalars():
        ranges.setdefault(record.series_id, []).append(record)

    designs = []
    for series in db.execute(select(Series).order_by(Series.sort_order)).scalars():
        if series.id in ranges:
            given: list[tuple[int | None, int, int | None, str | None]] = [
                (
                    r.denomination_id or series.denomination_id,
                    r.year_start,
                    r.year_end,
                    r.letters,
                )
                for r in ranges[series.id]
            ]
        elif series.year_start is not None:
            given = [(series.denomination_id, series.year_start, series.year_end, None)]
        else:
            given = []
        spans: tuple[Span, ...] = tuple(
            (denomination, start, end, letters)
            for denomination, start, end, letters in given
            if denomination is not None
        )
        if spans:
            designs.append(
                Design(
                    id=series.id,
                    code=series.code,
                    applies_to=series.applies_to,
                    needs_evidence=series.needs_evidence,
                    seal_color_id=series.seal_color_id,
                    spans=spans,
                    note_type_id=series.note_type_id,
                )
            )
    return designs


def candidates(
    designs: list[Design],
    inventory: str,
    denomination_id: int,
    year: int,
    letter: str | None,
) -> list[Design]:
    """The designs an item of this inventory, denomination, year and letter can be."""
    return [
        d
        for d in designs
        if d.applies_to == inventory and d.covers(denomination_id, year, letter)
    ]


def suggest_series(
    db: Session,
    inventory: str,
    denomination_id: int | None,
    year: int | None,
    letter: str | None,
    seal_color_id: int | None = None,
    note_type_id: int | None = None,
) -> str | None:
    """The design these facts alone decide, for the entry forms; None if open.

    No text is read: an entry form's title is the seller's words, which the
    batch pass weighs later.
    """
    if denomination_id is None or year is None:
        return None
    designs = load_designs(db)
    found = candidates(designs, inventory, denomination_id, year, letter)
    design, _, _ = decide(
        found,
        set(),
        {d.code for d in designs},
        seal_color_id,
        frozenset(),
        note_type_id,
    )
    return design.code if design is not None else None


def _rules_out(
    design: Design,
    inventory: str,
    denomination_id: int,
    year: int,
    letter: str | None,
    note_type_id: int | None,
) -> bool:
    """Whether the facts say an item cannot be `design`."""
    wrong_class = (
        design.note_type_id is not None
        and note_type_id is not None
        and design.note_type_id != note_type_id
    )
    return (
        wrong_class
        or design.applies_to != inventory
        or not design.covers(denomination_id, year, letter)
    )


def decide(
    candidates: list[Design],
    named: set[str],
    known: set[str],
    seal_color_id: int | None,
    lot_named: frozenset[str] = frozenset(),
    note_type_id: int | None = None,
) -> tuple[Design | None, str, tuple[str, ...]]:
    """The design an item is, or the reason it is left and the designs in play.

    `named` is every design the piece's own text names; `known` the designs
    with facts, since text naming a design without any (a bullion design)
    says nothing the facts can contradict. `lot_named` is what the lot's text
    names: never evidence for a design, but a lot of commemoratives may hold
    this piece, so an evidence-only candidate it names sends the piece to
    review rather than to the common design. `note_type_id` is the note's
    recorded class: a design of another class is not a candidate, and a
    design of this class has its evidence.
    """
    if note_type_id is not None:
        candidates = [
            d
            for d in candidates
            if d.note_type_id is None or d.note_type_id == note_type_id
        ]
    possible = {d.code for d in candidates}
    named = named & known
    if named - possible and not named & possible:
        return None, "conflict", tuple(sorted(named))

    eligible = [
        d
        for d in candidates
        if not d.needs_evidence
        or d.code in named
        or (seal_color_id is not None and d.seal_color_id == seal_color_id)
        or (note_type_id is not None and d.note_type_id == note_type_id)
    ]
    if not eligible:
        return None, "ordinary" if candidates else "no_candidate", ()
    hinted = [d for d in candidates if d.code in lot_named and d not in eligible]
    if hinted:
        return None, "boundary", tuple(sorted(d.code for d in eligible + hinted))
    if len(eligible) == 1:
        return eligible[0], "assigned", ()
    chosen = [d for d in eligible if d.code in named]
    if len(chosen) == 1:
        return chosen[0], "assigned", ()
    return None, "boundary", tuple(sorted(d.code for d in eligible))


def _with_facts(*columns: ColumnElement[Any] | QueryableAttribute[Any]) -> Select[Any]:
    """`columns` of every unsplit item, joined to its kind and currency detail.

    The shape both `_items` and `disagreements` read; each adds its own
    columns and its own condition on `series_id`.
    """
    return (
        select(*columns)
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .join(
            CurrencyDetail,
            CurrencyDetail.inventory_item_id == InventoryItem.id,
            isouter=True,
        )
        .where(InventoryItem.split_at.is_(None))
        .order_by(InventoryItem.item_code)
    )


def _design_year(
    inventory: str,
    years: tuple[int | None, int | None],
    series_year: int | None,
    series_letter: str | None,
) -> tuple[int | None, str | None]:
    """The year and letter a design is matched by.

    A note's series year and letter; a coin's single year, and no letter --
    a coin whose years are a range spans designs, so it has no year here.
    """
    if inventory == "currency":
        return series_year, series_letter
    return single_year(years), None


def _items(
    db: Session, item_ids: Collection[int] | None = None
) -> Sequence[tuple[Any, ...]]:
    """Every unclassified item, or those of `item_ids`, with what the pass reads.

    An item whose series a person emptied (`held`) is not unclassified: it
    stays empty.

    The `lot_text` column says whether the title and description are **lot text**:
    shared, word for word, with another piece of the same order. A lot's
    pieces carry the lot's listing, so "Large Cents,
    Morgans, Mercury dimes" on a 1943 cent describes the lot, not the cent,
    and is evidence neither for nor against it. The rating is always the
    piece's own.

    The listing is shared in one of two shapes. Title and description both
    the same: the pieces as they came in. Or the title alone, when it is a
    listing and not a face value (longer than `short_titles.SHORT_TITLE`):
    `app.seller_titles` moves the seller's text into the title and writes
    each piece its own description, and the lot's listing is no less the
    lot's for that.
    """
    sibling = aliased(InventoryItem)
    lot_text = exists().where(
        sibling.purchase_order_id == InventoryItem.purchase_order_id,
        sibling.id != InventoryItem.id,
        sibling.split_at.is_(None),
        sibling.source_title.is_not_distinct_from(InventoryItem.source_title),
        or_(
            sibling.description.is_not_distinct_from(InventoryItem.description),
            func.length(func.btrim(InventoryItem.source_title)) > SHORT_TITLE,
        ),
    )
    held = exists().where(
        ItemFieldSource.inventory_item_id == InventoryItem.id,
        ItemFieldSource.field_name == "series_id",
        ItemFieldSource.derived_by == HELD,
    )
    query = (
        _with_facts(
            InventoryItem.id,
            InventoryItem.item_code,
            ItemKind.code,
            InventoryItem.denomination_id,
            Denomination.code,
            InventoryItem.year_start,
            InventoryItem.year_end,
            CurrencyDetail.series_year,
            CurrencyDetail.series_letter,
            CurrencyDetail.seal_color_id,
            InventoryItem.source_title,
            InventoryItem.description,
            InventoryItem.rating,
            lot_text,
            CurrencyDetail.note_type_id,
        )
        .join(
            Denomination,
            Denomination.id == InventoryItem.denomination_id,
            isouter=True,
        )
        .where(InventoryItem.series_id.is_(None), ~held)
    )
    if item_ids is not None:
        query = query.where(InventoryItem.id.in_(item_ids))
    return db.execute(query).tuples().all()


def disagreements(db: Session, designs: list[Design]) -> list[Case]:
    """Items whose series the facts rule out. Reported, never changed.

    A series already set is never touched, but it can be wrong: a Franklin
    Pierce dollar matched as a Franklin half by its text, or a Kennedy half
    recorded as $1. Either the series or the denomination and year are wrong,
    and the report names the item so a person can say which.
    """
    by_id = {d.id: d for d in designs}
    rows = db.execute(
        _with_facts(
            InventoryItem.item_code,
            ItemKind.code,
            InventoryItem.series_id,
            InventoryItem.denomination_id,
            InventoryItem.year_start,
            InventoryItem.year_end,
            CurrencyDetail.series_year,
            CurrencyDetail.series_letter,
            CurrencyDetail.note_type_id,
        ).where(InventoryItem.series_id.is_not(None))
    ).tuples()

    cases = []
    for (
        code,
        kind,
        series_id,
        denomination_id,
        start,
        end,
        s_year,
        s_letter,
        note_type_id,
    ) in rows:
        design = None if series_id is None else by_id.get(series_id)
        if design is None or denomination_id is None:
            continue  # a design without facts, or an item without them
        inventory = inventory_of(kind)
        year, letter = _design_year(inventory, (start, end), s_year, s_letter)
        if year is None:
            continue
        if _rules_out(design, inventory, denomination_id, year, letter, note_type_id):
            cases.append(Case(code, "disagrees", (design.code,)))
    return cases


def classify(
    db: Session,
    item_ids: Collection[int] | None = None,
    designs: list[Design] | None = None,
) -> Report:
    """Decide every unclassified item, or those of `item_ids`, writing nothing.

    `designs` saves a caller that has already loaded them a second read. The
    disagreement report covers the whole collection, so it is made only for a
    run over all of it.
    """
    if designs is None:
        designs = load_designs(db)
    known = {d.code for d in designs}
    rules = build_rules(db)
    report = Report()

    for (
        item_id,
        item_code,
        kind,
        denomination_id,
        denomination,
        year_start,
        year_end,
        series_year,
        series_letter,
        seal_color_id,
        title,
        description,
        rating,
        lot_text,
        note_type_id,
    ) in _items(db, item_ids):
        inventory = inventory_of(kind)
        year, letter = _design_year(
            inventory, (year_start, year_end), series_year, series_letter
        )
        if denomination_id is None or year is None:
            report.counts["no_facts"] += 1
            continue

        found = candidates(designs, inventory, denomination_id, year, letter)
        own = (rating,) if lot_text else (title, description, rating)
        text = " ".join(part for part in own if part)
        named = match(text, denomination, rules, inventory)
        lot_named: frozenset[str] = frozenset()
        if lot_text:
            lot = " ".join(part for part in (title, description) if part)
            lot_named = frozenset(match(lot, denomination, rules, inventory))
        design, outcome, in_play = decide(
            found, named, known, seal_color_id, lot_named, note_type_id
        )

        report.counts[outcome] += 1
        if design is not None:
            report.assignments[item_id] = design.id
            report.assigned[design.code] += 1
        elif in_play:
            report.review.append(Case(item_code, outcome, in_play))

    if item_ids is None:
        report.review.extend(disagreements(db, designs))
    return report


def _stale(db: Session, designs: list[Design], item_ids: Collection[int]) -> list[int]:
    """Items whose retractable series the facts no longer support.

    Unsupported includes having no facts left: a guess with nothing under it
    is not kept.
    """
    by_id = {d.id: d for d in designs}
    rows = db.execute(
        _with_facts(
            InventoryItem.id,
            ItemKind.code,
            InventoryItem.series_id,
            InventoryItem.denomination_id,
            InventoryItem.year_start,
            InventoryItem.year_end,
            CurrencyDetail.series_year,
            CurrencyDetail.series_letter,
            CurrencyDetail.note_type_id,
        )
        .join(
            ItemFieldSource,
            (ItemFieldSource.inventory_item_id == InventoryItem.id)
            & (ItemFieldSource.field_name == "series_id"),
        )
        .where(
            InventoryItem.id.in_(item_ids),
            InventoryItem.series_id.is_not(None),
            ItemFieldSource.derived_by.in_(RETRACTABLE),
        )
    ).tuples()
    stale = []
    for (
        item_id,
        kind,
        series_id,
        denomination_id,
        start,
        end,
        s_year,
        s_letter,
        cls,
    ) in rows:
        design = by_id.get(series_id)
        inventory = inventory_of(kind)
        year, letter = _design_year(inventory, (start, end), s_year, s_letter)
        if (
            design is None
            or denomination_id is None
            or year is None
            or _rules_out(design, inventory, denomination_id, year, letter, cls)
        ):
            stale.append(item_id)
    return stale


def refresh_series(db: Session, item_ids: Collection[int]) -> None:
    """Bring these items' design series up to date with their facts, uncommitted.

    Called when an item is created or edited. A series this pass or an
    accepted suggestion wrote, which the facts now rule out, is cleared with
    its record; then each item left without one, and not held empty, is
    decided as the batch decides it. A person's series is never touched.
    """
    if not item_ids:
        return
    db.flush()
    designs = load_designs(db)
    stale = _stale(db, designs, item_ids)
    if stale:
        for item in db.execute(
            select(InventoryItem).where(InventoryItem.id.in_(stale))
        ).scalars():
            item.series_id = None
        forget(db, stale, ["series_id"])
        db.flush()
    assignments = classify(db, item_ids, designs).assignments
    # One statement per design and one upsert for the records, however many
    # items a bulk edit touched.
    by_design: dict[int, list[int]] = {}
    for item_id, series_id in assignments.items():
        by_design.setdefault(series_id, []).append(item_id)
    for series_id, ids in by_design.items():
        db.execute(
            update(InventoryItem)
            .where(InventoryItem.id.in_(ids))
            .values(series_id=series_id)
        )
    record_derived_many(db, assignments, ["series_id"], SERIES_CLASSIFY)
    db.flush()


def run(db: Session, *, commit: bool) -> Report:
    """Classify every unclassified item, writing only when asked."""
    report = classify(db)
    if commit and report.assignments:
        record_series(db, report.assignments, SERIES_CLASSIFY)
        report.counts["written"] = len(report.assignments)
    return report


def _print(report: Report, *, commit: bool) -> None:
    """The counts the owner reviews before anything is written."""
    counts = report.counts
    examined = sum(n for key, n in counts.items() if key != "written")
    print(f"unclassified items examined: {examined}")
    for key in (
        "assigned",
        "boundary",
        "conflict",
        "ordinary",
        "no_candidate",
        "no_facts",
        "written",
    ):
        if counts.get(key):
            print(f"  {key:<13} {counts[key]}")

    if report.assigned:
        print("\nwould assign, by design:" if not commit else "\nassigned, by design:")
        for code, n in report.assigned.most_common():
            print(f"  {code:<28} {n}")

    headings = {
        "conflict": "conflict: the text names a design the facts rule out",
        "boundary": "boundary: the facts allow several designs",
        "disagrees": "disagrees: a series already set that the facts rule out",
    }
    for reason, heading in headings.items():
        cases = [c for c in report.review if c.reason == reason]
        if not cases:
            continue
        print(f"\n{heading} ({len(cases)}):")
        by_designs = Counter(c.designs for c in cases)
        for designs, n in by_designs.most_common():
            codes = [c.item_code for c in cases if c.designs == designs]
            sample = ", ".join(codes[:6]) + (" ..." if len(codes) > 6 else "")
            print(f"  {' / '.join(designs):<50} {n:>4}  {sample}")

    if not commit:
        print("\n(dry run -- nothing written; pass --commit)")


def main(argv: list[str] | None = None) -> int:
    """Report or apply the classification."""
    parser = argparse.ArgumentParser(prog="series_classify", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="write the assignments")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        report = run(db, commit=args.commit)
    _print(report, commit=args.commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
