"""Read stored ratings again with the current rules.

docs/specs/item-attributes-design.md, section 4. The importer learned to
read more of a rating -- a bare "69 PCGS", "SP68PCGS", "P70DCAM", UCAM,
release pedigrees, CAC, Genuine, No Motto, Reverse Proof -- but the
collection was imported before it had. This applies the same rules
(`app.importers.rating`) to the items already stored.

**It fills what is empty and leaves what is not.** A grade, strike,
designation or grader already recorded stays, and so does anything a person
confirmed (`item_field_review`) or emptied on purpose (`held`). Two kinds of
value are *corrected* rather than filled, both machine readings the old
rules got wrong, and the report lists each one:

- a strike the rating names outright -- "Reverse PF70" was stored as a plain
  proof;
- FS read as Full Steps on something that is not a Jefferson nickel.

What it fills is recorded as derived by `rating`, so a person's later edit
takes over as with every other pass. Attributes are added as derived links,
and never where any link exists -- a removed one included.

    python -m app.rating_pass            report, write nothing
    python -m app.rating_pass --commit   apply

Every proposal is also written to `rating_pass.csv` in the log directory.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from . import aliases, grades
from .config import REPO_ROOT
from .database import SessionLocal
from .field_sources import HELD, RATING, record_derived
from .importers.loader import SchemaLoader
from .importers.rating import (
    ParsedCondition,
    bare_grade,
    designation_for,
    parse_condition,
)
from .models import (
    AppliesTo,
    Authenticity,
    GradeDesignation,
    GradingService,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ItemFieldReview,
    ItemFieldSource,
    ItemKind,
    ProvenanceSource,
    ReferenceMixin,
    StrikeType,
)

#: `item_field_source.derived_by` and `item_attribute_link.derived_by`.
RULE = RATING

#: Strikes a rating names outright, which may correct a stored plain one.
_NAMED_STRIKES = frozenset({"reverse_proof", "enhanced_reverse_proof"})
_PLAIN_STRIKES = frozenset({"proof", "business"})

#: Why a value was not filled although the rating names it.
LOCKED = "held or confirmed"


@dataclass(frozen=True)
class Change:
    """One thing the pass would do to one item."""

    item_code: str
    field: str
    before: str
    after: str
    #: fill, correct, add (an attribute) or skip.
    action: str
    #: What settled it: the rating, a designation, the description...
    how: str
    rating: str


@dataclass
class Report:
    """What a run found, and what it did."""

    changes: list[Change] = field(default_factory=list)
    #: Bare numbers nothing settled the strike of, as (item code, rating).
    undecided: list[tuple[str, str]] = field(default_factory=list)
    #: Attributes a rating named for the other kind of item.
    misfits: list[tuple[str, str]] = field(default_factory=list)

    def counts(self) -> Counter[tuple[str, str, str]]:
        """Changes by field, action and what settled them."""
        return Counter((c.field, c.action, c.how) for c in self.changes)


def _columns(
    db: Session,
    model: type[ItemFieldSource] | type[ItemFieldReview],
    *where: ColumnElement[bool],
) -> dict[int, set[str]]:
    found: dict[int, set[str]] = {}
    stmt = select(model.inventory_item_id, model.field_name).where(*where)
    for item_id, name in db.execute(stmt).tuples():
        found.setdefault(item_id, set()).add(name)
    return found


class _Pass:
    """One run over the collection."""

    def __init__(self, db: Session, *, commit: bool) -> None:
        self.db = db
        self.commit = commit
        self.report = Report()
        self.loader = SchemaLoader(db)
        self._ids: dict[tuple[str, str], int | None] = {}
        self._codes: dict[tuple[str, int], str] = {}
        self.kinds = dict(db.execute(select(ItemKind.id, ItemKind.code)).tuples().all())
        self.attributes = {
            row.code: row
            for row in db.scalars(
                select(ItemAttribute).where(ItemAttribute.is_active.is_(True))
            )
        }
        self.unverified = self.named(Authenticity, "unverified")
        self.locked_columns = _columns(
            db, ItemFieldSource, ItemFieldSource.derived_by == HELD
        )
        for item_id, names in _columns(db, ItemFieldReview).items():
            self.locked_columns.setdefault(item_id, set()).update(names)
        self.linked: set[tuple[int, int]] = set(
            db.execute(
                select(
                    ItemAttributeLink.inventory_item_id,
                    ItemAttributeLink.item_attribute_id,
                )
            )
            .tuples()
            .all()
        )

    # -- lookups -----------------------------------------------------------

    def named(self, model: type[ReferenceMixin], word: str) -> int | None:
        """The row a code, label or alias names, resolved once per run."""
        key = (model.__tablename__, word)
        if key not in self._ids:
            found = aliases.resolve(self.db, model, word)
            self._ids[key] = found.row_id if found else None
        return self._ids[key]

    def code(self, model: type[ReferenceMixin], row_id: int | None) -> str:
        """A row's code, or "" for none."""
        if row_id is None:
            return ""
        key = (model.__tablename__, row_id)
        if key not in self._codes:
            row = self.db.get(model, row_id)
            self._codes[key] = row.code if row is not None else str(row_id)
        return self._codes[key]

    # -- one item ----------------------------------------------------------

    def item(self, item: InventoryItem) -> None:
        """Everything the item's rating says that the item does not hold."""
        rating = (item.grade_raw or "").strip()
        if not rating:
            return
        self.current = item
        self.rating = rating
        self.filled: list[str] = []
        self.locked = self.locked_columns.get(item.id, set())
        parsed = parse_condition(rating)
        context = f"{item.description or ''} {item.source_title or ''}"
        kind = self.kinds.get(item.item_kind_id)

        # A note keeps its own scale, which the importer already read.
        if kind != "currency":
            self.grade_and_strike(parsed, context)
        self.designation(parsed, f"{rating} {context}")
        self.service(parsed)
        self.authenticity(parsed)
        wanted = list(parsed.attributes)
        if kind == "currency":
            wanted = list(parsed.note_attributes) + wanted
        self.item_attributes(wanted, kind)

        if self.commit and self.filled:
            record_derived(self.db, item.id, self.filled, RULE)

    def note(self, column: str, before: str, after: str, action: str, how: str) -> bool:
        """Record a proposal; True when it is to be applied now."""
        if action in {"fill", "correct"} and column in self.locked:
            action, how = "skip", LOCKED
        self.report.changes.append(
            Change(
                self.current.item_code, column, before, after, action, how, self.rating
            )
        )
        if action in {"fill", "correct"}:
            self.filled.append(column)
            return self.commit
        return False

    def grade_and_strike(self, parsed: ParsedCondition, context: str) -> None:
        """Fill a missing grade and strike; correct a strike the rating names."""
        item = self.current
        grade_code: str | None = None
        strike = parsed.strike_type
        how = "rating"
        if parsed.grade is not None:
            split = grades.split(parsed.grade)
            if split is not None:
                grade_code = split.grade
                strike = strike or split.strike_type
        elif parsed.bare_number is not None:
            bare = bare_grade(parsed, context)
            if bare is None:
                self.report.undecided.append((item.item_code, self.rating))
                return
            grade_code, strike, how = bare.grade, bare.strike_type, bare.by

        if (
            item.grade_id is None
            and grade_code is not None
            and self.note("grade_id", "", grade_code, "fill", how)
        ):
            item.grade_id = self.loader.number_grade_id(grade_code)

        strike_id = self.named(StrikeType, strike) if strike else None
        if strike is None or strike_id is None:
            return
        current = self.code(StrikeType, item.strike_type_id)
        if item.strike_type_id is None and (
            item.grade_id is not None or grade_code is not None
        ):
            if self.note("strike_type_id", "", strike, "fill", how):
                item.strike_type_id = strike_id
        elif (
            parsed.strike_type in _NAMED_STRIKES
            and current in _PLAIN_STRIKES
            and current != strike
            and self.note(
                "strike_type_id", current, strike, "correct", "rating names the strike"
            )
        ):
            item.strike_type_id = strike_id

    def designation(self, parsed: ParsedCondition, text: str) -> None:
        """Fill a missing designation; clear an FS that is not Full Steps."""
        item = self.current
        designation = designation_for(parsed, text)
        if item.grade_designation_id is None and designation:
            found = self.named(GradeDesignation, designation)
            if found is not None and self.note(
                "grade_designation_id",
                "",
                self.code(GradeDesignation, found),
                "fill",
                "rating",
            ):
                item.grade_designation_id = found
        elif (
            parsed.designation == "FS"
            and designation is None
            and self.code(GradeDesignation, item.grade_designation_id) == "FS"
            and self.note(
                "grade_designation_id", "FS", "", "correct", "FS is not Full Steps here"
            )
        ):
            item.grade_designation_id = None

    def service(self, parsed: ParsedCondition) -> None:
        """Fill a missing grader."""
        item = self.current
        if item.grading_service_id is not None or not parsed.service:
            return
        found = self.named(GradingService, parsed.service)
        if found is not None and self.note(
            "grading_service_id", "", parsed.service, "fill", "rating"
        ):
            item.grading_service_id = found

    def authenticity(self, parsed: ParsedCondition) -> None:
        """Genuine, where the item still says unverified -- the default."""
        item = self.current
        if not parsed.authenticity or item.authenticity_id != self.unverified:
            return
        found = self.named(Authenticity, parsed.authenticity)
        if found is not None and self.note(
            "authenticity_id", "unverified", parsed.authenticity, "fill", "rating"
        ):
            item.authenticity_id = found

    def item_attributes(self, codes: list[str], kind: str | None) -> None:
        """Link what the rating names, where no link exists at all."""
        item = self.current
        own = AppliesTo.currency if kind == "currency" else AppliesTo.coin
        for code in dict.fromkeys(codes):
            attribute = self.attributes.get(code)
            if attribute is None or (item.id, attribute.id) in self.linked:
                continue
            if attribute.applies_to not in {own, AppliesTo.any}:
                self.report.misfits.append((item.item_code, code))
                continue
            self.linked.add((item.id, attribute.id))
            self.report.changes.append(
                Change(
                    item.item_code, "attribute", "", code, "add", "rating", self.rating
                )
            )
            if self.commit:
                self.db.add(
                    ItemAttributeLink(
                        inventory_item_id=item.id,
                        item_attribute_id=attribute.id,
                        source=ProvenanceSource.derived,
                        derived_by=RULE,
                    )
                )


def run(db: Session, *, commit: bool) -> Report:
    """Propose, and optionally apply, what the current rules read."""
    work = _Pass(db, commit=commit)
    items = db.scalars(
        select(InventoryItem)
        .where(
            InventoryItem.deleted_at.is_(None),
            InventoryItem.split_at.is_(None),
            InventoryItem.grade_raw.is_not(None),
        )
        .order_by(InventoryItem.item_code)
    ).all()
    for item in items:
        work.item(item)
    if commit:
        db.commit()
    else:
        db.rollback()
    return work.report


def write_csv(changes: Iterable[Change], path: Path) -> None:
    """Every proposal, one row each, for review in a spreadsheet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(
            ["item_code", "field", "before", "after", "action", "how", "rating"]
        )
        for c in changes:
            writer.writerow(
                [c.item_code, c.field, c.before, c.after, c.action, c.how, c.rating]
            )


def default_csv() -> Path:
    """`rating_pass.csv` in the log directory (CCWEB_LOG_DIR, default logs)."""
    log_dir = os.environ.get("CCWEB_LOG_DIR") or "logs"
    return REPO_ROOT / log_dir / "rating_pass.csv"


def main(argv: list[str] | None = None) -> int:
    """Report, or apply, what the current rating rules read."""
    parser = argparse.ArgumentParser(prog="rating_pass", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="apply the changes")
    parser.add_argument("--csv", type=Path, default=None, help="where to write them")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        report = run(db, commit=args.commit)

    for (column, action, how), count in sorted(report.counts().items()):
        print(f"  {count:5}  {action:8} {column:22} {how}")
    print(
        f"\n  {len(report.undecided)} bare numbers with nothing to settle the strike:"
    )
    for code, rating in report.undecided:
        print(f"    {code}  {rating!r}")
    if report.misfits:
        print(f"\n  {len(report.misfits)} attributes for the other kind of item:")
        for code, attribute in report.misfits:
            print(f"    {code}  {attribute}")
    corrections = [c for c in report.changes if c.action == "correct"]
    if corrections:
        print(f"\n  {len(corrections)} corrections to stored values:")
        for c in corrections:
            print(f"    {c.item_code}  {c.field}: {c.before} -> {c.after or '(none)'}")
            print(f"      {c.rating!r}")

    path = args.csv or default_csv()
    write_csv(report.changes, path)
    print(f"\n  every proposal: {path}")
    if not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
