"""Attributes that follow from a note's facts.

docs/specs/item-attributes-design.md, section 2, "Attributes from facts".
"In God We Trust" first appeared on paper money on some Series 1935G $1
Silver Certificates (BEP, bep.gov/currency/faqs), so:

| $1 Silver Certificate      | No Motto                                  |
|----------------------------|-------------------------------------------|
| Series 1928 through 1935F  | always -- derived, no evidence needed     |
| Series 1935G               | by serial, else needs evidence            |
| Every other series         | never (before 1928, 1935H, 1957 on)       |

Series 1935G was printed without the motto until serial D 48960001 J, the
first with it, so a 1935G note's serial decides: one in the J blocks before
it (B-J, C-J, and D-J below it) has No Motto, one from it on has the motto.
A star note, or a serial that is not letter, eight digits, letter, still
needs evidence.

"Godless" is kept to that $1 run, which is where collectors
use the word. `app.classifier_defaults` applies the rules; what it adds is
a derived link (`derived_by` `attribute_rule`), which a person may remove
and which then stays removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "ALWAYS",
    "EVIDENCE",
    "NEVER",
    "RULE",
    "RULES",
    "AttributeRule",
    "verdict",
]

#: `item_attribute_link.derived_by` for the links these rules add.
RULE = "attribute_rule"

#: What the facts say about an attribute on one note.
ALWAYS = "always"
EVIDENCE = "evidence"
NEVER = "never"

#: A series, as it sorts: year, then letter ("" before "A").
Series = tuple[int, str]


@dataclass(frozen=True)
class AttributeRule:
    """One attribute, one class and face value, and its series ranges."""

    attribute: str
    note_type: str
    face: Decimal
    #: Inclusive ranges. A series in none of them is `NEVER`.
    always: tuple[Series, Series]
    evidence: tuple[Series, Series]
    #: Within `evidence`, the first serial without the attribute: a serial
    #: of the same suffix letter before it has it (ALWAYS), one from it on
    #: does not (NEVER). None: the serial says nothing.
    serial_cutoff: str | None = None


RULES: tuple[AttributeRule, ...] = (
    AttributeRule(
        attribute="no_motto",
        note_type="silver_certificate",
        face=Decimal("1"),
        always=((1928, ""), (1935, "F")),
        evidence=((1935, "G"), (1935, "G")),
        serial_cutoff="D48960001J",
    ),
)

#: A plain serial: prefix letter, eight digits, suffix letter. A star note's
#: replaces a letter with a star and is not read.
_SERIAL = re.compile(r"^([A-Z])(\d{8})([A-Z])$")


def _by_serial(cutoff: str, serial: str | None) -> str | None:
    """ALWAYS before the cutoff, NEVER from it on; None where it cannot say."""
    if serial is None:
        return None
    found = _SERIAL.match(serial.replace(" ", "").upper())
    limit = _SERIAL.match(cutoff)
    if found is None or limit is None or found[3] != limit[3]:
        return None
    return ALWAYS if (found[1], found[2]) < (limit[1], limit[2]) else NEVER


def verdict(
    rule: AttributeRule,
    note_type: str | None,
    face: Decimal | None,
    series_year: int | None,
    series_letter: str | None,
    serial_number: str | None = None,
) -> str | None:
    """ALWAYS, EVIDENCE or NEVER for this note; None where the rule says nothing.

    The rule speaks only about its own class and face value, and only when
    the series is known. In a series that needs evidence, the serial decides
    where the rule has a cutoff and the serial can be read against it.
    """
    if note_type != rule.note_type or face is None or face != rule.face:
        return None
    if series_year is None:
        return None
    series = (series_year, (series_letter or "").strip().upper())
    low, high = rule.always
    if low <= series <= high:
        return ALWAYS
    low, high = rule.evidence
    if low <= series <= high:
        if rule.serial_cutoff is not None:
            decided = _by_serial(rule.serial_cutoff, serial_number)
            if decided is not None:
                return decided
        return EVIDENCE
    return NEVER
