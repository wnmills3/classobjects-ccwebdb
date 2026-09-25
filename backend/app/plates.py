"""A banknote's plate numbers, and the printing location the face plate names.

The owner, 2026-09-25: the face and back plate numbers, with where the note
was printed, tell a Friedberg number apart -- a 2017-A $1 is Fr. 3005-A from
Washington, DC or Fr. 3006-A from Fort Worth -- and a mismatched face and back
is how a mule is found (the web search's answer carries an "m" suffix then).

- **Face plate**: a check letter and digits (``E82``), or digits alone on older
  notes (``153``). A note printed in Fort Worth has ``FW`` before it
  (``FW E82``); one printed in Washington has none.
- **Back plate**: digits.
- **Printing location**: ``dc`` or ``fw``. Read from the face plate when it is
  given -- ``FW`` means Fort Worth, no prefix Washington -- so the two cannot
  disagree; set by hand only where no face plate is recorded.

Facts of the note in hand, not a catalogue's arrangement (CLAUDE.md,
*Reference data*).
"""

from __future__ import annotations

import re

__all__ = [
    "FACILITIES",
    "back_plate",
    "face_plate",
    "facility_of",
]

#: The two places US currency is printed, by code, as a person reads them.
FACILITIES: dict[str, str] = {"dc": "Washington, DC", "fw": "Fort Worth, TX"}

_FACE = re.compile(r"^(FW)?\s*([A-Z]?)\s*(\d{1,5})$")
_BACK = re.compile(r"^\d{1,5}$")


def face_plate(text: str | None) -> str | None:
    """A face plate number as stored -- ``FW E82``, ``E82``, ``153`` -- or None.

    Raises ValueError, naming the shape, for anything else.
    """
    if text is None or not text.strip():
        return None
    match = _FACE.match(text.strip().upper())
    if match is None:
        raise ValueError(
            f"face plate {text!r}: a check letter and digits (E82), digits alone "
            "(153), with FW before them for a Fort Worth note (FW E82)"
        )
    prefix, letter, digits = match.groups()
    number = f"{letter}{digits}"
    return f"FW {number}" if prefix else number


def back_plate(text: str | None) -> str | None:
    """A back plate number as stored -- digits -- or None; ValueError otherwise."""
    if text is None or not text.strip():
        return None
    value = text.strip()
    if not _BACK.match(value):
        raise ValueError(f"back plate {text!r}: digits only, as printed (1234)")
    return value


def facility_of(face: str) -> str:
    """Where a note with this (stored) face plate number was printed."""
    return "fw" if face.startswith("FW") else "dc"
