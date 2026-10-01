"""The shape of a Friedberg number, checked where one is saved or confirmed.

Only the *form* is known here -- digits, an optional letter, a district, a
mule's `m`, a star, a seal shade -- never which number belongs to which
note: that mapping is the publisher's arrangement, which `CLAUDE.md` forbids
shipping. The form is what
catches a slip: `3007-` pasted for `3007-L` (owner's report, 2026-09-27), a
stray space, or a `Fr. ` prefix that one row of the catalog carried and the
rest did not.

`frontend/src/management/friedberg-format.js` applies the same rule as the
number is typed; this module is the one that holds.
"""

from __future__ import annotations

import re

__all__ = ["fr_problem", "fr_traits", "normalize_fr", "seal_shade"]

#: `Fr.`, `Fr#`, `FR-`, `Fr. #` -- a label for the number, not part of it --
#: taken off only where digits follow, so a word starting "fr" is left whole.
_PREFIX = re.compile(r"^fr[\s.#-]*(?=\d)", re.IGNORECASE)
#: Space around the hyphen: `3007 - L`.
_SPACED_HYPHEN = re.compile(r"\s*-\s*")
#: 1 to 4 digits, an optional letter (`1a`), a district `-A` to `-L`, `m` for
#: a mule (`3007-Em`; without a district the optional letter already holds
#: it), and `*` for a star note.
#: Then, after a space, `LGS` or `DGS` for a light or dark green seal --
#: `2008-B LGS` (owner, 2026-10-01). The seal is a catalog fact of its own,
#: so the two shades are already two types; the suffix keeps their numbers
#: apart.
_FORM = re.compile(r"^(?P<digits>\d+)[A-Za-z]?(?:-[A-L]m?)?\*?(?: (?:LGS|DGS))?$")
#: A seal shade typed at the end, any case and spacing: taken off before the
#: rest is cleaned, and put back as ` LGS` / ` DGS`.
_SHADE = re.compile(r"\s+(?P<shade>lgs|dgs)$", re.IGNORECASE)
#: A district with a mule's `m`, the star typed either side of it: kept as
#: `Em*`, the `m` lower-case so capitalising the district cannot turn it into
#: a second district letter.
_MULE_DISTRICT = re.compile(r"(?P<district>[a-l])(?P<a>\*?)m(?P<b>\*?)", re.IGNORECASE)


def normalize_fr(raw: str) -> str:
    """The number as it is kept: trimmed, unprefixed, its district in capitals."""
    number = raw.strip()
    shade = _SHADE.search(number)
    suffix = f" {shade['shade'].upper()}" if shade else ""
    if shade:
        number = number[: shade.start()]
    return _normalize_base(number) + suffix


def _normalize_base(raw: str) -> str:
    """The number before any seal shade, cleaned."""
    number = _SPACED_HYPHEN.sub("-", _PREFIX.sub("", raw.strip()))
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
