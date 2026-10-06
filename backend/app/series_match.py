"""Assign a design series to items from what their description already says.

Half the collection names its series in free text -- "1883-O AU/UNC MORGAN
SILVER DOLLAR" -- so the classification is recoverable rather than something
anyone must type 7,591 times.

**Ambiguity is the whole difficulty.** Several series names are shared across
denominations and mean nothing on their own:

    Barber          dime, quarter and half dollar
    Seated Liberty  dime, quarter, half and dollar
    Indian Head     cent, nickel and gold
    Liberty Head    nickel and gold
    Presidential    a dollar, but also medals and sets

For those the denomination decides, and an item whose denomination is unknown
is left unclassified rather than guessed at. A wrong series is worse than none:
it would be inherited by every price lookup made against it.

Matches are recorded as `derived`, never `manual`, so a later hand correction
outranks this and is never overwritten by a re-run.

**A name is not believed against the item's own year or denomination.**
"National Parks Quarter" on a piece dated 2005 is not an America the
Beautiful quarter, which began in 2010, and "$5 Commemorative" on a half
eagle is not a commemorative half dollar; such an item is left unclassified
and counted, for a person to look at.

Only coins (and the other non-note kinds) are matched here, and only against
coin designs. Notes are classified by `app.series_classify`, which checks the
facts as well as the text.

    python -m app.series_match            report, touching nothing
    python -m app.series_match --commit   write the matches
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from . import aliases
from .database import SessionLocal
from .field_sources import HELD, SERIES_MATCH, record_derived
from .models import (
    Denomination,
    InventoryItem,
    ItemFieldSource,
    ItemKind,
    ProvenanceSource,
    Series,
    SeriesYearRange,
)
from .years import single_year

#: Terms that identify a series only once the denomination is known. The value
#: maps a denomination's code to the series code the term then means. The
#: code, never the label: a label is reworded freely, and one read for the
#: word in it would take "Five Cents" for a cent.
AMBIGUOUS: dict[str, dict[str, str]] = {
    "barber": {
        "usd_coin_0_10": "barber_dime",
        "usd_coin_0_25": "barber_quarter",
        "usd_coin_0_50": "barber_half",
    },
    "seated liberty": {
        "usd_coin_0_10": "seated_liberty_dime",
        "usd_coin_0_25": "seated_liberty_quarter",
        "usd_coin_0_50": "seated_liberty_half",
        "usd_coin_1_00": "seated_liberty_dollar",
    },
    "indian head": {
        "usd_coin_0_01": "indian_head_cent",
        "usd_coin_0_05": "indian_head_nickel",
    },
    "liberty head": {
        "usd_coin_0_05": "liberty_head_nickel",
    },
    # Sellers write "$1 Presidential Coin" and "Presidential $1 Set" as often
    # as "Presidential Dollar". On a dollar the word is the design; on
    # anything else it is an inaugural medal or a set's name.
    "presidential": {
        "usd_coin_1_00": "presidential_dollar",
    },
}

#: Extra patterns beyond a series' own label and aliases, where the collection
#: writes something the vocabulary does not contain.
EXTRA: dict[str, list[str]] = {
    "washington_quarter": [r"washington\s+quarter"],
    "state_quarters": [r"state\s+quarter", r"50\s+states?\s+quarter"],
    "atb_quarters": [r"america\s+the\s+beautiful", r"national\s+park"],
    "morgan_dollar": [r"\bmorgan\b"],
    "peace_dollar": [r"peace\s+(silver\s+)?dollar"],
    # The Franklin Mint struck medals and ingots, not Franklin halves.
    "franklin_half": [r"\bfranklin\b(?!\s+mint\b)"],
    "kennedy_half": [r"\bkennedy\b"],
    "roosevelt_dime": [r"\broosevelt\b"],
    "jefferson_nickel": [r"\bjefferson\b"],
    "eisenhower_dollar": [r"\beisenhower\b"],
    "sacagawea_dollar": [r"\bsacagawea\b"],
    "trade_dollar": [r"trade\s+dollar"],
    "standing_liberty_quarter": [r"standing\s+liberty"],
    "walking_liberty_half": [r"walking\s+liberty"],
    "winged_liberty_head_dime": [r"\bmercury\b"],
    "indian_head_nickel": [r"\bbuffalo\s+nickel\b"],
    "american_silver_eagle": [r"silver\s+eagle"],
    "american_gold_eagle": [r"gold\s+eagle"],
    "american_gold_buffalo": [r"gold\s+buffalo"],
    "saint_gaudens_double_eagle": [r"saint.?gaudens", r"st\.?\s*gaudens"],
    "lincoln_cent": [r"\blincoln\b", r"wheat\s*(cent|penny)", r"wheatie"],
    "large_cent": [r"large\s+cent"],
    "flying_eagle_cent": [r"flying\s+eagle"],
    "shield_nickel": [r"shield\s+nickel"],
    "anthony_dollar": [r"susan\s+b", r"anthony\s+dollar"],
    "presidential_dollar": [r"presidential\s+dollar"],
}

#: `AMBIGUOUS`'s terms, compiled once rather than on every description.
_AMBIGUOUS_PATTERNS = [
    (aliases.word_pattern(term), by_denomination)
    for term, by_denomination in AMBIGUOUS.items()
]


@dataclass(frozen=True)
class Rule:
    """One compiled way of recognizing a series."""

    series_code: str
    pattern: re.Pattern[str]
    #: The inventory the design belongs to (`coin` or `currency`), so a coin
    #: that says "Hawaii" is not taken for a Hawaii overprint note.
    applies_to: str = "coin"


def inventory_of(item_kind: str | None) -> str:
    """Which designs an item can be: currency designs for notes, coin otherwise.

    Bullion, sets and medals are matched against the coin designs, which is
    where the bullion designs (Silver Eagle, Gold Buffalo) live.
    """
    return "currency" if item_kind == "currency" else "coin"


def build_rules(db: Session) -> list[Rule]:
    """Patterns from each series' own label and aliases, plus the extras.

    Derived from the vocabulary rather than hardcoded, so a nickname added to
    `series_alias` tomorrow starts matching without a code change.
    """
    rules: list[Rule] = []
    ambiguous_terms = set(AMBIGUOUS)

    rows = db.execute(
        select(Series.id, Series.code, Series.label, Series.applies_to)
    ).all()
    named = aliases.aliases_by_row(db, Series)

    for series_id, code, label, applies_to in rows:
        terms = [label, *named.get(series_id, [])]
        for term in terms:
            if term.lower() in ambiguous_terms:
                continue  # handled by denomination, below
            rules.append(Rule(code, aliases.word_pattern(term), applies_to))
        for extra in EXTRA.get(code, []):
            rules.append(Rule(code, re.compile(extra, re.IGNORECASE), applies_to))
    return rules


def match(
    text: str,
    denomination: str | None,
    rules: list[Rule],
    inventory: str = "coin",
) -> set[str]:
    """Every series code this description could mean, for that inventory.

    `denomination` is the item's denomination code, or None when it has none.
    """
    found = {
        rule.series_code
        for rule in rules
        if rule.applies_to == inventory and rule.pattern.search(text)
    }
    if inventory != "coin":
        return found  # the ambiguous families are all coin designs

    # The ambiguous families, resolved by denomination or else abandoned.
    for pattern, by_denomination in _AMBIGUOUS_PATTERNS:
        if not pattern.search(text):
            continue
        series_code = by_denomination.get(denomination or "")
        if series_code is not None:
            found.add(series_code)
    return found


#: The years each design was struck, as `(first, last)` spans by series
#: code; a `last` of None is still struck.
Years = dict[str, list[tuple[int, int | None]]]


def design_years(db: Session) -> Years:
    """When each design was struck: its year ranges, else its own span.

    A design with neither has no entry, and is struck in any year as far as
    this module knows.
    """
    years: Years = defaultdict(list)
    ranged = db.execute(
        select(Series.code, SeriesYearRange.year_start, SeriesYearRange.year_end).join(
            SeriesYearRange, SeriesYearRange.series_id == Series.id
        )
    )
    for code, start, end in ranged:
        years[code].append((start, end))
    for code, start, end in db.execute(
        select(Series.code, Series.year_start, Series.year_end)
    ):
        if code not in years and start is not None:
            years[code].append((start, end))
    return dict(years)


def design_denominations(db: Session) -> dict[str, set[str]]:
    """The denominations each design was struck in, by series code.

    Its own, and any its year ranges name. A design struck in several faces
    with none recorded -- the bullion and early gold designs -- has no entry,
    and fits any denomination as far as this module knows.
    """
    faces: dict[str, set[str]] = defaultdict(set)
    own = select(Series.code, Denomination.code).join(
        Denomination, Denomination.id == Series.denomination_id
    )
    ranged = (
        select(Series.code, Denomination.code)
        .join(SeriesYearRange, SeriesYearRange.series_id == Series.id)
        .join(Denomination, Denomination.id == SeriesYearRange.denomination_id)
    )
    for stmt in (own, ranged):
        for code, denomination in db.execute(stmt):
            faces[code].add(denomination)
    return dict(faces)


def struck_in(years: Years, series_code: str, year: int) -> bool:
    """Whether a design was struck in `year`, as far as its years are known."""
    spans = years.get(series_code)
    if not spans:
        return True
    return any(start <= year and (end is None or year <= end) for start, end in spans)


def _classify(
    items: Sequence[Any],
    rules: list[Rule],
    ids: dict[str, int],
    years: Years,
    faces: dict[str, set[str]],
) -> tuple[dict[int, int], Counter]:
    """Which series each item earns, and the tally of how it went."""
    stats: Counter = Counter()
    assignments: dict[int, int] = {}
    for item_id, description, title, denomination, item_kind, start, end in items:
        text = f"{title or ''} {description or ''}"
        named = match(text, denomination, rules, inventory_of(item_kind))
        # A piece of one year cannot be a design not struck that year, nor a
        # piece of one denomination a design struck in others. A range of
        # years spans designs, so it rules nothing out; nor does a
        # denomination left empty.
        year = single_year((start, end))
        found = {
            code
            for code in named
            if (year is None or struck_in(years, code, year))
            and (
                denomination is None or code not in faces or denomination in faces[code]
            )
        }
        if named and not found:
            stats["contradicted"] += 1
        elif not found:
            stats["no_match"] += 1
        elif len(found) > 1:
            # Two series in one description is normal for a mixed lot and must
            # not be resolved by picking one. Left for a person.
            stats["ambiguous"] += 1
        else:
            assignments[item_id] = ids[next(iter(found))]
            stats["matched"] += 1
    return assignments, stats


def record_series(
    db: Session, assignments: dict[int, int], derived_by: str = SERIES_MATCH
) -> None:
    """Record the classification, and say where the value came from.

    A series the matcher worked out is derived, not something that shipped
    with the catalog, so seeded provenance moves rather than staying -- and
    the field itself is recorded as derived, so the editor can mark it and a
    person's later choice replaces it.
    """
    for item_id, series_id in assignments.items():
        item = db.get(InventoryItem, item_id)
        if item is not None:
            item.series_id = series_id
            if item.source is ProvenanceSource.seeded:
                item.source = ProvenanceSource.derived
            record_derived(db, item_id, ["series_id"], derived_by)
    db.commit()


def run(db: Session, *, commit: bool) -> Counter:
    """Classify every unclassified item. Reports rather than guesses."""
    rules = build_rules(db)
    ids = {code: ident for ident, code in db.execute(select(Series.id, Series.code))}

    items = db.execute(
        select(
            InventoryItem.id,
            InventoryItem.description,
            InventoryItem.source_title,
            Denomination.code,
            ItemKind.code,
            InventoryItem.year_start,
            InventoryItem.year_end,
        )
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .join(
            Denomination, Denomination.id == InventoryItem.denomination_id, isouter=True
        )
        .where(
            InventoryItem.split_at.is_(None),
            InventoryItem.series_id.is_(None),
            # A series a person emptied stays empty.
            ~exists().where(
                ItemFieldSource.inventory_item_id == InventoryItem.id,
                ItemFieldSource.field_name == "series_id",
                ItemFieldSource.derived_by == HELD,
            ),
            # Notes are left to app.series_classify, which checks a note's
            # series year against the design before believing its text: a
            # note described as a Funnyback but recorded as Series 1923 is a
            # conflict to show someone, not a Funnyback.
            ItemKind.code != "currency",
        )
    ).all()

    assignments, stats = _classify(
        items, rules, ids, design_years(db), design_denominations(db)
    )
    if commit and assignments:
        record_series(db, assignments)
        stats["written"] = len(assignments)
    return stats


def main(argv: list[str] | None = None) -> int:
    """Report or apply the series classification."""
    parser = argparse.ArgumentParser(prog="series_match", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="write the matches")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        stats = run(db, commit=args.commit)

    outcomes = ("matched", "ambiguous", "contradicted", "no_match")
    print(f"unclassified items examined: {sum(stats[key] for key in outcomes)}")
    for key in (*outcomes, "written"):
        if stats.get(key):
            print(f"  {key:<12} {stats[key]}")
    if not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
