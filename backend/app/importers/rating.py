"""Reading a rating: the facts a condition string carries.

A rating is the owner's text about an item -- `PR69DCAM PCGS`, `MS70 NGC
First Release`, `69 PCGS`, `Genuine`. This module reads it into separate
facts: a grade, a strike type, a designation, a grader, attributes. The
importer (`app.importers.loader`) and the re-derivation pass over stored
items (`app.rating_pass`) both use it, so an item reads the same whichever
path it took. docs/specs/item-attributes-design.md, section 4.

Every rule keeps the parser's discipline: a match only inside the service's
published range, a missing prefix never guessed when nothing settles it, and
every miss visible rather than defaulted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "BareGrade",
    "ParsedCondition",
    "bare_grade",
    "designation_for",
    "note_grade_code",
    "parse_condition",
]


@dataclass(frozen=True)
class ParsedCondition:
    """The separate facts a condition string was carrying."""

    grade: str | None = None
    designation: str | None = None
    service: str | None = None
    catalog_number: str | None = None
    #: A banknote's treasury seal colour, when the value named one.
    seal_color: str | None = None
    #: item_attribute codes -- features of the note, which are not grades.
    note_attributes: tuple[str, ...] = ()
    #: A strike the rating names outright, which a grade prefix cannot say:
    #: "Reverse PF70" is a reverse proof, "MS69 Rev Proof" too.
    strike_type: str | None = None
    #: A Sheldon number written with no prefix, beside a designation or a
    #: grader ("69 PCGS", "70DCAM"). Its strike is decided by `bare_grade`.
    bare_number: str | None = None
    #: item_attribute codes for any kind of item: release pedigrees, CAC,
    #: No Motto, Genuine.
    attributes: tuple[str, ...] = ()
    #: An authenticity the rating states: a Genuine holder is `genuine`.
    authenticity: str | None = None


#: Valid Sheldon numbers for each prefix. A grade is a point on a scale, not
#: any letter followed by any number: without this, prose yields "F73" and
#: "G63" and they become permanent rows in a shared vocabulary.
_GRADE_RANGE: dict[str, tuple[int, int]] = {
    "MS": (60, 70),
    "PR": (60, 70),
    "SP": (50, 70),
    "AU": (50, 58),
    "XF": (40, 45),
    "VF": (20, 35),
    "F": (12, 15),
    "VG": (8, 10),
    "G": (4, 6),
    "AG": (3, 3),
    "FR": (2, 2),
    "P": (1, 1),
}

#: "P70" is a proof, as PCGS's own shorthand writes it; "P1" is Poor. The
#: numbers do not overlap, so the number says which.
_P_PROOF = (60, 70)

#: Seal colours, which belong on currency_detail rather than in a grade.
_SEAL_COLORS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{name}\s+SEAL\b", re.I), name.lower())
    for name in ("BLUE", "RED", "BROWN", "GREEN", "GOLD")
)

#: Banknote features. Attributes of the note, never of its condition -- putting
#: these in the grade column is what makes condition unqueryable.
_NOTE_ATTRIBUTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bSTAR\s*NOTE\b|\bSTAR\b", re.I), "star"),
    (re.compile(r"\bFANCY\s*SERIAL\b", re.I), "fancy_serial"),
    (re.compile(r"\bCONSECUTIVE\b", re.I), "consecutive"),
    (re.compile(r"\bLOW\s*SERIAL\b", re.I), "low_serial"),
    (re.compile(r"\bSOLID\b", re.I), "solid_serial"),
    (re.compile(r"\bRADAR\b", re.I), "radar"),
    (re.compile(r"\bREPEATER\b", re.I), "repeater"),
    (re.compile(r"\bBINARY\b", re.I), "binary"),
    (re.compile(r"\bBIRTHDAY\b", re.I), "birthday"),
    (re.compile(r"\bWEB\s*PRESS\b", re.I), "web_press"),
)

#: Attributes of any item. Each service's own release pedigree is its own
#: attribute, since each defines its own window; First Strike is never
#: shortened to FS, which on a label means Full Steps.
_ATTRIBUTES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bFIRST\s*STRIKE\b", re.I), "first_strike"),
    (re.compile(r"\bEARLY\s*RELEASES?\b", re.I), "early_releases"),
    (re.compile(r"\bFIRST\s*RELEASES?\b", re.I), "first_releases"),
    (
        re.compile(r"\bFIRST\s*DAY\s*(?:OF\s*)?ISSUE\b|\bFDO?I\b", re.I),
        "first_day_of_issue",
    ),
    (re.compile(r"\bFIRST\s*PRINT\b", re.I), "first_print"),
    (re.compile(r"\bNO\s*(?:GOD|MOTTO)\b|\bGODLESS\b", re.I), "no_motto"),
    (re.compile(r"\bGENUINE\b", re.I), "genuine"),
)

#: CACG's own label: only CAC's grading service writes it, so a rating
#: carrying it was graded by CACG, not stickered (decision 4).
_CACG_LABEL = re.compile(r"\bFIRST\s*(?:DAY\s*OF\s*)?DELIVERY\b", re.I)
_CACG = re.compile(r"\bCACG\b", re.I)
_CAC_GOLD = re.compile(r"\bCAC\s*GOLD\b|\bGOLD\s*CAC\b", re.I)
_CAC = re.compile(r"\bCAC\b", re.I)

#: A strike the rating names in words. Enhanced first: it is also a reverse
#: proof, and the more specific one is the one meant.
_STRIKES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bENHANCED\s*REV(?:ERSE)?\.?\s*(?:PR|PF|PROOF)", re.I),
        "enhanced_reverse_proof",
    ),
    (re.compile(r"\bREV(?:ERSE)?\.?\s*(?:PR|PF|PROOF)", re.I), "reverse_proof"),
)


#: An owner-assigned number, written as "#123" at the start of the value.
_CATALOG_NO = re.compile(r"#\s*(\d+)")

#: A Sheldon-style grade: a letter prefix, a number, optional plus signs.
#: EF is the British spelling of XF and normalises onto it.
_NUMERIC_GRADE = re.compile(
    # `\s*(?:-\s*)?` rather than `\s*-?\s*`: two optional whitespace runs
    # back to back are ambiguous, so a long run of spaces that never
    # completes a match backtracks super-linearly. This form accepts the
    # same strings with no ambiguity.
    r"\b(MS|PR|PF|SP|AU|XF|EF|VF|VG|AG|FR|F|G|P)\s*(?:-\s*)?(\d{1,2})(\+*)",
    re.I,
)

#: Adjectival grades, longest first so "GEM BU" wins over a bare "BU" and
#: "GEM PROOF" wins over a bare "PROOF".
_ADJECTIVAL: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bCHOICE[\s/_-]*PROOF\b", re.I), "CHOICE_PROOF"),
    (re.compile(r"\bGEM[\s/_-]*PROOF\b", re.I), "GEM_PROOF"),
    (re.compile(r"\bCHOICE[\s/_-]*BU\b", re.I), "CHOICE_BU"),
    (re.compile(r"\bCHOICE[\s/_-]*UNC\b", re.I), "CHOICE_UNC"),
    (re.compile(r"\bGEM[\s/_-]*BU\b", re.I), "GEM_BU"),
    (re.compile(r"\bGEM[\s/_-]*UNC\b", re.I), "GEM_UNC"),
    (re.compile(r"\bPROOF\b", re.I), "PROOF"),
    (re.compile(r"\bBU\b", re.I), "BU"),
    (re.compile(r"\bUNC\b", re.I), "UNC"),
    (re.compile(r"\bCIRC\b", re.I), "CIRC"),
    # Bare letter grades, with no Sheldon number attached. Last, so an
    # "AU-55" is read as AU55 rather than collapsing onto plain AU.
    (re.compile(r"\bAU\b", re.I), "AU"),
    (re.compile(r"\b(?:XF|EF)\b", re.I), "XF"),
    (re.compile(r"\bVF\b", re.I), "VF"),
    (re.compile(r"\bVG\b", re.I), "VG"),
)

#: The graders a designation may run straight into, as "PR70DCAMPCGS" does.
_SERVICES = "PCGS|NGC|ANACS|ICG|PMG|SEGS|CACG"

#: Designations. Longest first, so DCAM is not read as CAM and FBL not as
#: FB. Ultra Cameo, UCAM and DPL are NGC's words, which the vocabulary holds
#: as aliases (of DCAM and DMPL).
_DESIGNATION_WORDS = (
    r"DCAM|DMPL|UCAM|ULTRA\s*CAMEO|CAM|DPL|RD|RB|BN|FBL|FS|FB|FH|FT|PL|EPQ|PPQ"
)

# No leading word boundary: in "PR69DCAM" the designation follows a digit, and
# \b never fires between two word characters. A negative lookbehind for a
# letter is the precise rule -- it still refuses to find CAM inside SCAM.
_DESIGNATION = re.compile(
    # The flag is scoped to the alternation instead of the whole pattern:
    # under a global re.I the lookbehind's [A-Za-z] is a duplicated class,
    # because each half already matches either case. Note (?i:...) does not
    # capture, so it sits inside a capturing group -- the caller reads
    # .group(1).
    rf"(?<![A-Za-z])((?i:{_DESIGNATION_WORDS}))(?=\b|(?i:{_SERVICES})\b)"
)
#: NGC's Jefferson nickel steps. Not after a digit: "66FS" is 66 Full Steps,
#: not 6FS.
_STEPS = re.compile(r"(?<![A-Za-z0-9])([56]FS)\b", re.I)
_ULTRA_CAMEO = re.compile(r"ULTRA\s*CAMEO", re.I)
#: A grader, also where it follows a number or a designation with no space.
_SERVICE = re.compile(
    rf"(?:(?<![A-Za-z])|(?<=CAM)|(?<=DPL)|(?<=PL))((?i:{_SERVICES}))\b"
)

#: A Sheldon number with no prefix, standing directly before a designation
#: or a grader: "69 PCGS", "70DCAM PCGS", "66RB PCGS".
_BARE_NUMBER = re.compile(
    rf"(?<![A-Za-z0-9.$#/])(\d{{1,2}})(\+*)\s*"
    rf"(?=((?i:{_DESIGNATION_WORDS}|{_SERVICES}))(?i:{_SERVICES})?(?![A-Za-z]))"
)
#: A copper's colour follows a Mint State number; a smaller number before RD
#: is an ordinal ("3RD").
_COLOURS = frozenset({"RD", "RB", "BN"})
_COLOUR_FROM = 60

#: PR and PF are the same thing written two ways; so are XF and EF.
_GRADE_PREFIX_ALIAS = {"PF": "PR", "EF": "XF"}

#: The points PMG and PCGS both grade paper money on, Good 4 to 70. A number
#: outside this set is not a note grade: "UNC 5 2s" is five $2 notes.
_NOTE_NUMBERS = frozenset(
    {4, 6, 8, 10, 12, 15, 20, 25, 30, 35, 40, 45, 50, 53, 55, 58, *range(60, 71)}
)

#: A number set against a paper-quality designation or a grader, the way PMG
#: and PCGS labels write it: "64 EPQ", "50 PPQ", "12 PCGS".
_NOTE_NUMBER_BEFORE = re.compile(r"(?<![\d$.])(\d{1,2})\s*(?:EPQ|PPQ|PMG|PCGS)\b", re.I)
#: A number following a grade word: "UNC 64", "Gem Unc 65", "Very Fine 30".
_NOTE_NUMBER_AFTER = re.compile(
    r"\b(?:UNC|UNCIRCULATED|GEM|CHOICE|AU|XF|EF|VF|VG|FINE|GOOD)\s*(?:-\s*)?(\d{1,2})\b",
    re.I,
)

#: A note grade written with no number. Most specific first: About
#: Uncirculated before Uncirculated, Very Fine before Fine.
_NOTE_TERMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bGEM\s*(?:UNC|UNCIRCULATED|BU|CU)\b", re.I), "N_GEM_UNC"),
    (re.compile(r"\bCHOICE\s*(?:UNC|UNCIRCULATED|BU|CU)\b", re.I), "N_CHOICE_UNC"),
    (re.compile(r"\bABOUT\s*UNC(?:IRCULATED)?\b|\bAU\b", re.I), "N_AU"),
    (re.compile(r"\b(?:UNC|UNCIRCULATED|BU|CU)\b", re.I), "N_UNC"),
    (re.compile(r"\bEXTREMELY\s*FINE\b|\b(?:XF|EF)\b", re.I), "N_XF"),
    (re.compile(r"\bVERY\s*FINE\b|\bVF\b", re.I), "N_VF"),
    (re.compile(r"\bVERY\s*GOOD\b|\bVG\b", re.I), "N_VG"),
    (re.compile(r"\bFINE\b", re.I), "N_F"),
    (re.compile(r"\bGOOD\b", re.I), "N_G"),
)


def note_grade_code(text: str, sheldon: str | None) -> str | None:
    """The note-scale grade code a rating names, or None when it names none.

    A number wins over a bare term, and only a number on the scale counts --
    from a coin-style grade ("VF-30", PCGS's "MS65 PPQ"), from beside a
    designation or grader ("64 EPQ"), or after a grade word ("UNC 64").
    """
    candidates = re.findall(r"\d{1,2}", sheldon) if sheldon else []
    candidates += _NOTE_NUMBER_BEFORE.findall(text)
    candidates += _NOTE_NUMBER_AFTER.findall(text)
    for number in candidates:
        if int(number) in _NOTE_NUMBERS:
            return f"N{int(number)}"
    for pattern, code in _NOTE_TERMS:
        if pattern.search(text):
            return code
    return None


def _designation(value: str) -> str | None:
    steps = _STEPS.search(value)
    if steps:
        return steps.group(1).upper()
    found = _DESIGNATION.search(value)
    if not found:
        return None
    word = found.group(1)
    # Written as the vocabulary's alias spells it, so the lookup finds it.
    return "Ultra Cameo" if _ULTRA_CAMEO.fullmatch(word) else word.upper()


def _grade(value: str) -> str | None:
    for match in _NUMERIC_GRADE.finditer(value):
        prefix = match.group(1).upper()
        prefix = _GRADE_PREFIX_ALIAS.get(prefix, prefix)
        number = int(match.group(2))
        if prefix == "P" and _P_PROOF[0] <= number <= _P_PROOF[1]:
            prefix = "PR"
        low, high = _GRADE_RANGE.get(prefix, (0, 0))
        # Out-of-range means this was prose that happened to look like a
        # grade, not a grade. Keep scanning; a real one may follow.
        if low <= number <= high:
            return f"{prefix}{number}{match.group(3)}"

    for pattern, code in _ADJECTIVAL:
        if found := pattern.search(value):
            # A trailing '+' is a real distinction and is preserved.
            tail = value[found.end() : found.end() + 2]
            plus = "".join(c for c in tail if c == "+")
            return code + plus
    return None


def _bare_number(value: str) -> str | None:
    for match in _BARE_NUMBER.finditer(value):
        number = int(match.group(1))
        if match.group(3).upper() in _COLOURS and number < _COLOUR_FROM:
            continue
        if 1 <= number <= 70:
            return f"{number}{'+' if match.group(2) else ''}"
    return None


def _service_and_attributes(value: str) -> tuple[str | None, tuple[str, ...]]:
    attributes = [code for pattern, code in _ATTRIBUTES if pattern.search(value)]
    service = _SERVICE.search(value)
    service_code = service.group(1).upper() if service else None
    if _CACG.search(value) or (_CAC.search(value) and _CACG_LABEL.search(value)):
        # Graded by CAC: the grader is CACG, and First Delivery is its label.
        service_code = "CACG"
        if _CACG_LABEL.search(value):
            attributes.append("first_delivery")
    elif _CAC_GOLD.search(value):
        attributes.append("cac_gold")
    elif _CAC.search(value):
        attributes.append("cac")
    return service_code, tuple(attributes)


def parse_condition(text: str) -> ParsedCondition:
    """Decompose a condition string into the facts it actually carries.

    A real collection writes things like ``#14 PR69DCAM``, ``MS70 American
    Bald Eagle`` and ``Clad Roosevelt Gem Proof``. Each is a grade plus
    description, sometimes plus a designation, a grading service and the
    owner's own catalogue number.

    Extracting rather than accepting-or-rejecting the whole string matters in
    both directions. Rejecting wholesale leaves nearly half the collection
    with no grade at all. Accepting wholesale invents grades called
    ``ACADIANP`` and puts them in a vocabulary meant to be shared with other
    installations.

    ``grade`` is None when nothing grade-shaped is present, which is a real
    answer: the caller keeps the original text and flags it for a human.
    """
    value = str(text).strip()
    if not value:
        return ParsedCondition()

    catalog = _CATALOG_NO.search(value)
    grade = _grade(value)
    service, attributes = _service_and_attributes(value)
    strike = next((code for pattern, code in _STRIKES if pattern.search(value)), None)
    seal = next((c for p, c in _SEAL_COLORS if p.search(value)), None)

    return ParsedCondition(
        grade=grade,
        designation=_designation(value),
        service=service,
        catalog_number=catalog.group(1) if catalog else None,
        seal_color=seal,
        note_attributes=tuple(c for p, c in _NOTE_ATTRIBUTES if p.search(value)),
        strike_type=strike,
        bare_number=None if grade else _bare_number(value),
        attributes=attributes,
        authenticity="genuine" if "genuine" in attributes else None,
    )


# --- decisions that need more than the rating ---------------------------------

#: Designations only a proof carries, and ones only a business strike does.
_PROOF_DESIGNATIONS = frozenset({"DCAM", "CAM", "UCAM", "ULTRA CAMEO"})
_BUSINESS_DESIGNATIONS = frozenset({"PL", "DMPL", "DPL"})

#: A grade written in the listing text: "PCGS MS63", "ANACS PF67", "SP68".
_CONTEXT_GRADE = re.compile(
    r"\b(MS|PR|PF|SP|AU|XF|EF)\s*(?:-\s*)?(\d{1,2})(?!\d)", re.I
)
_CONTEXT_STRIKE = {"PR": "proof", "PF": "proof", "SP": "specimen"}
_PROOF_WORDS = re.compile(r"\bPROOF\b|\bPR\b|\bPF\b|\bCAMEO\b", re.I)
_BUSINESS_WORDS = re.compile(
    r"\bUNC(?:IRCULATED)?\b|\bBU\b|\bMS\b|\bMINT\s*STATE\b|\bBUSINESS\s*STRIKE\b",
    re.I,
)


@dataclass(frozen=True)
class BareGrade:
    """A bare number's grade and strike, and what settled the strike."""

    grade: str
    strike_type: str
    by: str


def bare_grade(parsed: ParsedCondition, context: str) -> BareGrade | None:
    """The grade a bare number means, or None when nothing settles its strike.

    `context` is the item's own text (description and title). The strike
    comes, in order, from the rating naming it, a proof-only or
    business-only designation, a grade with the same number in the text
    ("69 PCGS" beside "PCGS MS69"), or the text saying proof or uncirculated
    and not both. Otherwise it is left for a person -- a 69 is not MS69 by
    default.
    """
    if parsed.bare_number is None:
        return None
    number = parsed.bare_number
    if parsed.strike_type:
        return BareGrade(number, parsed.strike_type, "rating")
    designation = (parsed.designation or "").upper()
    if designation in _PROOF_DESIGNATIONS:
        return BareGrade(number, "proof", "designation")
    if designation in _BUSINESS_DESIGNATIONS:
        return BareGrade(number, "business", "designation")

    value = int(number.rstrip("+"))
    named = {
        _CONTEXT_STRIKE.get(match.group(1).upper(), "business")
        for match in _CONTEXT_GRADE.finditer(context)
        if int(match.group(2)) == value
    }
    if len(named) == 1:
        return BareGrade(number, named.pop(), "description grade")
    if named:
        return None

    # A grade with another number still says which strikes the text is
    # about: a lot described as "PF 68 ... Ms65" is both, and settles nothing.
    written = {
        _CONTEXT_STRIKE.get(match.group(1).upper(), "business")
        for match in _CONTEXT_GRADE.finditer(context)
    }
    proof = bool(_PROOF_WORDS.search(context)) or bool(written - {"business"})
    business = bool(_BUSINESS_WORDS.search(context)) or "business" in written
    if proof != business:
        return BareGrade(number, "proof" if proof else "business", "description")
    return None


_JEFFERSON = re.compile(r"\bJEFFERSON\b", re.I)


def designation_for(parsed: ParsedCondition, context: str) -> str | None:
    """The designation to record, after the rules the rating alone cannot apply.

    FS on a label is Full Steps only for a Jefferson nickel; on a Silver
    Eagle it is something else (PCGS's First Strike, or a Fivaz-Stanton
    variety) and is not recorded as a designation at all.
    """
    if parsed.designation == "FS" and not _JEFFERSON.search(context):
        return None
    return parsed.designation
