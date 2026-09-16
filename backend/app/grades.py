"""Grades as a strike type and a number, and back again.

docs/specs/item-attributes-design.md, decisions 1-3. A coin's grade is split:
``PR69+`` is strike type ``proof`` with grade ``69+``; ``MS65`` is
``business`` with ``65``. Adjectival grades take the bottom of their standard
range, with the owner's ladder for UNC and BU: plain is Uncirculated (60),
one plus is Choice (63), two pluses are Gem (65).

Everything that reads a compound grade -- the importer, and an API client
sending ``MS65`` -- goes through :func:`split`; everything that shows one goes
through :func:`display`, which the database mirrors in ``grade_display()``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import InventoryItem

#: Strike type codes.
BUSINESS = "business"
PROOF = "proof"
SPECIMEN = "specimen"
REVERSE_PROOF = "reverse_proof"
ENHANCED_REVERSE_PROOF = "enhanced_reverse_proof"
SMS = "sms"

#: The prefix a compound grade is written with, to its strike type.
_PREFIX_STRIKE = {
    "MS": BUSINESS,
    "AU": BUSINESS,
    "XF": BUSINESS,
    "EF": BUSINESS,
    "VF": BUSINESS,
    "F": BUSINESS,
    "VG": BUSINESS,
    "G": BUSINESS,
    "AG": BUSINESS,
    "FR": BUSINESS,
    "PO": BUSINESS,
    "P": BUSINESS,
    "PR": PROOF,
    "PF": PROOF,
    "SP": SPECIMEN,
}

_COMPOUND = re.compile(r"^(MS|PR|PF|SP|AU|XF|EF|VF|VG|AG|FR|PO|F|G|P)-?(\d{1,2})(\+*)$")
_NOTE = re.compile(r"^N\d{1,2}\+?$")

#: Adjectival coin grades: strike type and number (decision 3).
ADJECTIVAL: dict[str, tuple[str, int]] = {
    "UNC": (BUSINESS, 60),
    "BU": (BUSINESS, 60),
    "CHOICE_UNC": (BUSINESS, 63),
    "CHOICE_BU": (BUSINESS, 63),
    "GEM_UNC": (BUSINESS, 65),
    "GEM_BU": (BUSINESS, 65),
    "PROOF": (PROOF, 63),
    "CHOICE_PROOF": (PROOF, 63),
    "GEM_PROOF": (PROOF, 65),
    "AU": (BUSINESS, 55),
    "XF": (BUSINESS, 40),
    "VF": (BUSINESS, 20),
    "F": (BUSINESS, 12),
    "VG": (BUSINESS, 8),
    "G": (BUSINESS, 4),
}

#: UNC and BU climb the owner's ladder with pluses instead of gaining one:
#: one plus is Choice, two are Gem.
_LADDER = {"UNC": (60, 63, 65), "BU": (60, 63, 65)}

#: Adjectival note grades take the bottom of the note scale's range.
NOTE_ADJECTIVAL: dict[str, str] = {
    "N_GEM_UNC": "N65",
    "N_CHOICE_UNC": "N63",
    "N_UNC": "N60",
    "N_AU": "N50",
    "N_XF": "N40",
    "N_VF": "N20",
    "N_F": "N12",
    "N_VG": "N8",
    "N_G": "N4",
}

#: Grades with no number that stay as they are.
UNNUMBERED = frozenset({"CIRC", "UNGRADED"})

#: The business-strike prefix for each number, highest first.
_BUSINESS_PREFIXES = (
    (60, "MS"),
    (50, "AU"),
    (40, "XF"),
    (20, "VF"),
    (12, "F"),
    (8, "VG"),
    (4, "G"),
    (3, "AG"),
    (2, "FR"),
    (1, "PO"),
)


@dataclass(frozen=True)
class Split:
    """A grade taken apart: its strike type (or None) and its grade code."""

    strike_type: str | None
    grade: str


def number_code(number: int, plus: bool) -> str:
    """The grade code for a Sheldon number: ``65``, ``64+``."""
    return f"{number}{'+' if plus else ''}"


def split(text: str) -> Split | None:
    """Take a compound or adjectival grade apart; None if it is not one.

    ``MS65`` -> business, 65; ``PR69+`` -> proof, 69+; ``GEM_BU`` ->
    business, 65; ``BU+`` -> business, 63; ``AU+`` -> business, 55+;
    ``N_UNC`` -> no strike, N60; ``65`` -> no strike, 65.
    """
    code = text.strip().upper().replace(" ", "_")
    if not code:
        return None
    if code.rstrip("+").isdigit():
        return Split(None, code)
    if _NOTE.match(code) or code in UNNUMBERED:
        return Split(None, code)
    if code in NOTE_ADJECTIVAL:
        return Split(None, NOTE_ADJECTIVAL[code])
    if match := _COMPOUND.match(code):
        prefix, number, pluses = match.groups()
        return Split(_PREFIX_STRIKE[prefix], number_code(int(number), bool(pluses)))

    base = code.rstrip("+")
    pluses = len(code) - len(base)
    if base in _LADDER:
        return Split(BUSINESS, str(_LADDER[base][min(pluses, 2)]))
    if base in ADJECTIVAL:
        strike, number = ADJECTIVAL[base]
        return Split(strike, number_code(number, pluses > 0))
    return None


#: Where each business prefix's numbers run, as a grade rank (a plus adds a
#: half). MS runs to 70, AU from 50 to 58+, and so on down the scale.
_PREFIX_RANGES: dict[str, tuple[int, int]] = {
    "MS": (60, 70),
    "AU": (50, 58),
    "XF": (40, 49),
    "EF": (40, 49),
    "VF": (20, 39),
    "F": (12, 19),
    "VG": (8, 11),
    "G": (4, 7),
    "AG": (3, 3),
    "FR": (2, 2),
    "PO": (1, 1),
    "P": (1, 1),
}

#: Search words for the owner's ladder, to the rank each step spans:
#: BU is 60-62, BU+ 63-64, BU++ 65-66 (decision 3).
_LADDER_STEPS = ((60, 62), (63, 64), (65, 66))
_LADDER_WORDS = {"UNC": None, "BU": None, "CHOICE": 1, "GEM": 2}

#: Words that name a strike rather than a grade.
_STRIKE_WORDS = {"PROOF": "PR", "PR": "PR", "PF": "PR", "SP": "SP", "SPECIMEN": "SP"}

#: A number search term, optionally prefixed: 55, 55+, 55%, MS65, PR69%.
_TERM = re.compile(r"^([A-Z]{0,2})-?(\d{1,2})(\+|%)?$")

#: Not a restriction on the prefix at all.
ANY_PREFIX = "*"


@dataclass(frozen=True)
class GradeTerm:
    """What a grade search term matches.

    ``low`` and ``high`` bound the grade rank, inclusive. ``plus`` narrows to
    plus grades (True) or plain ones (False); None takes both. ``prefix`` is
    the strike prefix the item shows -- ``PR``, ``SP`` -- with None meaning
    none (a business strike, SMS, or no strike recorded) and ``ANY_PREFIX``
    meaning no restriction. ``code`` matches a grade with no number exactly.
    """

    low: float | None = None
    high: float | None = None
    plus: bool | None = None
    prefix: str | None = ANY_PREFIX
    code: str | None = None


def _rank_span(low: int, high: int) -> tuple[float, float]:
    """Ranks from ``low`` up to and including ``high+``."""
    return float(low), high + 0.5


def search_term(text: str) -> GradeTerm | None:
    """What a person typed in the grade filter, as a range; None if unknown.

    A number is exact -- ``55`` is 55 and not 55+ -- and ``%`` widens it to
    both: ``55%`` is 55 and 55+. The owner's ladder words are steps: ``BU``
    is 60-62, ``BU+`` 63-64, ``BU++`` 65-66, and ``BU%`` all three. Any other
    word is its whole range -- ``AU`` is 50 to 58+ -- and a plus after it
    narrows to the plus grades: ``AU+`` is 50+ to 58+.
    """
    code = text.strip().upper().replace(" ", "_").replace("-", "")
    if not code:
        return None
    if code in UNNUMBERED:
        return GradeTerm(code=code)
    if _NOTE.match(code):
        code = code[1:]
    code = code.removeprefix("N_")

    if match := _TERM.match(code):
        head, number, mark = match.groups()
        value = int(number)
        plus = None if mark == "%" else mark == "+"
        low = value + (0.5 if plus else 0.0)
        high = value + (0.0 if plus is False else 0.5)
        if not head:
            return GradeTerm(low, high, plus)
        if head in _STRIKE_WORDS:
            return GradeTerm(low, high, plus, _STRIKE_WORDS[head])
        if head in _PREFIX_RANGES:
            floor, top = _rank_span(*_PREFIX_RANGES[head])
            if not floor <= low <= high <= top:
                return None  # MS55: no mint-state grade is 55
            return GradeTerm(low, high, plus, None)
        return None

    wild = code.endswith("%")
    base = code.rstrip("%")
    word = base.rstrip("+")
    pluses = len(base) - len(word)
    word = word.removesuffix("_UNC").removesuffix("_BU")
    if word in _LADDER_WORDS:
        step = _LADDER_WORDS[word]
        if step is not None:
            if pluses:
                return None
            low, high = _rank_span(*_LADDER_STEPS[step])
        elif wild:
            low, high = _rank_span(_LADDER_STEPS[0][0], _LADDER_STEPS[-1][1])
        elif pluses > 2:
            return None
        else:
            low, high = _rank_span(*_LADDER_STEPS[pluses])
        return GradeTerm(low, high, None, None)
    if word in _STRIKE_WORDS and not pluses:
        return GradeTerm(prefix=_STRIKE_WORDS[word])
    if word in _PREFIX_RANGES and pluses <= 1:
        low, high = _rank_span(*_PREFIX_RANGES[word])
        return GradeTerm(low, high, True if pluses and not wild else None, None)
    return None


def business_prefix(number: int) -> str:
    """MS, AU, XF ... for a business strike of this number."""
    for floor, prefix in _BUSINESS_PREFIXES:
        if number >= floor:
            return prefix
    return "PO"


def display(
    prefix: str | None,
    suffix: str | None,
    numeric_value: int | None,
    is_plus: bool,
    label: str,
    *,
    sheldon: bool,
) -> str:
    """The grade as collectors write it: MS65, PR69+, PR70 Reverse Proof.

    Mirrors the database's ``grade_display()``. A grade that is not a Sheldon
    number (a note grade, Circulated) shows its own label.
    """
    if not sheldon or numeric_value is None:
        return label
    head = prefix or business_prefix(numeric_value)
    text = f"{head}{numeric_value}{'+' if is_plus else ''}"
    return f"{text} {suffix}" if suffix else text


def split_fields(
    grade: str | None, strike_type: str | None
) -> tuple[str | None, str | None]:
    """A client's grade and strike type, with a compound grade taken apart.

    ``("MS65", None)`` becomes ``("65", "business")``. A strike type the
    client names wins over the one the grade implies. A grade :func:`split`
    does not recognise is passed through for the code lookup to judge.
    """
    parts = split(grade) if grade else None
    if parts is None:
        return grade, strike_type
    return parts.grade, strike_type or parts.strike_type


def display_item(item: InventoryItem) -> str | None:
    """An inventory item's grade as collectors write it, or None if ungraded."""
    grade = item.grade
    if grade is None:
        return None
    strike = item.strike_type
    scale = grade.grade_scale
    return display(
        strike.prefix if strike else None,
        strike.suffix if strike else None,
        grade.numeric_value,
        grade.is_plus,
        grade.label,
        sheldon=scale is not None and scale.code == "sheldon",
    )


#: ``grade_display()`` in SQL, created by the migration that split grades.
GRADE_DISPLAY_SQL = """
CREATE OR REPLACE FUNCTION grade_display(
    strike_prefix text,
    strike_suffix text,
    numeric_value integer,
    is_plus boolean,
    label text,
    sheldon boolean
) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN NOT sheldon OR numeric_value IS NULL THEN label
        ELSE COALESCE(strike_prefix, CASE
                WHEN numeric_value >= 60 THEN 'MS'
                WHEN numeric_value >= 50 THEN 'AU'
                WHEN numeric_value >= 40 THEN 'XF'
                WHEN numeric_value >= 20 THEN 'VF'
                WHEN numeric_value >= 12 THEN 'F'
                WHEN numeric_value >= 8 THEN 'VG'
                WHEN numeric_value >= 4 THEN 'G'
                WHEN numeric_value = 3 THEN 'AG'
                WHEN numeric_value = 2 THEN 'FR'
                ELSE 'PO'
            END)
            || numeric_value::text
            || CASE WHEN is_plus THEN '+' ELSE '' END
            || COALESCE(' ' || strike_suffix, '')
    END
$$
"""
