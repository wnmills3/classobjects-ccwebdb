"""Profile for the owner's hand-maintained collection spreadsheet.

*** DISPOSABLE. This module is deleted once the database and admin UI are the
system of record. Do not generalise it, do not build on it, and do not unit
test individual correction rules -- they are being thrown away. ***

Everything source-specific lives here so the engine stays clean.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..profile import (
    ERROR,
    INFO,
    UNKNOWN,
    WARNING,
    Classification,
    Issue,
    RawRow,
    RowResult,
)

# --------------------------------------------------------------------------
# Source columns
# --------------------------------------------------------------------------
COL_ORDERED = "Ordered"
COL_ORDER_NO = "Order Number"
COL_DENOM = "Denom"
COL_YEAR = "Year"
COL_RATING = "Rating"
COL_PRICE = "Price"
COL_DESCRIPTION = "Description"
COL_VENDOR = "Vendor"
COL_SHIPPING = "Shipping"
COL_GRADING = "Grading#"
COL_VALUE = "Value"
COL_COMMENT = "Comment"

# --------------------------------------------------------------------------
# Classification. ORDER MATTERS MORE THAN THE RULES DO.
#
# Bullion and sets are tested before the "number followed by a word" currency
# rule, because otherwise "1oz Copper Round" matches it and becomes currency.
# --------------------------------------------------------------------------
BULLION_FORMS: list[tuple[str, str]] = [
    ("Silver Eagle", r"silver\s*eagle|silvereagle"),
    ("Gold Eagle", r"gold\s*eagle"),
    ("Silver Round", r"silver\s*round"),
    ("Copper Round", r"copper\s*round|\d+\s*oz\s*copper|copper\s*\d+\s*oz"),
    ("Silver Bar", r"silver\s*bar"),
    ("Copper Bar", r"copper\s*bar"),
    ("Gold Maple", r"maple"),
    ("Libertad", r"libertad"),
    ("Krugerrand", r"krugerrand"),
    ("Britannia", r"britannia"),
    ("Philharmonic", r"philharmonic"),
    ("Buffalo", r"buffalo"),
    ("Round", r"\bround\b"),
    ("Bar", r"\bbar\b"),
    ("Bullion", r"\boz\b|bullion|ingot"),
]

SET_FORMS: list[tuple[str, str]] = [
    ("Proof Set", r"proof\s*set"),
    ("Mint Set", r"mint\s*set"),
    ("Prestige Set", r"prestige"),
    ("Coin Set", r"coin\s*set|quarter\s*set|\bset\b"),
]

NAMED_KINDS: list[tuple[str, str, str | None]] = [
    ("medal", r"\bmedal\b", None),
    ("token", r"\btoken\b", None),
    ("other", r"meteorite", "Meteorite"),
]

CURRENCY_WORD = r"\bbill\b|\bnote\b|\bblll\b|\bbil\b"
NUM_THEN_WORD = re.compile(r"^\$?\s*[\d,.]+\s*[A-Za-z]")
PURE_NUMBER = re.compile(r"^\$?\s*[\d,.]+$")
MULTIPLIER = re.compile(r"^(\d+)\s*[xX]\b")

#: Known misspellings and spacing variants. Every correction is logged, so this
#: map stays visible rather than becoming invisible magic. It is not a fuzzy
#: matcher and must not become one.
CORRECTIONS: dict[str, str] = {
    "$20 blll": "$20 Bill",
    "$2bill": "$2 Bill",
    "$20 b": "$20 Bill",
    "#3 bill": "$3 Bill",
    "silvereagle": "Silver Eagle",
}

# --------------------------------------------------------------------------
# Field parsing
# --------------------------------------------------------------------------
YEAR_RANGE = re.compile(r"^(1[5-9]\d{2}|20\d{2})\s*-\s*(1[5-9]\d{2}|20\d{2})")
YEAR_ONE = re.compile(r"^(1[5-9]\d{2}|20\d{2})")
MINT_MARKS = re.compile(r"(?<![A-Za-z0-9])(CC|[PDSOWC])(?![A-Za-z0-9])")
SERIES_LETTER = re.compile(r"^(?:1[5-9]\d{2}|20\d{2})\s*-\s*([A-Z])$")

#: Excel turns long identifiers into floats. The damage is done at rest and the
#: importer can only detect it.
SCIENTIFIC = re.compile(r"^\d(?:\.\d+)?[eE][+-]?\d+$")

#: Statuses the owner recorded in the value column.
VALUE_MARKERS = {
    "x": "received",
    "canceled": "canceled",
    "cancelled": "canceled",
    "returned": "returned",
    "counterfeit": "counterfeit",
}


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ArithmeticError, ValueError):
        return None


class CollectionV1Profile:
    """Rules for one spreadsheet. Disposable."""

    name = "collection_v1"

    # -- classification ----------------------------------------------------
    def _classify(self, denom: str) -> Classification:
        low = denom.casefold()
        if not denom:
            return Classification(kind=UNKNOWN, rule="blank")

        for label, pattern in BULLION_FORMS:
            if re.search(pattern, low):
                return Classification("bullion", label, "bullion-keyword")
        for label, pattern in SET_FORMS:
            if re.search(pattern, low):
                return Classification("set", label, "set-keyword")
        for kind, pattern, subtype in NAMED_KINDS:
            if re.search(pattern, low):
                return Classification(kind, subtype, "named-kind")
        if re.search(CURRENCY_WORD, low):
            return Classification("currency", None, "currency-word")
        if denom.startswith("$"):
            return Classification("currency", None, "currency-symbol")
        if PURE_NUMBER.match(denom):
            return Classification("coin", None, "numeric-denomination")
        if NUM_THEN_WORD.match(denom):
            return Classification("currency", None, "number-then-word")
        return Classification(kind=UNKNOWN, rule="unmatched")

    # -- the profile contract ---------------------------------------------
    def inspect(self, row: RawRow) -> RowResult:
        issues: list[Issue] = []
        fields: dict[str, object] = {}

        denom_raw = row.text(COL_DENOM)
        denom = denom_raw
        corrected = CORRECTIONS.get(denom_raw.casefold())
        if corrected:
            issues.append(
                Issue(
                    rule="denomination-corrected",
                    severity=INFO,
                    column=COL_DENOM,
                    raw_value=denom_raw,
                    proposed=corrected,
                )
            )
            denom = corrected

        classification = self._classify(denom)
        if classification.kind == UNKNOWN:
            issues.append(
                Issue(
                    rule="unclassified",
                    severity=WARNING,
                    column=COL_DENOM,
                    raw_value=denom_raw or "<blank>",
                    note="no classification rule matched",
                )
            )
        fields["denom_raw"] = denom_raw

        # multi-piece lots stay one row with a quantity
        m = MULTIPLIER.match(denom)
        fields["storage_quantity"] = int(m.group(1)) if m else 1

        self._check_money(row, issues, fields)
        self._parse_year(row, issues, fields, classification.kind)
        self._check_identifiers(row, issues, classification.kind, fields)
        self._read_value_column(row, issues, fields)

        return RowResult(classification=classification, issues=issues, fields=fields)

    # -- individual checks -------------------------------------------------
    def _check_money(self, row: RawRow, issues: list[Issue], fields: dict) -> None:
        for column, key in ((COL_PRICE, "price"), (COL_SHIPPING, "shipping")):
            text = row.text(column)
            if not text:
                fields[key] = Decimal("0.00")
                continue
            value = _decimal(text)
            if value is None:
                issues.append(
                    Issue(
                        rule="money-not-numeric",
                        severity=ERROR,
                        column=column,
                        raw_value=text,
                    )
                )
            else:
                fields[key] = value

    def _parse_year(
        self, row: RawRow, issues: list[Issue], fields: dict, kind: str
    ) -> None:
        text = row.text(COL_YEAR)
        fields["year_raw"] = text
        if not text:
            return
        if rng := YEAR_RANGE.match(text):
            fields["year_start"], fields["year_end"] = int(rng.group(1)), int(rng.group(2))
        elif one := YEAR_ONE.match(text):
            fields["year_start"] = fields["year_end"] = int(one.group(1))
        else:
            issues.append(
                Issue(rule="year-unparsed", severity=INFO, column=COL_YEAR, raw_value=text)
            )
            return
        # "1921-D" and "2017-A" are the same shape. A regex cannot tell a coin's
        # mint mark from a note's series letter -- only the item kind can, so
        # route on that rather than on the pattern.
        if kind == "currency":
            if series := SERIES_LETTER.match(text):
                fields["series_letter"] = series.group(1)
        else:
            marks = MINT_MARKS.findall(text[4:])
            if marks:
                fields["mint_marks"] = sorted(set(marks))

    def _check_identifiers(
        self, row: RawRow, issues: list[Issue], kind: str, fields: dict
    ) -> None:
        """Identifiers are text. Detect what a spreadsheet already destroyed."""
        for column in (COL_ORDER_NO, COL_GRADING):
            text = row.text(column)
            if not text:
                continue
            if SCIENTIFIC.match(text):
                issues.append(
                    Issue(
                        rule="identifier-lost-to-scientific-notation",
                        severity=ERROR,
                        column=column,
                        raw_value=text,
                        note="digits are unrecoverable from this file; re-enter from "
                        "the item or a vendor document",
                    )
                )

        # The grading column means different things depending on the kind:
        # a note's own printed serial, or a grading certificate serial.
        grading = row.text(COL_GRADING)
        if grading and not SCIENTIFIC.match(grading):
            fields["serial_number" if kind == "currency" else "cert_number"] = grading

    def _read_value_column(self, row: RawRow, issues: list[Issue], fields: dict) -> None:
        """The value column carries either an appraisal or a status marker."""
        text = row.text(COL_VALUE)
        if not text:
            return
        if (value := _decimal(text)) is not None:
            fields["numismatic_value"] = value
            return
        marker = VALUE_MARKERS.get(text.casefold())
        if marker:
            fields["status_marker"] = marker
        else:
            issues.append(
                Issue(
                    rule="value-not-understood",
                    severity=WARNING,
                    column=COL_VALUE,
                    raw_value=text,
                    note="neither an amount nor a known status marker",
                )
            )
