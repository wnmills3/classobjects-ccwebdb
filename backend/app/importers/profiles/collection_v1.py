"""Profile for the owner's hand-maintained collection spreadsheet.

*** DISPOSABLE. This module is deleted once the database and admin UI are the
system of record. Do not generalise it, do not build on it, and do not unit
test individual correction rules -- they are being thrown away. ***

Everything source-specific lives here so the engine stays clean.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

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
COL_LINK = "Link"
COL_SHIPPING = "Shipping"
COL_GRADING = "Grading#"
COL_VALUE = "Value"
COL_COMMENT = "Comment"

# Series name the profile matches on.
_SILVER_EAGLE = "Silver Eagle"

# --------------------------------------------------------------------------
# Classification. ORDER MATTERS MORE THAN THE RULES DO.
#
# Bullion and sets are tested before the "number followed by a word" currency
# rule, because otherwise "1oz Copper Round" matches it and becomes currency.
# --------------------------------------------------------------------------
BULLION_FORMS: list[tuple[str, str]] = [
    (_SILVER_EAGLE, r"silver\s*eagle|silvereagle"),
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
    "silvereagle": _SILVER_EAGLE,
    "silvre eagle": _SILVER_EAGLE,
    "meteoriate": "Meteorite",
    "mixxed": "Mixed",
    "coloriazed state quarters": "Colorized State Quarters",
}

# --------------------------------------------------------------------------
# Values confirmed correct as written.
#
# The column profiler proposes corrections by collapsing case, spacing and
# punctuation, which makes some legitimate values look like typos. Each entry
# here was checked against the actual record and is not to be flagged again.
# --------------------------------------------------------------------------
KNOWN_GOOD: dict[str, set[str]] = {
    COL_DENOM: {
        # A 1/4 oz American Gold Eagle has a $25 face value. It collapses onto
        # "2.5" -- the $2.50 Quarter Eagle -- which is a different coin entirely.
        "25",
        # ".5 oz" is a half ounce. It collapses onto "5oz", which is ten times
        # the metal.
        "Silver Round .5 oz",
        "Silver Round .5oz",
    },
    COL_YEAR: {
        # A year may be a single year, a range, a decade, or uncertain.
        # Decades use an apostrophe, e.g. a roll of pennies spanning the 1980s.
        # They collapse onto "1980-S", which is a mint mark, not a decade.
        "1980's",
        "1970's",
        "2024?",  # the year is uncertain; meaning still unresolved
        "2016-",  # "2016=" is the majority but the "=" is unexplained
    },
}

# --------------------------------------------------------------------------
# Known values.
#
# An explicit map, not clever patterns: every entry is auditable at a glance
# and correctable in isolation. Consulted before the keyword rules, since an
# exact match is the most specific thing we can know. Disposable, like the
# rest of this module.
# --------------------------------------------------------------------------
KNOWN_VALUES: dict[str, tuple[str, str | None]] = {
    # -- sets --------------------------------------------------------------
    "mint proof": ("set", "Mint Proof Set"),
    "mint silver": ("set", "Silver Mint Set"),
    "silver mint": ("set", "Silver Mint Set"),
    "silver mint proof": ("set", "Silver Proof Set"),
    "silver rev mint": ("set", "Reverse Proof Set"),
    "reverse proof": ("set", "Reverse Proof Set"),
    "proof": ("set", "Proof Set"),
    "silver proof": ("set", "Silver Proof Set"),
    "tribute proof": ("set", "Tribute Proof Set"),
    "mixed sets": ("set", "Mixed Sets"),
    "nickel series": ("set", "Nickel Series"),
    "collection": ("set", "Collection"),
    "colorized state quarters": ("set", "Colorized State Quarters"),
    # -- bullion -----------------------------------------------------------
    "ltd ed. silver": ("bullion", "Limited Edition Silver"),
    "premier silver": ("bullion", "Premier Silver"),
    "silver": ("bullion", "Silver"),
    "silver 10oz": ("bullion", "Silver"),
    "silver 15oz": ("bullion", "Silver"),
    "silver coins 9.3303oz": ("bullion", "Silver"),
    "silver square 10g": ("bullion", "Silver Square"),
    "silver square 5g": ("bullion", "Silver Square"),
    "silver onza": ("bullion", "Silver Onza"),
    "copper 1/2 kilo": ("bullion", "Copper"),
    "copper roll 20x 1oz": ("bullion", "Copper Round"),
    "titanium 1lb": ("bullion", "Titanium"),
    "tuvalu 2oz": ("bullion", "Silver"),
    "dragon purple 1oz": ("bullion", "Silver"),
    "1/500 gold": ("bullion", "Gold"),
    "gold nugget 1.5gm": ("bullion", "Gold Nugget"),
    "gold foil": ("bullion", "Gold Foil"),
    "silver foil $100": ("bullion", "Silver Foil"),
    # -- coins -------------------------------------------------------------
    "box pennies": ("coin", None),
    "roll": ("coin", None),
    "roll pennies": ("coin", None),
    "roll 1c": ("coin", None),
    "roll 25c": ("coin", None),
    "roll dimes": ("coin", None),
    "roll quarters": ("coin", None),
    "rolls .25": ("coin", None),
    "peso": ("coin", None),
    "cien peso": ("coin", None),
    "cien pesos": ("coin", None),
    "mexican peso": ("coin", None),
    "duit": ("coin", None),
    "voc duit": ("coin", None),
    "thaler": ("coin", None),
    "farthing": ("coin", None),
    "half penny": ("coin", None),
    "one crown": ("coin", None),
    "double denarius": ("coin", None),
    "colonial coin": ("coin", None),
    "constantine x": ("coin", "Ancient"),
    "constans": ("coin", "Ancient"),
    "julian ii": ("coin", "Ancient"),
    "herod 1": ("coin", "Ancient"),
    "17-18th century": ("coin", None),
    "m25 japan yen": ("coin", None),
    "m26 japan yen": ("coin", None),
    "m27 japan yen": ("coin", None),
    "mark 45": ("coin", None),
    "bullet paisa": ("coin", None),
    "silver coin": ("coin", None),
    "mini coins": ("coin", None),
    "bimetalic": ("coin", None),
    "double eagle": ("coin", None),
    "commemorative": ("coin", "Commemorative"),
    "comm": ("coin", "Commemorative"),
    "pres coin": ("coin", "Presidential"),
    "indian chief": ("coin", None),
    "nixon": ("coin", "Presidential"),
    "1880-o": ("coin", None),
    # -- currency ----------------------------------------------------------
    "mixed bills": ("currency", None),
    "mixed gold bills": ("currency", None),
    "1/2 goldback": ("currency", "Goldback"),
    "1/4 gold back": ("currency", "Goldback"),
    "currency": ("currency", None),
    "adv script": ("currency", "Advertising Scrip"),
    "dozen dollars": ("currency", None),
    # -- medals ------------------------------------------------------------
    "medals": ("medal", None),
    # -- other -------------------------------------------------------------
    "mixed": ("other", "Mixed Lot"),
    "multi": ("other", "Mixed Lot"),
    "special": ("other", None),
    "pirate": ("other", "Novelty"),
    "pirate coin": ("other", "Novelty"),
    "pirate money": ("other", "Novelty"),
    "stamps": ("other", "Stamps"),
    "jeweler loop": ("other", "Equipment"),
    "flip": ("other", "Supplies"),
    "merch": ("other", "Merchandise"),
    "vaultbox": ("other", "Vault Box"),
}

#: Packaging, read from the denomination and description together.
STORAGE_FORMS: list[tuple[str, str]] = [
    ("box", r"\bbox\b"),
    ("roll", r"\brolls?\b"),
    ("tube", r"\btube\b"),
    ("bag", r"\bbag\b"),
    ("album", r"\balbum\b|\bfolder\b"),
    ("proof set", r"proof\s*set"),
    ("mint set", r"mint\s*set"),
]

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

# --------------------------------------------------------------------------
# Weight
#
# Weights are written into the denomination rather than a column of their own,
# in five different units. They are extracted to troy ounces because that is
# what melt value multiplies against.
#
# Decimal throughout, never float: this number multiplies into money.
# --------------------------------------------------------------------------
GRAMS_PER_OZT = Decimal("31.1034768")
GRAMS_PER_LB = Decimal("453.59237")

#: unit as written -> troy ounces per unit.
#: "oz" is read as a TROY ounce, the bullion convention. Copper rounds are
#: often sold by the avoirdupois ounce (28.35 g), 9.7% lighter, so weight_raw
#: is kept for anything that later needs re-reading.
UNIT_TO_OZT: dict[str, Decimal] = {
    "ozt": Decimal(1),
    "oz": Decimal(1),
    "ounce": Decimal(1),
    "ounces": Decimal(1),
    "g": Decimal(1) / GRAMS_PER_OZT,
    "gm": Decimal(1) / GRAMS_PER_OZT,
    "gram": Decimal(1) / GRAMS_PER_OZT,
    "grams": Decimal(1) / GRAMS_PER_OZT,
    "kg": Decimal(1000) / GRAMS_PER_OZT,
    "kilo": Decimal(1000) / GRAMS_PER_OZT,
    "kilos": Decimal(1000) / GRAMS_PER_OZT,
    "lb": GRAMS_PER_LB / GRAMS_PER_OZT,
    "lbs": GRAMS_PER_LB / GRAMS_PER_OZT,
    "dwt": Decimal(1) / Decimal(20),  # pennyweight
}

#: A quantity may be decimal, a bare fraction, or start with the point --
#: ".5 oz" is a half ounce and must not be read as 5.
WEIGHT = re.compile(
    r"(\d+\s*/\s*\d+|\d+\.\d+|\.\d+|\d+)\s*"
    r"(ozt|ounces|ounce|oz|grams|gram|gm|kilos|kilo|kg|lbs|lb|dwt|g)\b",
    re.I,
)

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
    # InvalidOperation derives from ArithmeticError; ValueError does not.
    except (ArithmeticError, ValueError):
        return None


class CollectionV1Profile:
    """Rules for one spreadsheet. Disposable."""

    name = "collection_v1"
    known_good = KNOWN_GOOD

    # -- classification ----------------------------------------------------
    def _classify(self, denom: str) -> Classification:
        low = denom.casefold()
        if not denom:
            return Classification(kind=UNKNOWN, rule="blank")

        if known := KNOWN_VALUES.get(low):
            kind, subtype = known
            return Classification(kind, subtype, "known-value")

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
        """Classify one spreadsheet row and pull its fields out."""
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

        blob = f"{denom} {row.text(COL_DESCRIPTION)}".casefold()
        fields["storage_form"] = next(
            (name for name, pat in STORAGE_FORMS if re.search(pat, blob)), "single"
        )

        self._parse_weight(denom, issues, fields)
        self._check_money(row, issues, fields)
        self._parse_year(row, issues, fields, classification.kind)
        self._check_identifiers(row, issues, classification.kind, fields)
        self._read_value_column(row, issues, fields)
        self._carry_text(row, fields)
        self._parse_face_value(denom_raw, classification.kind, fields)

        return RowResult(classification=classification, issues=issues, fields=fields)

    def _carry_text(self, row: RawRow, fields: dict) -> None:
        """Carry the remaining source columns across the seam.

        Copies them under the normalised names the loader expects.

        This is the whole of the seam: everything above decides what a value
        *means*, and this decides what the schema calls it. The loader never
        sees a source column name.
        """
        fields["title"] = row.text(COL_DENOM)
        fields["description"] = row.text(COL_DESCRIPTION)
        fields["grade_raw"] = row.text(COL_RATING) or None
        fields["comment"] = row.text(COL_COMMENT) or None
        fields["order_number"] = row.text(COL_ORDER_NO) or None

        # Two different URLs, and the difference matters. `Vendor` is the
        # seller's own page -- ebay.com/usr/sylvinac -- and identifies who sold
        # it. `Link` is the item -- ebay.com/itm/315248803796 -- and carries
        # the venue's transaction id, which is the only way to recover a
        # purchase for the 2,644 rows whose order number was never recorded.
        # Emitting only the first left `identify()` reading a seller page and
        # finding nothing.
        fields["listing_url"] = row.text(COL_LINK) or None

        # The host is the vendor's identity -- a dozen different deep links are
        # all one seller.
        vendor_url = row.text(COL_VENDOR) or None
        if vendor_url:
            fields["vendor_url"] = vendor_url
            host = re.search(r"https?://([^/]+)", vendor_url)
            fields["vendor_name"] = (
                host.group(1).lower().removeprefix("www.") if host else vendor_url
            )

        if ordered := row.text(COL_ORDERED):
            fields["ordered_on"] = _as_date(ordered)

    def _parse_face_value(self, denom: str, kind: str, fields: dict) -> None:
        """Read a face value out of the denomination, when it states one.

        Face value is what links an item to the `composition` table, and
        composition is what makes melt valuation possible at all -- a 1963
        dime is 90% silver because the law said so. Without this the whole
        public-facts mechanism is inert.

        Only unambiguous forms are read. _SILVER_EAGLE has a face value of
        $1 but is worth its metal, and reading a face value there would
        resolve it to a composition it does not have.
        """
        text = denom.strip()
        if not text:
            return

        if kind == "currency":
            if m := re.match(
                r"^\$?\s*([\d,]+(?:\.\d+)?)\s*(?:bill|note)?\b", text, re.I
            ):
                fields["face_value"] = _decimal(m.group(1))
                fields["denomination_kind"] = "note"
            return

        # A coin denomination in this source is written as a bare number:
        # 0.25 is a quarter, 25 is a $25 Gold Eagle. Anything with words in
        # it is a product name, not a face value.
        if kind == "coin" and PURE_NUMBER.match(text):
            fields["face_value"] = _decimal(text.lstrip("$").strip())
            fields["denomination_kind"] = "coin"

    # -- individual checks -------------------------------------------------
    def _parse_weight(self, denom: str, issues: list[Issue], fields: dict) -> None:
        """Pull a weight out of the denomination and convert to troy ounces.

        Only the denomination is read. Descriptions are seller prose and
        mentioning a weight there does not make it this item's weight.
        """
        if not denom:
            return
        matches = WEIGHT.findall(denom)
        if not matches:
            return
        if len(matches) > 1:
            issues.append(
                Issue(
                    rule="weight-ambiguous",
                    severity=WARNING,
                    column=COL_DENOM,
                    raw_value=denom,
                    note=f"{len(matches)} weights in one denomination",
                )
            )
            return

        quantity, unit = matches[0]
        factor = UNIT_TO_OZT.get(unit.lower())
        if factor is None:
            issues.append(
                Issue(
                    rule="weight-unit-unknown",
                    severity=WARNING,
                    column=COL_DENOM,
                    raw_value=f"{quantity} {unit}",
                )
            )
            return

        try:
            if "/" in quantity:
                num, den = (p.strip() for p in quantity.split("/"))
                amount = Decimal(num) / Decimal(den)
            else:
                amount = Decimal(quantity)
        # InvalidOperation and ZeroDivisionError both derive from
        # ArithmeticError.
        except ArithmeticError:
            issues.append(
                Issue(
                    rule="weight-not-parsed",
                    severity=WARNING,
                    column=COL_DENOM,
                    raw_value=quantity,
                )
            )
            return

        fields["weight_raw"] = f"{quantity}{unit}"
        fields["weight_unit_raw"] = unit.lower()
        # six decimal places matches the column; quantize rather than round
        fields["weight_ozt"] = (amount * factor).quantize(Decimal("0.000001"))

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
            fields["year_start"], fields["year_end"] = (
                int(rng.group(1)),
                int(rng.group(2)),
            )
        elif one := YEAR_ONE.match(text):
            fields["year_start"] = fields["year_end"] = int(one.group(1))
        else:
            issues.append(
                Issue(
                    rule="year-unparsed", severity=INFO, column=COL_YEAR, raw_value=text
                )
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

    def _read_value_column(
        self, row: RawRow, issues: list[Issue], fields: dict
    ) -> None:
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


def _as_date(text: str) -> date | None:
    """Read a date the spreadsheet may have written several ways.

    Returns None rather than a guess: an unparsed order date is a missing
    fact, and inventing one would put a wrong figure in a real column.
    """
    from datetime import datetime as _dt

    value = text.strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%Y/%m/%d"):
        try:
            return _dt.strptime(value, fmt).date()
        except ValueError:
            continue
    # openpyxl hands back real datetimes for date-formatted cells.
    try:
        return _dt.fromisoformat(value).date()
    except ValueError:
        return None
