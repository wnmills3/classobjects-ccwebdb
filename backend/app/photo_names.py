"""Reading the item out of a photograph's filename.

`<item_code>_<nn>.<ext>` -- `CC-000412_01.jpg`. The convention is applied by
whoever takes the photographs; this reads it and never rewrites it.

**Nothing here repairs input.** A lowercase `cc-` is a miss, not a correction:
a parser that quietly fixes a filename teaches the operator that the
convention does not matter, and the next mistake is one nobody catches.

The sequence carries the role as well as the order, because the two common
shots are the two sides and photographing them front-then-back is what people
already do. Everything past the second is `unassigned`, to be set by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

__all__ = ["ParsedName", "parse"]

#: `item_code` is generated as `'CC-' || lpad(nextval(...), 6, '0')`, so the
#: shape is exact rather than a guess. Anchored at both ends: a name with
#: anything else around the code is not this convention.
_NAME = re.compile(r"^(CC-\d{6})_(\d{2,})\.[^.]+$")

#: What each position means. Beyond the second, a person decides.
_ROLES = {1: "obverse", 2: "reverse"}
_DEFAULT_ROLE = "unassigned"


@dataclass(frozen=True)
class ParsedName:
    """What a conforming filename says about the photograph."""

    item_code: str
    sequence: int
    #: An `image_role` code: obverse, reverse, or unassigned.
    role: str
    #: The first photograph is the one the shop shows.
    is_primary: bool


def parse(filename: str) -> ParsedName | None:
    """What the name says, or `None` if it does not follow the convention.

    Accepts a path as well as a bare name -- the pass walks a tree, and the
    directories above a photograph carry no meaning here.
    """
    # Both separators, because the library is read on Windows and the tests
    # build paths with either.
    name = PureWindowsPath(PurePosixPath(filename).name).name
    match = _NAME.match(name)
    if match is None:
        return None
    sequence = int(match.group(2))
    if sequence < 1:
        return None
    return ParsedName(
        item_code=match.group(1),
        sequence=sequence,
        role=_ROLES.get(sequence, _DEFAULT_ROLE),
        is_primary=sequence == 1,
    )
