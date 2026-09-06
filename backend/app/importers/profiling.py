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
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
    except (InvalidOperation, ArithmeticError, ValueError):
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
        return (self.filled / self.total * 100) if self.total else 0.0

    @property
    def cardinality_ratio(self) -> float:
        return (self.distinct / self.filled) if self.filled else 0.0


def profile_column(name: str, counts: Counter, total_rows: int) -> ColumnProfile:
    filled = sum(counts.values())
    distinct = len(counts)
    values = list(counts)

    numeric_share = (sum(1 for v in values if _is_number(v)) / distinct) if distinct else 0.0
    date_share = (sum(1 for v in values if _is_date(v)) / distinct) if distinct else 0.0
    max_length = max((len(v) for v in values), default=0)
    ratio = (distinct / filled) if filled else 0.0

    if filled == 0:
        rec, why = EMPTY, "no values present"
    elif date_share > 0.95:
        rec, why = DATE, f"{date_share:.0%} of distinct values parse as dates"
    elif numeric_share > 0.95 and distinct > REFERENCE_MAX_DISTINCT:
        rec, why = NUMERIC, f"{numeric_share:.0%} numeric, {distinct} distinct"
    elif distinct <= REFERENCE_MAX_DISTINCT and ratio <= REFERENCE_MAX_RATIO:
        rec, why = (
            REFERENCE,
            f"{distinct} distinct across {filled} values (ratio {ratio:.3f})",
        )
    elif ratio >= FREE_TEXT_MIN_RATIO and max_length > 25:
        rec, why = FREE_TEXT, f"nearly unique (ratio {ratio:.2f}), long values"
    elif ratio >= FREE_TEXT_MIN_RATIO:
        rec, why = IDENTIFIER, f"nearly unique (ratio {ratio:.2f}), short values"
    else:
        rec, why = (
            REVIEW,
            f"{distinct} distinct, ratio {ratio:.2f} -- between a vocabulary and free text",
        )

    # Near-duplicate detection: only meaningful where a vocabulary is expected.
    variants: list[Variant] = []
    if rec in (REFERENCE, REVIEW):
        groups: dict[str, list[str]] = defaultdict(list)
        for value in values:
            key = normalise(value)
            if key:
                groups[key].append(value)
        for spellings in groups.values():
            if len(spellings) < 2:
                continue
            ranked = sorted(spellings, key=lambda v: -counts[v])
            dominant = ranked[0]
            for rare in ranked[1:]:
                # only flag when one spelling clearly dominates
                if counts[rare] * 3 <= counts[dominant]:
                    variants.append(
                        Variant(name, rare, counts[rare], dominant, counts[dominant])
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
        variants=variants,
    )


def profile_columns(report: ImportReport) -> list[ColumnProfile]:
    return [
        profile_column(name, report.column_values.get(name, Counter()), report.rows)
        for name in report.columns
    ]


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def write_columns_csv(profiles: list[ColumnProfile], path: str | Path) -> int:
    path = Path(path)
    with path.open("w", newline="", encoding=ENCODING) as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_ALL)
        w.writerow(
            [
                "column", "recommendation", "reason", "filled", "fill_pct",
                "distinct", "cardinality_ratio", "singletons", "max_length",
                "numeric_share", "likely_variants", "top_values",
            ]
        )
        for p in profiles:
            w.writerow(
                [
                    p.name, p.recommendation, p.reason, p.filled,
                    f"{p.fill_pct:.1f}", p.distinct, f"{p.cardinality_ratio:.4f}",
                    p.singletons, p.max_length, f"{p.numeric_share:.2f}",
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
                [v.column, v.rare_value, v.rare_count, v.likely_intended, v.common_count]
            )
    return len(rows)


def render(profiles: list[ColumnProfile], top: int = 6) -> str:
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
