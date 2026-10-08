"""Best guesses at what a bar, round or medal weighs, for a person to confirm.

A coin's weight follows from its denomination and year (`composition`). A
bar, a round or a medal has neither, so its weight is entered by hand
(`routers.inventory`) -- and for the items entered before that was possible,
this pass guesses. A guess is recorded as one (`item_field_source`), so the
item editor marks the field "suggested" and says where it came from; saving
the field by hand makes it the person's, and no pass touches it again.

Only an item of kind bullion, medal or token with **no fine weight** is
looked at, and only its empty fields are filled. Two sources, in order:

- **Its own words.** A weight in the title, the description or the weight as
  written -- "5 oz", "1/2 oz", "10 grams", "8G", "1 Kilo", "One Ounce" --
  becomes the gross weight, and a fineness -- ".999", "999 fine", "sterling",
  "22K" -- the fineness. Two different weights (a lot's description: "ten
  1 oz rounds, 10 oz") say nothing, and neither does two different
  finenesses.
- **Its peers.** Where the words give no weight, what most items of the same
  bullion form and metal already hold: when at least `PEER_SHARE` of them
  (and at least `PEER_MINIMUM`) agree on a fine weight, the item takes the
  commonest gross weight, fineness and fine weight among those. Guessed
  values are never counted as peers, and a peer weight is not applied to an
  item whose own fineness differs -- a sterling round is not a .999 one.
  Peers that record no fineness are taken to be fine metal, so their weight
  goes only to an item of unknown fineness or of .999 and above.

Fine weight is then gross weight times fineness, by the same rule a save
applies (`classifier_defaults.weight_outcome`), unless the peers record a
fine weight with no gross weight or no fineness to work it out from, which
is taken as it stands.

    python -m app.bullion_weights                          report, touching nothing
    python -m app.bullion_weights --list                   ... naming every item
    python -m app.bullion_weights --commit --by EMAIL      write the guesses

Every change is logged in the item's History under the person named.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import field_changes, pass_cli
from .classifier_defaults import refresh_items
from .database import SessionLocal
from .field_sources import (
    HELD,
    WEIGHT_PEERS,
    WEIGHT_RULES,
    WEIGHT_TEXT,
    record_derived,
    sources_by_item,
)
from .models import InventoryItem, ItemKind

__all__ = ["Guess", "Plan", "apply", "fineness_in", "plan", "weight_in"]

#: The kinds whose weight no composition decides.
KINDS: tuple[str, ...] = ("bullion", "medal", "token")
#: The columns this pass may fill.
COLUMNS: tuple[str, ...] = ("gross_weight_ozt", "fineness", "fine_weight_ozt")

#: How much of a form's items must agree before their weight is a guess.
PEER_SHARE = Decimal("0.6")
PEER_MINIMUM = 5

_SIX = Decimal("0.000001")
_FOUR = Decimal("0.0001")
#: Troy ounces in each unit a weight is written in.
_GRAM = Decimal(1) / Decimal("31.1034768")
_UNITS: dict[str, Decimal] = {
    "ozt": Decimal(1),
    "g": _GRAM,
    "kilo": _GRAM * 1000,
    "lb": _GRAM * Decimal("453.59237"),
    "grain": Decimal(1) / 480,
}
#: Heavier than any single piece: a number this large is a lot's, or a typo.
_HEAVIEST = Decimal(400)

_NUMBER = r"(\d+\s*/\s*\d+|\d*\.\d+|\d+)"
_WEIGHT = re.compile(
    _NUMBER + r"\s*-?\s*(?:"
    r"(?P<ozt>(?:troy\s*)?(?:ozt|oz|ounces?)\b\.?)"
    r"|(?P<kilo>kilos?\b|kg\b|kilograms?\b)"
    r"|(?P<lb>lbs?\b|pounds?\b)"
    r"|(?P<grain>grains?\b)"
    r"|(?P<g>grams?\b|gms?\b|gr\b|g\b)"
    r")",
    re.IGNORECASE,
)
_WORDS: tuple[tuple[re.Pattern[str], Decimal], ...] = tuple(
    (re.compile(rf"\b{word}\s+(?:troy\s+)?(?:ounce|oz)\b", re.IGNORECASE), value)
    for word, value in (
        ("one", Decimal(1)),
        ("half", Decimal("0.5")),
        ("quarter", Decimal("0.25")),
        ("tenth", Decimal("0.1")),
    )
)
#: `.999` or `0.999`. A digit before that is a larger number's: `12.925`.
_DECIMAL_FINENESS = re.compile(r"(?<![\d.])0?\.(\d{3,4})(?!\d)")
#: The finenesses metal is actually sold at. Any other `.nnn` in a title is
#: something else -- a lot code, a price.
_KNOWN_FINENESS = frozenset(
    {
        "9999",
        "9995",
        "999",
        "995",
        "990",
        "986",
        "958",
        "925",
        "9167",
        "917",
        "916",
        "900",
        "835",
        "800",
        "750",
        "585",
        "500",
        "400",
        "350",
    }
)
#: Words saying the metal named is a surface, not the piece: its fineness
#: says nothing about what the piece contains.
_SURFACE = re.compile(
    r"\b(?:plated|layered|clad|foil|gilt|gilded|tone|toned|replica|copy)\b",
    re.IGNORECASE,
)
_BARE_FINENESS = re.compile(r"\b(9{3,4})\s*(?:fine|pure)\b", re.IGNORECASE)
_KARAT = re.compile(r"\b(\d{1,2})\s*(?:k|kt|karat|carat)\b", re.IGNORECASE)
_NAMED_FINENESS: tuple[tuple[re.Pattern[str], Decimal], ...] = (
    (re.compile(r"\bsterling\b", re.IGNORECASE), Decimal("0.9250")),
    (re.compile(r"\b90\s*%", re.IGNORECASE), Decimal("0.9000")),
    (re.compile(r"\bcoin silver\b", re.IGNORECASE), Decimal("0.9000")),
)


def _number(text: str) -> Decimal:
    """A written number: `5`, `.5`, `1/2`."""
    if "/" in text:
        top, bottom = (Decimal(part.strip()) for part in text.split("/"))
        return top / bottom if bottom else Decimal(0)
    return Decimal(text)


def weight_in(text: str | None) -> Decimal | None:
    """The one weight `text` states, in troy ounces, or None.

    None when it states no weight, or two different ones: a description is
    often a whole lot's, and says nothing about one piece then.
    """
    found: set[Decimal] = set()
    for match in _WEIGHT.finditer(text or ""):
        unit = next(name for name in _UNITS if match.group(name))
        found.add((_number(match.group(1)) * _UNITS[unit]).quantize(_SIX))
    for pattern, value in _WORDS:
        if pattern.search(text or ""):
            found.add(value.quantize(_SIX))
    found = {weight for weight in found if 0 < weight <= _HEAVIEST}
    return next(iter(found)) if len(found) == 1 else None


def fineness_in(text: str | None) -> Decimal | None:
    """The one fineness `text` states, as a fraction of 1, or None."""
    found: set[Decimal] = set()
    source = text or ""
    if _SURFACE.search(source):
        return None
    for match in _DECIMAL_FINENESS.finditer(source):
        if match.group(1) in _KNOWN_FINENESS:
            found.add(Decimal(f"0.{match.group(1)}"))
    for match in _BARE_FINENESS.finditer(source):
        found.add(Decimal(f"0.{match.group(1)}"))
    for match in _KARAT.finditer(source):
        karat = int(match.group(1))
        if 8 <= karat <= 24:
            found.add(min(Decimal(karat) / 24, Decimal("0.9999")))
    for pattern, value in _NAMED_FINENESS:
        if pattern.search(source):
            found.add(value)
    found = {value.quantize(_FOUR) for value in found if Decimal("0.3") <= value <= 1}
    return next(iter(found)) if len(found) == 1 else None


@dataclass(frozen=True)
class Guess:
    """One value to write on one item: column, value, the rule, and why."""

    item_id: int
    item_code: str
    column: str
    value: Decimal
    rule: str
    why: str


@dataclass
class Plan:
    """What the pass would write, and what it looked at and left."""

    guesses: list[Guess] = field(default_factory=list)
    #: Items looked at, and those for which nothing could be guessed.
    looked_at: int = 0
    no_guess: list[str] = field(default_factory=list)

    def by_rule(self) -> Counter[tuple[str, str]]:
        """Values to write, per (column, rule)."""
        return Counter((guess.column, guess.rule) for guess in self.guesses)

    def items(self) -> int:
        """How many items get at least one value."""
        return len({guess.item_id for guess in self.guesses})


#: What a form and metal's items mostly hold: gross, fineness, fine, and how
#: many of how many agree.
_Peers = tuple[Decimal | None, Decimal | None, Decimal, int, int]


def _peers(
    rows: Sequence[InventoryItem], guessed: dict[int, dict[str, str]]
) -> dict[tuple[int, int | None], _Peers]:
    """Per bullion form and metal, the weight most of its items agree on."""
    held: dict[tuple[int, int | None], list[InventoryItem]] = {}
    for item in rows:
        if item.bullion_form_id is None or item.fine_weight_ozt is None:
            continue
        rules = guessed.get(item.id, {})
        if any(rules.get(column) in WEIGHT_RULES for column in COLUMNS):
            continue  # a guess is not evidence for the next guess
        held.setdefault((item.bullion_form_id, item.metal_id), []).append(item)
    peers: dict[tuple[int, int | None], _Peers] = {}
    for key, items in held.items():
        by_fine = Counter(
            item.fine_weight_ozt.quantize(Decimal("0.01"))
            for item in items
            if item.fine_weight_ozt is not None
        )
        fine, agreeing = by_fine.most_common(1)[0]
        if agreeing < PEER_MINIMUM or Decimal(agreeing) / len(items) < PEER_SHARE:
            continue
        triples = Counter(
            (item.gross_weight_ozt, item.fineness, item.fine_weight_ozt)
            for item in items
            if item.fine_weight_ozt is not None
            and item.fine_weight_ozt.quantize(Decimal("0.01")) == fine
        )
        (gross, fineness, exact), _ = triples.most_common(1)[0]
        if exact is not None:
            peers[key] = (gross, fineness, exact, agreeing, len(items))
    return peers


#: At or above this an item is fine metal: what peers recording a fine
#: weight and no fineness are taken to be.
_FINE = Decimal("0.999")


def _same_metal(fineness: Decimal | None, peer_fineness: Decimal | None) -> bool:
    """Whether peers' weight can stand for an item of this fineness.

    An item whose fineness is unknown takes its peers' as it stands. One
    whose fineness is known takes their weight only when theirs is the same
    -- or, where the peers record none, when the item is fine metal: a
    sterling medal among one-ounce .999 rounds is not one of them, whatever
    its form says.
    """
    if fineness is None:
        return True
    if peer_fineness is None:
        return fineness >= _FINE
    return fineness == peer_fineness


def plan(db: Session) -> Plan:
    """Every guess the pass would write. Writes nothing."""
    rows = (
        db.execute(
            select(InventoryItem)
            .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
            .where(
                ItemKind.code.in_(KINDS),
                InventoryItem.deleted_at.is_(None),
                InventoryItem.split_at.is_(None),
            )
            .order_by(InventoryItem.item_code)
        )
        .scalars()
        .all()
    )
    sources = sources_by_item(db, [item.id for item in rows])
    peers = _peers(rows, sources)
    todo = Plan()
    for item in rows:
        recorded = sources.get(item.id, {})
        if item.fine_weight_ozt is not None or recorded.get("fine_weight_ozt") == HELD:
            continue
        todo.looked_at += 1
        before = len(todo.guesses)

        def guess(
            column: str,
            value: Decimal,
            rule: str,
            why: str,
            item: InventoryItem = item,
            recorded: dict[str, str] = recorded,
        ) -> None:
            """Plan `value` for the item's column, if empty and not held empty."""
            if getattr(item, column) is None and recorded.get(column) != HELD:
                todo.guesses.append(
                    Guess(item.id, item.item_code, column, value, rule, why)
                )

        words = " ".join(
            part
            for part in (item.source_title, item.description, item.weight_note)
            if part
        )
        fineness = item.fineness
        stated_fineness = fineness_in(words)
        if fineness is None and stated_fineness is not None:
            guess("fineness", stated_fineness, WEIGHT_TEXT, "its own words")
            fineness = stated_fineness
        gross = item.gross_weight_ozt
        stated_weight = weight_in(words)
        if gross is None and stated_weight is not None:
            guess("gross_weight_ozt", stated_weight, WEIGHT_TEXT, "its own words")
            gross = stated_weight

        key = (item.bullion_form_id, item.metal_id)
        if gross is None and item.bullion_form_id is not None and key in peers:
            peer_gross, peer_fineness, peer_fine, agreeing, total = peers[
                (item.bullion_form_id, item.metal_id)
            ]
            why = f"{agreeing} of {total} items of its form and metal"
            if _same_metal(fineness, peer_fineness):
                if fineness is None and peer_fineness is not None:
                    guess("fineness", peer_fineness, WEIGHT_PEERS, why)
                    fineness = peer_fineness
                if peer_gross is not None:
                    guess("gross_weight_ozt", peer_gross, WEIGHT_PEERS, why)
                # Without both a gross weight and a fineness nothing works
                # the fine weight out, so the peers' own is taken.
                if peer_gross is None or fineness is None:
                    guess("fine_weight_ozt", peer_fine, WEIGHT_PEERS, why)
        if len(todo.guesses) == before:
            todo.no_guess.append(item.item_code)
    return todo


def apply(db: Session, todo: Plan, user_id: int) -> dict[str, int]:
    """Write the guesses, work out fine weights, and log each item's change.

    The caller commits.
    """
    now = datetime.now(UTC)
    by_item: dict[int, list[Guess]] = {}
    for guess in todo.guesses:
        by_item.setdefault(guess.item_id, []).append(guess)
    before: dict[int, dict[str, str | None]] = {}
    for item_id, guesses in by_item.items():
        item = db.get_one(InventoryItem, item_id)
        before[item_id] = _values(item)
        for guess in guesses:
            setattr(item, guess.column, guess.value)
        for rule in {guess.rule for guess in guesses}:
            record_derived(
                db, item_id, [g.column for g in guesses if g.rule == rule], rule
            )
    # Fine weight from gross weight and fineness, by the rule a save applies.
    refresh_items(db, list(by_item))
    filled = 0
    for item_id, old in before.items():
        item = db.get_one(InventoryItem, item_id)
        db.refresh(item)
        new = _values(item)
        field_changes.record(db, item_id, old, new, COLUMNS, user_id=user_id, at=now)
        filled += new["fine_weight_ozt"] is not None
    db.flush()
    return {
        "items": len(by_item),
        "values": len(todo.guesses),
        "fine_weights": filled,
    }


def _values(item: InventoryItem) -> dict[str, str | None]:
    """The three weight columns as History records them."""
    return {
        column: None if getattr(item, column) is None else str(getattr(item, column))
        for column in COLUMNS
    }


def _print(todo: Plan, *, listed: bool) -> None:
    """Print the plan's counts by rule and, when `listed`, every guess and miss."""
    print(f"items with no fine weight: {todo.looked_at}")
    print(f"items given at least one value: {todo.items()}")
    for (column, rule), count in sorted(todo.by_rule().items()):
        print(f"  {column} from {rule}: {count}")
    print(f"items left as they are (nothing to go on): {len(todo.no_guess)}")
    if not listed:
        return
    for guess in todo.guesses:
        print(
            f"  {guess.item_code} {guess.column} = {guess.value} "
            f"({guess.rule}: {guess.why})"
        )
    for code in todo.no_guess:
        print(f"  {code} left")


def expected_fine(todo: Plan, db: Session) -> int:
    """How many items would end with a fine weight, for the dry run's report."""
    by_item: dict[int, dict[str, Decimal]] = {}
    for guess in todo.guesses:
        by_item.setdefault(guess.item_id, {})[guess.column] = guess.value
    count = 0
    for item_id, values in by_item.items():
        item = db.get_one(InventoryItem, item_id)
        gross = values.get("gross_weight_ozt", item.gross_weight_ozt)
        fineness = values.get("fineness", item.fineness)
        if "fine_weight_ozt" in values or (gross is not None and fineness is not None):
            count += 1
    return count


def main(argv: Sequence[str] | None = None) -> int:
    """Report, or with --commit write, the guessed weights."""
    parser = argparse.ArgumentParser(prog="bullion_weights", description=__doc__)
    pass_cli.add_commit_arguments(parser, "write the guesses")
    parser.add_argument("--list", action="store_true", help="name every item")
    args = pass_cli.parse_args(parser, argv)

    with SessionLocal() as db:
        todo = plan(db)
        _print(todo, listed=args.list)
        print(f"items that would end with a fine weight: {expected_fine(todo, db)}")
        return pass_cli.commit_or_report(
            db, args, lambda user_id: apply(db, todo, user_id)
        )


if __name__ == "__main__":
    sys.exit(main())
