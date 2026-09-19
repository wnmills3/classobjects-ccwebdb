"""The filename convention, case by case.

`<item_code>_<nn>.<ext>` -- CC-000412_01.jpg. A table rather than prose,
because a convention is only as good as the cases nobody remembered.
"""

from __future__ import annotations

import pytest
from app.photo_names import ParsedName, parse


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        (
            "CC-000412_01.jpg",
            ParsedName("CC-000412", 1, "obverse", True),
        ),
        (
            "CC-000412_02.jpg",
            ParsedName("CC-000412", 2, "reverse", False),
        ),
        (
            "CC-000412_03.jpg",
            ParsedName("CC-000412", 3, "unassigned", False),
        ),
        (
            "CC-000412_17.jpeg",
            ParsedName("CC-000412", 17, "unassigned", False),
        ),
        # A path, not a bare name: the pass walks a directory tree.
        (
            "box3/CC-000001_01.png",
            ParsedName("CC-000001", 1, "obverse", True),
        ),
        # The library is read on Windows, where the tree walk yields
        # backslash-separated paths.
        (
            "box3\\CC-000001_01.png",
            ParsedName("CC-000001", 1, "obverse", True),
        ),
    ],
)
def test_names_that_follow_the_convention(filename: str, expected: ParsedName) -> None:
    assert parse(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "cc-000412_01.jpg",  # lowercase prefix is a miss, not a correction
        "CC-00412_01.jpg",  # five digits
        "CC-0004123_01.jpg",  # seven digits
        "CC-000412.jpg",  # no sequence
        "CC-000412_1.jpg",  # sequence not zero-padded
        "CC-000412_00.jpg",  # sequence must start at 1
        "CC-000412-01.jpg",  # hyphen, not underscore
        "IMG_4021.jpg",  # a camera's own name
        "CC-000412_01",  # no extension
        "",
    ],
)
def test_names_that_do_not(filename: str) -> None:
    assert parse(filename) is None
