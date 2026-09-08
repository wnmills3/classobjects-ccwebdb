"""Fancy-serial designations derived from the serial number.

These are what a PMG or PCGS submission asks the sender to declare, and they
carry real premiums, so the risk runs both ways: a missed designation sells a
note too cheaply, and a wrong one is a claim the note cannot support.
"""

from __future__ import annotations

import pytest
from app.serial_patterns import analyse, is_incomplete


@pytest.mark.parametrize(
    ("serial", "expected"),
    [
        ("A12344321B", {"radar", "fancy_serial"}),
        ("A12341234B", {"repeater", "fancy_serial"}),
        ("A11112222B", {"binary", "fancy_serial"}),
        ("M88888888", {"solid_serial", "radar", "repeater", "fancy_serial"}),
        ("A12345678B", {"ladder", "fancy_serial"}),
        ("A00000059B", {"low_serial"}),
        ("A04301989B", {"birthday", "fancy_serial"}),
        ("A19472856B", set()),
    ],
)
def test_designations(serial: str, expected: set[str]) -> None:
    assert analyse(serial) == expected


def test_a_star_is_not_a_fancy_serial() -> None:
    """Two different questions on a grading form, and two different suffixes.

    Sweeping stars into `fancy_serial` labelled all 189 of this collection's
    star notes as fancy serials, which is a claim about their digits that
    their digits do not support.
    """
    plain_star = analyse("A19472856*")
    assert plain_star == {"star"}
    assert "fancy_serial" not in plain_star

    # A star note whose digits *are* patterned earns both, separately.
    both = analyse("A12344321*")
    assert {"star", "radar", "fancy_serial"} <= both


def test_short_serials_yield_no_pattern() -> None:
    """The eight-digit rule.

    "59" has two distinct digits, so a naive binary test calls it a binary
    note. It is a truncated field, not a note. 43 of this collection's serials
    are short, and reading patterns from them would invent designations.
    """
    for serial in ("59", "887", "1791040", "99V7711"):
        assert is_incomplete(serial)
        assert analyse(serial) - {"star"} == set()


def test_a_short_serial_still_shows_its_star() -> None:
    """The asterisk is not a digit pattern, so truncation does not hide it."""
    assert analyse("*59") == {"star"}


def test_consecutive_is_never_derived() -> None:
    """It describes a run of notes, which one serial cannot show.

    68 items carry it, recorded by a person who knew they bought a run. No
    single serial could establish that, so this must not try.
    """
    for serial in ("A12345678B", "A00000001B", "M88888888"):
        assert "consecutive" not in analyse(serial)


def test_empty_serial_is_silent() -> None:
    assert analyse("") == set()
