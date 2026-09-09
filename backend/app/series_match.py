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

For those the denomination decides, and an item whose denomination is unknown
is left unclassified rather than guessed at. A wrong series is worse than none:
it would be inherited by every price lookup made against it.

Matches are recorded as `derived`, never `manual`, so a later hand correction
outranks this and is never overwritten by a re-run.

    python -m app.series_match            report, touching nothing
    python -m app.series_match --commit   write the matches
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Denomination, InventoryItem, ProvenanceSource, Series, SeriesAlias

#: Terms that identify a series only once the denomination is known. The value
#: maps a denomination code fragment to the series code the term then means.
AMBIGUOUS: dict[str, dict[str, str]] = {
    "barber": {
        "dime": "barber_dime",
        "quarter": "barber_quarter",
        "half": "barber_half",
    },
    "seated liberty": {
        "dime": "seated_liberty_dime",
        "quarter": "seated_liberty_quarter",
        "half": "seated_liberty_half",
        "dollar": "seated_liberty_dollar",
    },
    "indian head": {
        "cent": "indian_head_cent",
        "penny": "indian_head_cent",
        "nickel": "indian_head_nickel",
    },
    "liberty head": {
        "nickel": "liberty_head_nickel",
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
    "franklin_half": [r"\bfranklin\b"],
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


@dataclass(frozen=True)
class Rule:
    """One compiled way of recognising a series."""

    series_code: str
    pattern: re.Pattern[str]


def build_rules(db: Session) -> list[Rule]:
    """Patterns from each series' own label and aliases, plus the extras.

    Derived from the vocabulary rather than hardcoded, so a nickname added to
    `series_alias` tomorrow starts matching without a code change.
    """
    rules: list[Rule] = []
    ambiguous_terms = set(AMBIGUOUS)

    rows = db.execute(select(Series.id, Series.code, Series.label)).all()
    aliases: dict[int, list[str]] = {}
    for series_id, alias in db.execute(
        select(SeriesAlias.series_id, SeriesAlias.alias)
    ).all():
        aliases.setdefault(series_id, []).append(alias)

    for series_id, code, label in rows:
        terms = [label, *aliases.get(series_id, [])]
        for term in terms:
            if term.lower() in ambiguous_terms:
                continue  # handled by denomination, below
            rules.append(
                Rule(code, re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE))
            )
        for extra in EXTRA.get(code, []):
            rules.append(Rule(code, re.compile(extra, re.IGNORECASE)))
    return rules


def match(text: str, denomination: str | None, rules: list[Rule]) -> set[str]:
    """Every series code this description could mean."""
    found = {rule.series_code for rule in rules if rule.pattern.search(text)}

    # The ambiguous families, resolved by denomination or else abandoned.
    for term, by_denomination in AMBIGUOUS.items():
        if not re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
            continue
        if not denomination:
            continue
        for fragment, series_code in by_denomination.items():
            if fragment in denomination.lower():
                found.add(series_code)
                break
    return found


def _classify(
    items: Sequence[Any], rules: list[Rule], ids: dict[str, int]
) -> tuple[dict[int, int], Counter]:
    """Which series each item earns, and the tally of how it went."""
    stats: Counter = Counter()
    assignments: dict[int, int] = {}
    for item_id, description, title, denomination in items:
        text = f"{title or ''} {description or ''}"
        found = match(text, denomination, rules)
        if not found:
            stats["no_match"] += 1
        elif len(found) > 1:
            # Two series in one description is normal for a mixed lot and must
            # not be resolved by picking one. Left for a person.
            stats["ambiguous"] += 1
        else:
            assignments[item_id] = ids[next(iter(found))]
            stats["matched"] += 1
    return assignments, stats


def _write(db: Session, assignments: dict[int, int]) -> None:
    """Record the classification, and say where the value came from.

    A series the matcher worked out is derived, not something that shipped
    with the catalogue, so seeded provenance moves rather than staying.
    """
    for item_id, series_id in assignments.items():
        item = db.get(InventoryItem, item_id)
        if item is not None:
            item.series_id = series_id
            if item.source is ProvenanceSource.seeded:
                item.source = ProvenanceSource.derived
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
            Denomination.label,
        )
        .join(
            Denomination, Denomination.id == InventoryItem.denomination_id, isouter=True
        )
        .where(InventoryItem.split_at.is_(None), InventoryItem.series_id.is_(None))
    ).all()

    assignments, stats = _classify(items, rules, ids)
    if commit and assignments:
        _write(db, assignments)
        stats["written"] = len(assignments)
    return stats


def main(argv: list[str] | None = None) -> int:
    """Report or apply the series classification."""
    parser = argparse.ArgumentParser(prog="series_match", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="write the matches")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        stats = run(db, commit=args.commit)

    total = stats["matched"] + stats["ambiguous"] + stats["no_match"]
    print(f"unclassified items examined: {total}")
    for key in ("matched", "ambiguous", "no_match", "written"):
        if stats.get(key):
            print(f"  {key:<10} {stats[key]}")
    if not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
