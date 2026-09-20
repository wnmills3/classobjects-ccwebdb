"""Fill classifiers that follow from an item's recorded facts.

docs/specs/classifier-defaults-design.md. For a banknote, the denomination and
series decide the class, and with it the seal and the signatures; a Federal
Reserve Note's serial names its issuing Bank. For a coin, denomination,
country and year decide its composition. A person enters the facts; this
fills the rest.

The rules, per field:

- A field is written only when the facts allow exactly one value.
- An empty field is filled. A field holding a derived default
  (`item_field_source`) is refreshed if the facts now say otherwise. Any
  other value is a person's, or came with the data, and is never touched --
  but it does narrow the facts: a note recorded with a red seal is not the
  blue-seal issue of its series.
- A recorded value the facts rule out is reported, not changed.
- A derived value the facts no longer support is retracted: cleared, with
  its record. The machine may take back its own guess; nobody else's.
- A field a person emptied (`held`) stays empty.

Attributes that follow from a note's facts -- No Motto on a $1 Silver
Certificate of Series 1928-1935F (`app.attribute_rules`) -- are added the
same way: only where the note has no link for it at all, a removed one
included; taken back only where this pass added it.

    python -m app.classifier_defaults            report, touching nothing
    python -m app.classifier_defaults --commit   write the defaults
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import attribute_rules
from .database import SessionLocal
from .field_sources import (
    COMPOSITION,
    HELD,
    NOTE_ISSUE,
    SERIAL_DISTRICT,
    forget,
    record_derived,
    sources_by_item,
)
from .models import (
    Composition,
    CurrencyDetail,
    Denomination,
    FedDistrict,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ItemKind,
    NoteIssue,
    NoteType,
    ProvenanceSource,
    ReferenceAlias,
)
from .serial_patterns import WELL_FORMED

#: The note columns an issue decides, in the order they are reported.
NOTE_COLUMNS: tuple[str, ...] = (
    "note_type_id",
    "seal_color_id",
    "signature_combination_id",
)

#: The item columns a composition decides.
COMPOSITION_COLUMNS: tuple[str, ...] = (
    "composition_id",
    "metal_id",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
)

#: From Series 1996, $5 and higher carry two leading serial letters: the
#: series, then the Bank (BEP, bep.gov/currency/serial-numbers).
TWO_LETTER_FROM_YEAR = 1996
TWO_LETTER_FROM_FACE = Decimal("5")

#: A Federal Reserve Bank's letter, A (Boston) through L (San Francisco).
BANK_LETTERS = frozenset("ABCDEFGHIJKL")

#: The rule of a Change that clears a derived value the facts dropped.
RETRACT = "retract"

#: A note's columns the pass fills, the Bank included.
NOTE_FILLED: tuple[str, ...] = (
    "note_type_id",
    "seal_color_id",
    "signature_combination_id",
    "fed_district_id",
)

#: One issue, as the facts table records it.
IssueKey = tuple[int, int, str | None]


@dataclass(frozen=True)
class Issue:
    """What one small-size issue was: class, seal, signatures, serial letter."""

    note_type_id: int
    seal_color_id: int
    signature_combination_id: int | None
    serial_prefix: str | None


@dataclass(frozen=True)
class Case:
    """One item a person should look at, and why."""

    item_code: str
    reason: str
    detail: str


@dataclass(frozen=True)
class Change:
    """One default to write, or to retract: row, column, value, rule.

    A retraction has `value` None and `rule` RETRACT.
    """

    item_id: int
    on_note: bool
    column: str
    value: object
    rule: str


@dataclass(frozen=True)
class LinkChange:
    """An attribute link a rule adds (`add`) or takes back."""

    item_id: int
    attribute_id: int
    add: bool


@dataclass(frozen=True)
class Link:
    """An existing link, as far as the rules care."""

    active: bool
    derived_by: str | None


@dataclass
class Report:
    """What the pass found and would write."""

    changes: list[Change] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    review: list[Case] = field(default_factory=list)
    links: list[LinkChange] = field(default_factory=list)

    def by_column(self, *, retracted: bool = False) -> Counter:
        """Writes (or retractions) per column: `note_type_id` 907."""
        return Counter(
            change.column
            for change in self.changes
            if (change.rule == RETRACT) == retracted
        )


def load_issues(db: Session) -> dict[IssueKey, list[Issue]]:
    """Every seeded issue, by denomination, series year and letter."""
    issues: dict[IssueKey, list[Issue]] = {}
    for row in db.execute(select(NoteIssue)).scalars():
        issues.setdefault(
            (row.denomination_id, row.series_year, row.series_letter), []
        ).append(
            Issue(
                row.note_type_id,
                row.seal_color_id,
                row.signature_combination_id,
                row.serial_prefix,
            )
        )
    return issues


def decide(
    current: dict[str, Any],
    derived: set[str],
    options: dict[str, set[object]],
    held: frozenset[str] = frozenset(),
) -> tuple[dict[str, object], list[str]]:
    """What to write, given what is recorded and what the facts allow.

    `options` is, per column, every value the facts allow. Returns the columns
    to write and the derived columns whose value the facts no longer support.
    A held column is a person's choice to leave it empty, and is skipped.
    """
    writes: dict[str, object] = {}
    stale: list[str] = []
    for column, allowed in options.items():
        value = current.get(column)
        mine = column in derived
        if column in held or (value is not None and not mine):
            continue  # a person's value, or the data's: never touched
        only = next(iter(allowed)) if len(allowed) == 1 else None
        if only is not None:
            if only != value:
                writes[column] = only
        elif mine and value is not None and value not in allowed - {None}:
            stale.append(column)
    return writes, stale


def _retract(
    out: Outcome, current: dict[str, Any], derived: set[str], columns: tuple[str, ...]
) -> None:
    """Take back derived values the facts no longer decide."""
    for column in columns:
        if column in derived and current.get(column) is not None:
            out.retracts.append(column)


def _note_options(
    issues: list[Issue], current: dict[str, Any], derived: set[str]
) -> tuple[list[Issue], list[str]]:
    """The issues this note can be, and the recorded columns that rule all out.

    A value a person recorded narrows the issues; a derived one does not,
    since it is only what this pass concluded last time.
    """
    fixed = {
        column: current[column]
        for column in NOTE_COLUMNS
        if current.get(column) is not None and column not in derived
    }
    matches = [
        issue
        for issue in issues
        if all(getattr(issue, column) == value for column, value in fixed.items())
    ]
    if matches or not issues:
        return matches, []
    contradicted = [
        column
        for column, value in fixed.items()
        if all(getattr(issue, column) != value for issue in issues)
    ]
    return [], contradicted or sorted(fixed)


def district_letter(serial: str | None, face: Decimal, year: int) -> tuple[str, str]:
    """The Bank letter a Federal Reserve Note's serial names, and its series letter.

    Returns ("", "") when the serial is missing or not the shape its era
    uses; a star in place of a prefix letter hides the Bank.
    """
    text = (serial or "").replace(" ", "").upper()
    if not WELL_FORMED.match(text):
        return "", ""
    prefix = text[: len(text) - 9]
    two = year >= TWO_LETTER_FROM_YEAR and face >= TWO_LETTER_FROM_FACE
    if len(prefix) != (2 if two else 1) or "*" in prefix:
        return "", ""
    return prefix[-1], (prefix[0] if two else "")


def _items(
    db: Session, item_ids: Collection[int] | None = None
) -> Sequence[tuple[Any, ...]]:
    """Every live item, or the given ones, with the facts defaults come from."""
    query = (
        select(
            InventoryItem,
            ItemKind.code,
            Denomination.face_value,
            CurrencyDetail,
        )
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .join(
            Denomination,
            Denomination.id == InventoryItem.denomination_id,
            isouter=True,
        )
        .join(
            CurrencyDetail,
            CurrencyDetail.inventory_item_id == InventoryItem.id,
            isouter=True,
        )
        .where(InventoryItem.split_at.is_(None))
        .order_by(InventoryItem.item_code)
    )
    if item_ids is not None:
        query = query.where(InventoryItem.id.in_(list(item_ids)))
    return db.execute(query).tuples().all()


@dataclass
class Facts:
    """The published facts, loaded once per run."""

    issues: dict[IssueKey, list[Issue]]
    compositions: list[Composition]
    note_type_labels: dict[int, str]
    note_type_codes: dict[int, str]
    #: The attributes the rules name, by code.
    rule_attributes: dict[str, tuple[int, str]]
    #: Patterns naming each class, from its label and aliases.
    class_names: list[tuple[int, re.Pattern[str]]]
    frn_id: int | None
    districts: dict[str, int]
    first_issue_year: int


@dataclass(frozen=True)
class NoteFacts:
    """What a note records that its defaults are decided from."""

    denomination_id: int | None
    face: Decimal | None
    series_year: int | None
    series_letter: str | None
    serial_number: str | None
    #: The rating, in the owner's words: the only text read as evidence of a
    #: class. Descriptions are too often a lot's.
    rating: str | None
    #: Recorded values of NOTE_COLUMNS and `fed_district_id`.
    current: dict[str, Any]


@dataclass
class Outcome:
    """What the facts say about one item."""

    writes: list[tuple[str, object, str]] = field(default_factory=list)
    #: Derived columns to clear: the facts no longer support them.
    retracts: list[str] = field(default_factory=list)
    cases: list[tuple[str, str]] = field(default_factory=list)
    count: str = ""


def load_facts(db: Session) -> Facts:
    """Everything the defaults are decided from."""
    issues = load_issues(db)
    labels = dict(db.execute(select(NoteType.id, NoteType.label)).tuples().all())
    names: dict[int, list[str]] = {
        type_id: [label] for type_id, label in labels.items()
    }
    for row_id, alias in db.execute(
        select(ReferenceAlias.row_id, ReferenceAlias.alias).where(
            ReferenceAlias.table_name == NoteType.__tablename__,
            ReferenceAlias.is_active.is_(True),
        )
    ).tuples():
        names.setdefault(row_id, []).append(alias)
    class_names = [
        (type_id, re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE))
        for type_id, words in names.items()
        for name in words
    ]
    return Facts(
        issues=issues,
        compositions=list(
            db.execute(
                select(Composition).order_by(Composition.year_from.desc())
            ).scalars()
        ),
        note_type_labels=labels,
        note_type_codes=dict(
            db.execute(select(NoteType.id, NoteType.code)).tuples().all()
        ),
        rule_attributes={
            code: (row_id, label)
            for row_id, code, label in db.execute(
                select(ItemAttribute.id, ItemAttribute.code, ItemAttribute.label).where(
                    ItemAttribute.code.in_([r.attribute for r in attribute_rules.RULES])
                )
            ).tuples()
        },
        class_names=class_names,
        frn_id=db.execute(
            select(NoteType.id).where(NoteType.code == "frn")
        ).scalar_one_or_none(),
        districts=dict(
            db.execute(select(FedDistrict.letter, FedDistrict.id)).tuples().all()
        ),
        first_issue_year=min((key[1] for key in issues), default=9999),
    )


def note_outcome(
    facts: Facts,
    note: NoteFacts,
    derived: set[str],
    held: frozenset[str] = frozenset(),
) -> Outcome:
    """Note type, seal and signatures from the issue; the Bank from the serial."""
    out = Outcome()
    current = note.current
    if note.denomination_id is None or note.series_year is None:
        out.count = "note: no denomination or series year"
        _retract(out, current, derived, NOTE_FILLED)
        return out
    letter = (note.series_letter or "").strip().upper() or None
    series = f"{note.series_year}{letter or ''}"
    known = facts.issues.get((note.denomination_id, note.series_year, letter), [])
    if not known:
        out.count = "note: series not in the facts"
        if note.series_year >= facts.first_issue_year:
            out.cases.append(("unknown issue", series))
        _retract(out, current, derived, NOTE_FILLED)
        return out

    matches, contradicted = _note_options(known, current, derived)
    if contradicted:
        out.count = "note: recorded value the facts rule out"
        names = ", ".join(c.removesuffix("_id") for c in contradicted)
        out.cases.append(("disagrees", f"{series}: {names}"))
        _retract(out, current, derived, NOTE_FILLED)
        return out

    # Several classes, and the rating names exactly one of them: that one.
    classes = {issue.note_type_id for issue in matches}
    if len(classes) > 1 and note.rating:
        named = {
            type_id
            for type_id, pattern in facts.class_names
            if type_id in classes and pattern.search(note.rating)
        }
        if len(named) == 1:
            matches = [issue for issue in matches if issue.note_type_id in named]

    options = {
        column: {getattr(issue, column) for issue in matches} for column in NOTE_COLUMNS
    }
    writes, stale = decide(current, derived, options, held)
    out.writes += [(column, value, NOTE_ISSUE) for column, value in writes.items()]
    out.retracts += stale
    note_type = writes.get(
        "note_type_id",
        None if "note_type_id" in stale else current.get("note_type_id"),
    )
    if note_type is None:
        _retract(out, current, derived, ("fed_district_id",))
        out.count = "note: class undecided (seal would decide)"
        labels = sorted(facts.note_type_labels[t] for t in options["note_type_id"])
        out.cases.append(("ambiguous", f"{series}: {' / '.join(labels)}"))
        return out
    out.count = "note: class known"

    if note_type != facts.frn_id or note.face is None:
        _retract(out, current, derived, ("fed_district_id",))
        return out
    bank, series_letter = district_letter(
        note.serial_number, note.face, note.series_year
    )
    if not bank:
        _retract(out, current, derived, ("fed_district_id",))
        return out
    prefixes = {issue.serial_prefix for issue in matches} - {None}
    if series_letter and len(prefixes) == 1 and series_letter not in prefixes:
        out.cases.append(
            (
                "serial prefix",
                f"{note.serial_number} is not Series {series} "
                f"(which starts {next(iter(prefixes))})",
            )
        )
    district = facts.districts.get(bank) if bank in BANK_LETTERS else None
    if district is None:
        out.cases.append(("serial prefix", f"{note.serial_number}: no Bank {bank}"))
        _retract(out, current, derived, ("fed_district_id",))
        return out
    recorded = current.get("fed_district_id")
    writes, _ = decide(
        {"fed_district_id": recorded},
        derived,
        {"fed_district_id": {district}},
        held,
    )
    out.writes += [(c, v, SERIAL_DISTRICT) for c, v in writes.items()]
    if recorded not in (None, district) and "fed_district_id" not in derived:
        out.cases.append(("disagrees", f"district, serial says {bank}"))
    return out


def coin_outcome(
    facts: Facts,
    item: InventoryItem,
    derived: set[str],
    held: frozenset[str] = frozenset(),
) -> Outcome:
    """Composition, metal, fineness and weights from denomination and year.

    A range of years has a composition only when one covers all of it: a
    1999-2008 quarter set is clad throughout, a 1909-2022 cent lot is not.
    """
    out = Outcome()
    current = {column: getattr(item, column) for column in COMPOSITION_COLUMNS}
    if (
        item.denomination_id is None
        or item.country_id is None
        or item.year_start is None
    ):
        _retract(out, current, derived, COMPOSITION_COLUMNS)
        return out
    first = item.year_start
    last = item.year_end if item.year_end is not None else first
    found = next(
        (
            c
            for c in facts.compositions
            if c.denomination_id == item.denomination_id
            and c.country_id == item.country_id
            and c.year_from <= first
            and (c.year_to is None or c.year_to >= last)
        ),
        None,
    )
    if found is None:
        _retract(out, current, derived, COMPOSITION_COLUMNS)
        return out
    options: dict[str, set[object]] = {
        column: {getattr(found, column if column != "composition_id" else "id")}
        for column in COMPOSITION_COLUMNS
    }
    writes, stale = decide(current, derived, options, held)
    out.writes += [(column, value, COMPOSITION) for column, value in writes.items()]
    out.retracts += stale
    differs = [
        column.removesuffix("_id")
        for column, allowed in options.items()
        if column not in derived
        and current[column] is not None
        and None not in allowed
        and current[column] not in allowed
    ]
    if differs:
        out.cases.append(
            ("disagrees", f"composition says otherwise: {', '.join(differs)}")
        )
    out.count = "coin: composition known"
    return out


def _note_facts(
    item: InventoryItem, detail: CurrencyDetail, face: Decimal | None
) -> NoteFacts:
    columns = (*NOTE_COLUMNS, "fed_district_id")
    return NoteFacts(
        denomination_id=item.denomination_id,
        face=face,
        series_year=detail.series_year,
        series_letter=detail.series_letter,
        serial_number=detail.serial_number,
        rating=item.grade_raw,
        current={column: getattr(detail, column) for column in columns},
    )


def _links(
    db: Session, facts: Facts, item_ids: Collection[int] | None
) -> dict[tuple[int, int], Link]:
    """The links of the attributes the rules name, removed ones included."""
    ids = [row_id for row_id, _ in facts.rule_attributes.values()]
    query = select(
        ItemAttributeLink.inventory_item_id,
        ItemAttributeLink.item_attribute_id,
        ItemAttributeLink.removed_at,
        ItemAttributeLink.derived_by,
    ).where(ItemAttributeLink.item_attribute_id.in_(ids))
    if item_ids is not None:
        query = query.where(ItemAttributeLink.inventory_item_id.in_(list(item_ids)))
    return {
        (item_id, attribute_id): Link(removed_at is None, derived_by)
        for item_id, attribute_id, removed_at, derived_by in db.execute(query).tuples()
    }


def attribute_outcome(
    facts: Facts,
    item_id: int,
    note: NoteFacts,
    note_type_id: int | None,
    links: dict[tuple[int, int], Link],
) -> tuple[list[LinkChange], list[tuple[str, str]], list[str]]:
    """The links the rules add or take back, the cases, and the counts."""
    changes: list[LinkChange] = []
    cases: list[tuple[str, str]] = []
    counts: list[str] = []
    note_type = facts.note_type_codes.get(note_type_id) if note_type_id else None
    series = f"{note.series_year}{(note.series_letter or '').strip().upper()}"
    for rule in attribute_rules.RULES:
        found = facts.rule_attributes.get(rule.attribute)
        if found is None:
            continue
        attribute_id, label = found
        verdict = attribute_rules.verdict(
            rule, note_type, note.face, note.series_year, note.series_letter
        )
        link = links.get((item_id, attribute_id))
        active = link is not None and link.active
        mine = active and link is not None and link.derived_by == attribute_rules.RULE
        if verdict == attribute_rules.ALWAYS:
            if link is None:
                changes.append(LinkChange(item_id, attribute_id, add=True))
                counts.append(f"attribute: {rule.attribute} added")
            continue
        if mine:
            changes.append(LinkChange(item_id, attribute_id, add=False))
            counts.append(f"attribute: {rule.attribute} taken back")
        elif verdict == attribute_rules.EVIDENCE and not active:
            cases.append(("needs evidence", f"{series}: {label}?"))
        elif verdict == attribute_rules.NEVER and active:
            cases.append(("disagrees", f"{series} is never {label}"))
    return changes, cases, counts


def classify(db: Session, item_ids: Collection[int] | None = None) -> Report:
    """Decide the defaults of every item, or of the given ones, writing nothing."""
    facts = load_facts(db)
    sources = sources_by_item(db, item_ids)
    links = _links(db, facts, item_ids)
    report = Report()
    for item, kind, face, detail in _items(db, item_ids):
        recorded = sources.get(item.id, {})
        mine = {f for f, rule in recorded.items() if rule != HELD}
        held = frozenset(f for f, rule in recorded.items() if rule == HELD)
        if kind == "currency":
            if detail is None:
                report.counts["note: no currency detail"] += 1
                continue
            note = _note_facts(item, detail, face)
            outcome = note_outcome(facts, note, mine, held)
            on_note = True
            written = {column: value for column, value, _ in outcome.writes}
            note_type_id = written.get(
                "note_type_id",
                None if "note_type_id" in outcome.retracts else detail.note_type_id,
            )
            changes, cases, counts = attribute_outcome(
                facts,
                item.id,
                note,
                note_type_id if isinstance(note_type_id, int) else None,
                links,
            )
            report.links += changes
            report.counts.update(counts)
            report.review += [Case(item.item_code, r, d) for r, d in cases]
        else:
            outcome = coin_outcome(facts, item, mine, held)
            on_note = False
        if outcome.count:
            report.counts[outcome.count] += 1
        report.changes += [
            Change(item.id, on_note, column, value, rule)
            for column, value, rule in outcome.writes
        ]
        report.changes += [
            Change(item.id, on_note, column, None, RETRACT)
            for column in dict.fromkeys(outcome.retracts)
        ]
        report.review += [
            Case(item.item_code, "retracted", column.removesuffix("_id"))
            for column in dict.fromkeys(outcome.retracts)
        ]
        report.review += [
            Case(item.item_code, reason, detail) for reason, detail in outcome.cases
        ]
    return report


def apply(db: Session, report: Report, *, commit: bool = True) -> None:
    """Write the report's changes and record each field as derived.

    **Commits by default**, unlike every other pass here, which takes a
    keyword-only `commit` with no default so the caller has to state its
    intent. The default is kept because the CLI entry point is the usual
    caller and a pass that writes nothing is useless; `refresh_items` -- run
    inside a request handler on every item create and edit -- passes
    `commit=False` so it does not commit someone else's transaction, and any
    other in-request caller must do the same.
    """
    by_item: dict[int, list[Change]] = {}
    for change in report.changes:
        by_item.setdefault(change.item_id, []).append(change)
    for item_id, changes in by_item.items():
        item = db.get(InventoryItem, item_id)
        if item is None:
            continue
        for change in changes:
            target = item.currency_detail if change.on_note else item
            setattr(target, change.column, change.value)
        forget(db, [item_id], [c.column for c in changes if c.rule == RETRACT])
        for rule in {c.rule for c in changes} - {RETRACT}:
            record_derived(
                db, item_id, [c.column for c in changes if c.rule == rule], rule
            )
    for link in report.links:
        if link.add:
            db.add(
                ItemAttributeLink(
                    inventory_item_id=link.item_id,
                    item_attribute_id=link.attribute_id,
                    source=ProvenanceSource.derived,
                    derived_by=attribute_rules.RULE,
                )
            )
        else:
            row = db.get(ItemAttributeLink, (link.item_id, link.attribute_id))
            if row is not None:
                db.delete(row)
    if commit:
        db.commit()


def refresh_items(db: Session, item_ids: Collection[int]) -> None:
    """Bring items' defaults up to date with their facts, without committing.

    Called when an item is created or edited, so a note entered today has its
    class now rather than at the next batch run, and a corrected series year
    corrects the class that was derived from it.
    """
    if not item_ids:
        return
    db.flush()
    apply(db, classify(db, item_ids), commit=False)


def suggest(db: Session, note: NoteFacts) -> dict[str, int]:
    """The defaults a note with these facts would get, for the entry form.

    `note.current` holds only what the person has chosen, so their choices
    narrow the suggestion exactly as a recorded value does.
    """
    outcome = note_outcome(load_facts(db), note, set())
    return {
        column: value for column, value, _ in outcome.writes if isinstance(value, int)
    }


def run(db: Session, *, commit: bool) -> Report:
    """Classify, and write only when asked."""
    report = classify(db)
    if commit and (report.changes or report.links):
        apply(db, report)
        report.counts["written"] = len(report.changes) + len(report.links)
    return report


def _print(report: Report, *, commit: bool) -> None:
    """The counts the owner reviews before anything is written."""
    for key, n in sorted(report.counts.items()):
        print(f"  {key:<45} {n:>6}")
    headings = (
        (False, "written", "would write"),
        (True, "retracted", "would retract"),
    )
    for retracted, done, planned in headings:
        columns = report.by_column(retracted=retracted)
        if not columns:
            continue
        print(f"\n{done if commit else planned}, by field:")
        for column, n in columns.most_common():
            print(f"  {column.removesuffix('_id'):<30} {n:>6}")

    reasons = Counter(case.reason for case in report.review)
    for reason, total in reasons.most_common():
        cases = [c for c in report.review if c.reason == reason]
        print(f"\n{reason} ({total}):")
        groups = Counter(c.detail for c in cases)
        for detail, n in groups.most_common(12):
            codes = [c.item_code for c in cases if c.detail == detail]
            sample = ", ".join(codes[:4]) + (" ..." if len(codes) > 4 else "")
            print(f"  {detail[:60]:<60} {n:>4}  {sample}")
    if not commit:
        print("\n(dry run -- nothing written; pass --commit)")


def main(argv: list[str] | None = None) -> int:
    """Report or apply the defaults."""
    parser = argparse.ArgumentParser(prog="classifier_defaults", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="write the defaults")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        report = run(db, commit=args.commit)
    _print(report, commit=args.commit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
