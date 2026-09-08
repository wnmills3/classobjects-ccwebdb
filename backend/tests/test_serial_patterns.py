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
        ("A11112222B", {"binary", "double_quad", "fancy_serial"}),
        ("M88888888", {"solid_serial", "radar", "repeater", "fancy_serial"}),
        ("A12345678B", {"ladder", "fancy_serial"}),
        # Low and high are positional -- where the note sits in the print
        # run -- so they stack with the pattern designations rather than
        # replacing them.
        ("A00001234B", {"low_serial"}),
        ("A98765432B", {"high_serial", "ladder", "fancy_serial"}),
        (
            "A99999999B",
            {
                "high_serial",
                "solid_serial",
                "radar",
                "repeater",
                "fancy_serial",
            },
        ),
        ("A04301989B", {"birthday", "fancy_serial"}),
        ("A15141514B", {"trinary", "repeater", "fancy_serial"}),
        ("A25282252B", {"trinary", "fancy_serial"}),
        # Four of one digit then four of another. Binary too -- two distinct
        # digits -- but the arrangement is the thing collectors pay for.
        ("E00003333B", {"double_quad", "binary", "low_serial", "fancy_serial"}),
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


def test_an_internal_letter_is_refused_not_warned() -> None:
    """A letter among the digits is a typo every time.

    S97535476A entered as S9753547A6. Letters belong at the ends of a serial
    -- one or two in front, one behind -- so one in the middle cannot be what
    is printed on the note, and data entry refuses it rather than asking.
    """
    from app.serial_patterns import check

    issues = check("S9753547A6")
    assert issues[0].severity == "error"
    assert "transposition" in issues[0].message


def test_short_and_high_serials_only_warn() -> None:
    """Unusual is not impossible, and entry proceeds if the owner insists."""
    from app.serial_patterns import check

    short = check("E9801342C")
    assert [i.severity for i in short] == ["warning"]
    assert "only 7 digits" in short[0].message

    high = check("A99889530B", 2001)
    assert [i.severity for i in high] == ["warning"]
    assert "worth confirming" in high[0].message


def test_an_impossible_value_is_an_error() -> None:
    """Above 96 million is advisory; no eight-digit serial exceeds 99,999,999.

    The distinction matters: the print-run limit varies by series and this
    project has no sourced table of them, so the wording must not claim more
    than is known.
    """
    from app.serial_patterns import check

    assert check("A96000000B") == []
    assert check("A12345678B") == []


def test_ends_may_carry_letters_or_a_star() -> None:
    from app.serial_patterns import check

    for serial in ("A12345678B", "AB12345678C", "*12345678B", "A12345678*"):
        assert check(serial) == [], serial
