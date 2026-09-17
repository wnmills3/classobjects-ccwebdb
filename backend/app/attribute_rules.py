"""Attributes that follow from a note's facts.

docs/specs/item-attributes-design.md, section 2, "Attributes from facts".
"In God We Trust" first appeared on paper money on some Series 1935G $1
Silver Certificates (BEP, bep.gov/currency/faqs), so:

| $1 Silver Certificate      | No Motto                                  |
|----------------------------|-------------------------------------------|
| Series 1928 through 1935F  | always -- derived, no evidence needed     |
| Series 1935G               | printed both ways: needs evidence         |
| Series 1935H, 1957 on      | never                                     |

"Godless" is kept to that $1 run (decision 5), which is where collectors
use the word. `app.classifier_defaults` applies the rules; what it adds is
a derived link (`derived_by` `attribute_rule`), which a person may remove
and which then stays removed.
"""

from __future__ import annotations

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


RULES: tuple[AttributeRule, ...] = (
    AttributeRule(
        attribute="no_motto",
        note_type="silver_certificate",
        face=Decimal("1"),
        always=((1928, ""), (1935, "F")),
        evidence=((1935, "G"), (1935, "G")),
    ),
)


def verdict(
    rule: AttributeRule,
    note_type: str | None,
    face: Decimal | None,
    series_year: int | None,
    series_letter: str | None,
) -> str | None:
    """ALWAYS, EVIDENCE or NEVER for this note; None where the rule says nothing.

    The rule speaks only about its own class and face value, and only when
    the series is known.
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
        return EVIDENCE
    return NEVER
