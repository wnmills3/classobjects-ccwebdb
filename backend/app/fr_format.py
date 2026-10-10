"""The shape of a Friedberg number, checked where one is saved or confirmed.

Only the *form* is known here -- digits, an optional letter, a district, a
mule's `m`, a star, a seal shade -- never which number belongs to which
note: that mapping is the publisher's arrangement, which
`docs/reference-data.md` forbids shipping. The form is what
catches a slip: `9907-` pasted for `9907-L`, a
stray space, or a `Fr. ` prefix that one row of the catalog carried and the
rest did not.

`frontend/src/management/friedberg-format.js` applies the same rule as the
number is typed; this module is the one that holds.
"""

from __future__ import annotations

import re

__all__ = [
    "DISTRICT_SQL_PATTERN",
    "fr_district",
    "fr_problem",
    "fr_traits",
    "normalize_fr",
    "seal_shade",
]

#: `Fr.`, `Fr#`, `FR-`, `Fr. #` -- a label for the number, not part of it --
#: taken off only where digits follow, so a word starting "fr" is left whole.
#: The digit is `0` to `9` only -- `(?a:...)` holds `\d` to ASCII without
#: narrowing the space before it, which stays any script's.
_PREFIX = re.compile(r"^fr[\s.#-]*(?=(?a:\d))", re.IGNORECASE)
#: 1 to 4 digits, an optional letter (`1a`), a district `-A` to `-L`, `m` for
#: a mule (`9907-Em`; without a district the optional letter already holds
#: it), and `*` for a star note.
#: Then, after a space, `LGS` or `DGS` for a light or dark green seal --
#: `9908-B LGS`. The seal is a catalog fact of its own,
#: so the two shades are already two types; the suffix keeps their numbers
#: apart. Digits are `0` to `9` only, here and in `_PREFIX`: without
#: `re.ASCII`, `\d` would take any script's, which the console's copy of the
#: rule refuses.
_FORM = re.compile(
    r"^(?P<digits>\d+)[A-Za-z]?(?:-(?P<district>[A-L])m?)?\*?(?: (?:LGS|DGS))?$",
    re.ASCII,
)
#: `_FORM`'s district for PostgreSQL, whose `substring(text, pattern)` gives
#: what the one capturing group matched: the same answer as `fr_district`,
#: for a search that has to read it from every row.
DISTRICT_SQL_PATTERN = r"^[0-9]+[A-Za-z]?-([A-L])m?[*]?(?: (?:LGS|DGS))?$"
#: The letters of a seal shade typed at the end, any case: with space before
#: them they are taken off before the rest is cleaned, and put back as
#: ` LGS` / ` DGS`.
_SHADE_LETTERS = re.compile(r"lgs|dgs", re.IGNORECASE)
#: A district with a mule's `m`, the star typed either side of it: kept as
#: `Em*`, the `m` lower-case so capitalising the district cannot turn it into
#: a second district letter.
_MULE_DISTRICT = re.compile(r"(?P<district>[a-l])(?P<a>\*?)m(?P<b>\*?)", re.IGNORECASE)


def _close_hyphens(text: str) -> str:
    """`text` with the space on either side of each hyphen taken out.

    `9907 - L` is `9907-L`: the space ending what comes before a hyphen and
    the space starting what follows it are not part of the number. Text with
    no hyphen is returned as it is.
    """
    parts = text.split("-")
    last = len(parts) - 1
    closed = []
    for at, part in enumerate(parts):
        if at > 0:
            part = part.lstrip()
        if at < last:
            part = part.rstrip()
        closed.append(part)
    return "-".join(closed)


def _shade_at_end(text: str) -> tuple[str, str] | None:
    """What comes before a seal shade typed at the end of `text`, and its letters.

    None when `text`, already trimmed, does not end with space and then LGS
    or DGS: the letters alone, or run on to what is before them, are no
    shade.
    """
    letters = text[-3:]
    if _SHADE_LETTERS.fullmatch(letters) is None:
        return None
    before = text[:-3]
    base = before.rstrip()
    if len(base) == len(before):
        return None
    return base, letters


def normalize_fr(raw: str) -> str:
    """The number as it is kept: trimmed, unprefixed, its district in capitals."""
    number = raw.strip()
    shade = _shade_at_end(number)
    if shade is None:
        return _normalize_base(number)
    base, letters = shade
    return f"{_normalize_base(base)} {letters.upper()}"


def _normalize_base(raw: str) -> str:
    """The number before any seal shade, cleaned."""
    number = _close_hyphens(_PREFIX.sub("", raw.strip()))
    head, hyphen, district = number.partition("-")
    if not hyphen:
        return number
    mule = _MULE_DISTRICT.fullmatch(district)
    if mule is not None:
        star = "*" if mule["a"] or mule["b"] else ""
        return f"{head}-{mule['district'].upper()}m{star}"
    return f"{head}-{district.upper()}"


def seal_shade(number: str) -> str | None:
    """`LGS` or `DGS` when `number` ends with a seal shade, else None."""
    match = re.search(r" (LGS|DGS)$", number)
    return match.group(1) if match else None


def fr_traits(number: str) -> tuple[bool, bool]:
    """Whether `number` is a star note's and whether it is a mule's.

    Read from the number alone, past any seal shade: a `*` ending the rest
    is a star note, an `m` before it (or ending the rest) a mule. These tell
    apart notes whose catalog facts are all the same -- a mule differs only in
    its plates, a star note only in its serial -- so they are part of a type's
    identity. The same rule, in SQL, generates `friedberg_number.is_star` and
    `is_mule`.
    """
    base = re.sub(r" (?:LGS|DGS)$", "", number)
    return base.endswith("*"), base.rstrip("*").endswith("m")


def fr_district(number: str) -> str | None:
    """The district letter `number` (already normalized) carries, or None.

    `9907-L` is district L's number whatever else is known of the note, so a
    row recorded from a note with no district on it still has one. None for
    a number without a district, and for text not in a number's form.
    `DISTRICT_SQL_PATTERN` is the same rule for a query.
    """
    match = _FORM.match(number)
    return match["district"] if match else None


def fr_problem(number: str) -> str | None:
    """Why `number` (already normalized) is not a Friedberg number, or None."""
    if not number:
        return "A Friedberg number is needed."
    if number.endswith("-"):
        return f"{number} ends with a hyphen: its district letter is missing."
    match = _FORM.match(number)
    if match is None:
        return (
            f"{number} is not in the form of a Friedberg number: digits, then an "
            "optional letter, then -A to -L for a district, then m for a mule, "
            "then * for a star note, then LGS or DGS for a seal shade."
        )
    digits = len(match["digits"])
    if digits > 4:
        return f"{number} has {digits} digits; a Friedberg number has 1 to 4."
    return None
