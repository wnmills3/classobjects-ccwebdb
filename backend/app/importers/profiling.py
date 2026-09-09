"""Column profiling: which columns want a reference table, and which are free text.

DURABLE in spirit -- this answers a schema-design question, not a
source-specific one -- but it is a diagnostic tool, not part of the running
application.

The useful trick here is **normalised grouping**. Values that collapse to the
same key once case, spacing and punctuation are removed are almost always the
same concept written differently. That is how "SilverEagle" is recognised as a
variant of "Silver Eagle" without any hand-written list of typos, and the same
mechanism works on every column at once.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .engine import ImportReport
from .reporting import ENCODING

# Recommendations
REFERENCE = "reference table"
FREE_TEXT = "free text"
NUMERIC = "numeric measure"
DATE = "date"
IDENTIFIER = "identifier"
EMPTY = "empty (drop)"
REVIEW = "review"

#: A column with few enough distinct values, repeated often enough, is a
#: controlled vocabulary whether or not anyone declared it one.
REFERENCE_MAX_DISTINCT = 300
REFERENCE_MAX_RATIO = 0.20
#: Above this, values are essentially unique per row.
FREE_TEXT_MIN_RATIO = 0.50

_NORMALISE = re.compile(r"[^a-z0-9]+")


def normalise(value: str) -> str:
    """Collapse case, spacing and punctuation: '$20 Blll' -> '20blll'."""
    return _NORMALISE.sub("", value.casefold())


def _is_number(value: str) -> bool:
    try:
        Decimal(value.replace(",", "").replace("$", "").strip())
        return True
    # InvalidOperation derives from ArithmeticError; ValueError does not.
    except (ArithmeticError, ValueError):
        return False


def _is_date(value: str) -> bool:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


@dataclass
class Variant:
    """A rare spelling that collapses onto a common one."""

    column: str
    rare_value: str
    rare_count: int
    likely_intended: str
    common_count: int


@dataclass
class ColumnProfile:
    """What one source column looks like: fill, cardinality and its values."""

    name: str
    filled: int
    total: int
    distinct: int
    numeric_share: float
    date_share: float
    max_length: int
    recommendation: str
    reason: str
    top_values: list[tuple[str, int]] = field(default_factory=list)
    singletons: int = 0
    variants: list[Variant] = field(default_factory=list)

    @property
    def fill_pct(self) -> float:
        """How much of the column is populated."""
        return (self.filled / self.total * 100) if self.total else 0.0

    @property
    def cardinality_ratio(self) -> float:
        """Distinct values over populated values -- near 1 means free text."""
        return (self.distinct / self.filled) if self.filled else 0.0


def _is_modifier_suffix(rare: str, dominant: str) -> bool:
    """True when the two differ only by a trailing modifier such as '+'.

    A trailing '+' is a qualifier, not a misspelling -- in this domain 'UNC+'
    and 'MS64+' are grades in their own right. Proposing their removal would
    destroy a real distinction, so it is never suggested.
    """
    return rare != dominant and rare.rstrip("+") == dominant.rstrip("+")


def _recommendation(
    *,
    filled: int,
    distinct: int,
    ratio: float,
    numeric_share: float,
    date_share: float,
    max_length: int,
) -> tuple[str, str]:
    """What this column looks like, and the reason to show a person.

    Ordered most specific first: an empty column is not a vocabulary, and a
    column of dates is not a numeric one just because the years parse.
    """
    if filled == 0:
        return EMPTY, "no values present"
    if date_share > 0.95:
        return DATE, f"{date_share:.0%} of distinct values parse as dates"
    if numeric_share > 0.95 and distinct > REFERENCE_MAX_DISTINCT:
        return NUMERIC, f"{numeric_share:.0%} numeric, {distinct} distinct"
    if distinct <= REFERENCE_MAX_DISTINCT and ratio <= REFERENCE_MAX_RATIO:
        return (
            REFERENCE,
            f"{distinct} distinct across {filled} values (ratio {ratio:.3f})",
        )
    if ratio >= FREE_TEXT_MIN_RATIO and max_length > 25:
        return FREE_TEXT, f"nearly unique (ratio {ratio:.2f}), long values"
    if ratio >= FREE_TEXT_MIN_RATIO:
        return IDENTIFIER, f"nearly unique (ratio {ratio:.2f}), short values"
    return (
        REVIEW,
        f"{distinct} distinct, ratio {ratio:.2f} -- between a vocabulary and free text",
    )


def _variants(name: str, counts: Counter, accepted: set[str] | None) -> list[Variant]:
    """Rare spellings of a value that a common one already covers.

    Only meaningful where a vocabulary is expected, so the caller decides
    whether to ask at all.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for value in counts:
        key = normalise(value)
        if key:
            groups[key].append(value)

    variants: list[Variant] = []
    for spellings in groups.values():
        if len(spellings) < 2:
            continue
        ranked = sorted(spellings, key=lambda v: -counts[v])
        dominant = ranked[0]
        for rare in ranked[1:]:
            if accepted and rare in accepted:
                continue  # confirmed correct as written
            if _is_modifier_suffix(rare, dominant):
                continue
            # only flag when one spelling clearly dominates
            if counts[rare] * 3 <= counts[dominant]:
                variants.append(
                    Variant(name, rare, counts[rare], dominant, counts[dominant])
                )
    return variants


def profile_column(
    name: str,
    counts: Counter,
    total_rows: int,
    accepted: set[str] | None = None,
) -> ColumnProfile:
    """Describe one column, and propose corrections for rare spellings."""
    filled = sum(counts.values())
    distinct = len(counts)
    values = list(counts)

    numeric_share = (
        (sum(1 for v in values if _is_number(v)) / distinct) if distinct else 0.0
    )
    date_share = (sum(1 for v in values if _is_date(v)) / distinct) if distinct else 0.0
    max_length = max((len(v) for v in values), default=0)
    ratio = (distinct / filled) if filled else 0.0

    rec, why = _recommendation(
        filled=filled,
        distinct=distinct,
        ratio=ratio,
        numeric_share=numeric_share,
        date_share=date_share,
        max_length=max_length,
    )

    return ColumnProfile(
        name=name,
        filled=filled,
        total=total_rows,
        distinct=distinct,
        numeric_share=numeric_share,
        date_share=date_share,
        max_length=max_length,
        recommendation=rec,
        reason=why,
        top_values=counts.most_common(8),
        singletons=sum(1 for v in values if counts[v] == 1),
        variants=_variants(name, counts, accepted)
        if rec in (REFERENCE, REVIEW)
        else [],
    )


def profile_columns(
    report: ImportReport,
    accepted: Mapping[str, set[str]] | None = None,
) -> list[ColumnProfile]:
    """`accepted` maps a column to values confirmed correct as written.

    Supplied by the source profile, so domain knowledge stays out of here.
    """
    accepted = accepted or {}
    return [
        profile_column(
            name,
            report.column_values.get(name, Counter()),
            report.rows,
            accepted.get(name),
        )
        for name in report.columns
    ]


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def write_columns_csv(profiles: list[ColumnProfile], path: str | Path) -> int:
    """Write the column profiles for review outside the database."""
    path = Path(path)
    with path.open("w", newline="", encoding=ENCODING) as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        w.writerow(
            [
                "column",
                "recommendation",
                "reason",
                "filled",
                "fill_pct",
                "distinct",
                "cardinality_ratio",
                "singletons",
                "max_length",
                "numeric_share",
                "likely_variants",
                "top_values",
            ]
        )
        for p in profiles:
            w.writerow(
                [
                    p.name,
                    p.recommendation,
                    p.reason,
                    p.filled,
                    f"{p.fill_pct:.1f}",
                    p.distinct,
                    f"{p.cardinality_ratio:.4f}",
                    p.singletons,
                    p.max_length,
                    f"{p.numeric_share:.2f}",
                    len(p.variants),
                    " | ".join(f"{v} ({n})" for v, n in p.top_values),
                ]
            )
    return len(profiles)


def write_variants_csv(profiles: list[ColumnProfile], path: str | Path) -> int:
    """Rare spellings that collapse onto a common one -- probable typos."""
    path = Path(path)
    rows = [v for p in profiles for v in p.variants]
    with path.open("w", newline="", encoding=ENCODING) as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        w.writerow(
            ["column", "rare_value", "rare_count", "likely_intended", "common_count"]
        )
        for v in sorted(rows, key=lambda v: (v.column, -v.common_count)):
            w.writerow(
                [
                    v.column,
                    v.rare_value,
                    v.rare_count,
                    v.likely_intended,
                    v.common_count,
                ]
            )
    return len(rows)


def render(profiles: list[ColumnProfile], top: int = 6) -> str:
    """The column profiles as a readable table."""
    out: list[str] = ["COLUMN PROFILE", ""]
    width = max((len(p.name) for p in profiles), default=10)
    out.append(
        f"  {'column'.ljust(width)}  {'recommendation':<16} {'filled':>7} "
        f"{'distinct':>9} {'ratio':>7}  variants"
    )
    for p in profiles:
        out.append(
            f"  {p.name.ljust(width)}  {p.recommendation:<16} {p.filled:>7} "
            f"{p.distinct:>9} {p.cardinality_ratio:>7.3f}  "
            f"{len(p.variants) or ''}"
        )
    flagged = [p for p in profiles if p.variants]
    if flagged:
        out += ["", "LIKELY VARIANTS (rare spelling -> dominant spelling)"]
        for p in flagged:
            out.append(f"  {p.name}")
            for v in sorted(p.variants, key=lambda v: -v.common_count)[:top]:
                out.append(
                    f"      {v.rare_value!r} ({v.rare_count}) -> "
                    f"{v.likely_intended!r} ({v.common_count})"
                )
            if len(p.variants) > top:
                out.append(f"      ... +{len(p.variants) - top} more")
    return "\n".join(out)
