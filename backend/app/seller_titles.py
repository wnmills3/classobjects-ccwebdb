"""Move the seller's words into the title, and describe the item from its record.

Most items came in with the face value as their title -- "1", "0.5",
"$1 Bill" -- and the seller's listing text as their description. The title
then says nothing a list can be read by, and the description says nothing
about the piece. This pass puts each where it belongs:

    title        "1"                                   -> the seller's text
    description  "1881-S Morgan Silver Dollar BU #412" -> written from the record

An item is changed when all of these hold:

- its title is very short (`SHORT_TITLE` characters or fewer);
- its description is long (`LONG_DESCRIPTION` characters or more), and fits
  a title;
- the record has something to say (`item_descriptions.suggested_description`
  is not empty), and the description is not already that.

Both fields change together or not at all: the seller's text is never
dropped, and a description is never emptied. An item whose title is already
the seller's text is left alone, so the pass can be run again.

Every change is logged to the item's History under the person named.

    python -m app.seller_titles                      report, touching nothing
    python -m app.seller_titles --commit --by EMAIL  write the changes
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import field_changes
from .database import SessionLocal
from .item_descriptions import suggested_description
from .live import live_item
from .models import InventoryItem, ItemKind, User

__all__ = ["LONG_DESCRIPTION", "SHORT_TITLE", "Plan", "apply", "plan"]

#: A title this short is a face value or a denomination -- "1", "0.005",
#: "$1 Bill", "$1000 Bill" -- not a name for the piece.
SHORT_TITLE = 10
#: A description this long says something a title could: "Item as shown #39"
#: does not, "1881-S Morgan Silver Dollar BU #412" does.
LONG_DESCRIPTION = 30
#: The width of `inventory_item.source_title`.
_TITLE_WIDTH = 500


@dataclass(frozen=True)
class Change:
    """One item's new title and description, and what they replace."""

    item_id: int
    item_code: str
    kind: str
    title: str
    description: str
    new_description: str


@dataclass
class Plan:
    """What the pass would change, and why each other short title is left."""

    changes: list[Change] = field(default_factory=list)
    #: Short-titled items left alone, by reason.
    left: Counter[str] = field(default_factory=Counter)


def plan(db: Session) -> Plan:
    """Every live item the rule changes. Writes nothing."""
    todo = Plan()
    rows = db.execute(
        select(InventoryItem, ItemKind.code)
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .where(
            live_item(),
            func.length(func.btrim(InventoryItem.source_title)) <= SHORT_TITLE,
        )
        .order_by(InventoryItem.id)
    ).all()
    for item, kind in rows:
        title = (item.source_title or "").strip()
        description = (item.description or "").strip()
        if len(description) < LONG_DESCRIPTION:
            todo.left["description too short to be a title"] += 1
            continue
        if len(description) > _TITLE_WIDTH:
            todo.left["description too long to be a title"] += 1
            continue
        written = suggested_description(db, item)
        if not written:
            todo.left["nothing in the record to describe it by"] += 1
            continue
        if written == description:
            todo.left["description already written from the record"] += 1
            continue
        todo.changes.append(
            Change(item.id, item.item_code, kind, title, description, written)
        )
    return todo


def apply(db: Session, todo: Plan, user_id: int) -> int:
    """Make the planned changes and log each; the caller commits."""
    now = datetime.now(UTC)
    for change in todo.changes:
        item = db.get_one(InventoryItem, change.item_id)
        before = {"source_title": item.source_title, "description": item.description}
        item.source_title = change.description
        item.description = change.new_description
        after = {"source_title": item.source_title, "description": item.description}
        field_changes.record(
            db,
            item.id,
            before,
            after,
            ["source_title", "description"],
            user_id=user_id,
            at=now,
            text_fields=["source_title", "description"],
        )
    db.flush()
    return len(todo.changes)


def main(argv: Sequence[str] | None = None, *, db: Session | None = None) -> int:
    """Report, or with --commit make, the title and description changes.

    `db` is the session to work in; run as a module, the application's own
    `SessionLocal` is opened. A caller that already has a session -- the
    tests, which must never let this open the live database -- passes it.
    """
    parser = argparse.ArgumentParser(prog="seller_titles", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="write the changes")
    parser.add_argument("--by", help="the person the History rows name (email)")
    parser.add_argument(
        "--show", type=int, default=3, help="examples to print per kind (default 3)"
    )
    args = parser.parse_args(argv)
    if args.commit and not args.by:
        parser.error("--commit needs --by: every change is logged under a person")
    # A seller's text holds whatever they typed; a console that cannot show
    # a character must not stop the report.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    if db is None:
        with SessionLocal() as own:
            return _run(own, args)
    return _run(db, args)


def _run(db: Session, args: argparse.Namespace) -> int:
    """The report, and the changes when asked for, in one session."""
    todo = plan(db)
    by_kind = Counter(change.kind for change in todo.changes)
    print(f"items to change: {len(todo.changes)}")
    for kind, count in by_kind.most_common():
        print(f"  {kind:<10} {count}")
        for change in [c for c in todo.changes if c.kind == kind][: args.show]:
            print(f"    {change.item_code}  title: {change.title!r}")
            print(f"      -> title:       {change.description}")
            print(f"      -> description: {change.new_description}")
    if todo.left:
        print("short titles left alone:")
        for reason, count in todo.left.most_common():
            print(f"  {count:>6}  {reason}")
    if not args.commit:
        print("dry run: nothing written (--commit --by EMAIL to apply)")
        return 0
    user_id = db.scalar(
        select(User.id).where(func.lower(User.email) == args.by.lower())
    )
    if user_id is None:
        print(f"no user {args.by}", file=sys.stderr)
        return 1
    written = apply(db, todo, user_id)
    db.commit()
    print(f"written: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
