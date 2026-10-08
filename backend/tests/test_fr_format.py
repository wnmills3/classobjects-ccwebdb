"""The form of a Friedberg number: cleaned, then checked (`app.fr_format`).

The numbers here are forms only, chosen from no catalog: nothing in this file
says which note any of them belongs to.
"""

from __future__ import annotations

import pytest
from app.fr_format import fr_problem, fr_traits, normalize_fr


@pytest.mark.parametrize(
    ("typed", "kept"),
    [
        ("  9901-L ", "9901-L"),
        ("Fr. 9905-D", "9905-D"),
        ("FR#9905-d", "9905-D"),
        ("fr 12", "12"),
        ("FR-9905-D", "9905-D"),
        ("French", "French"),
        # The label comes off only before a digit 0 to 9.
        ("Fr. \uff19\uff19", "Fr. \uff19\uff19"),
        ("9907 - l", "9907-L"),
        ("9a", "9a"),
        ("9907-lm", "9907-Lm"),
        ("9907-EM", "9907-Em"),
        ("Fr. 9907-e*m", "9907-Em*"),
        ("9908-b lgs", "9908-B LGS"),
        ("9908-B   dgs ", "9908-B DGS"),
        ("9908-b* lgs", "9908-B* LGS"),
        ("9908-em lgs", "9908-Em LGS"),
    ],
)
def test_a_number_is_cleaned_before_it_is_kept(typed: str, kept: str) -> None:
    assert normalize_fr(typed) == kept


@pytest.mark.parametrize(
    ("typed", "kept"),
    [
        # Space of every kind round a hyphen, and runs of hyphens.
        ("", ""),
        ("   ", ""),
        ("-", "-"),
        (" - ", "-"),
        ("9907\t-\tl", "9907-L"),
        ("9907  -\n l", "9907-L"),
        ("9907\xa0-\u2003l", "9907-L"),
        ("9907 - - l", "9907--L"),
        ("9907--l", "9907--L"),
        ("- 9907", "-9907"),
        ("9907 -", "9907-"),
        ("99 07", "99 07"),
        # Where a seal shade is one: space of any kind, letters of any case.
        ("9908-b\tLgS", "9908-B LGS"),
        ("9908-b \n dGs", "9908-B DGS"),
        ("9908-b\xa0lgs", "9908-B LGS"),
        ("9908 - b  lgs", "9908-B LGS"),
        # No space before the letters: not a shade, and cleaned as a district.
        ("9908-blgs", "9908-BLGS"),
        ("lgs", "lgs"),
        ("9908-b gs", "9908-B GS"),
        # Only the letters at the very end are a shade.
        ("9908-b lgs dgs", "9908-B LGS DGS"),
        ("9908-b lgs x", "9908-B LGS X"),
        # The space before a digit after the label is any script's.
        ("Fr.\xa09907", "9907"),
    ],
)
def test_the_cleaning_at_its_edges(typed: str, kept: str) -> None:
    assert normalize_fr(typed) == kept


@pytest.mark.parametrize(
    "number",
    [
        "1",
        "9",
        "9901",
        "9901-L",
        "9901-A*",
        "12a",
        "12a-B",
        "9907-Em",
        "9907-Em*",
        "9901m",
        "9908-B LGS",
        "9908-B DGS",
        "9908-B* LGS",
        "9908-Em* DGS",
        "9908 LGS",
    ],
)
def test_a_well_formed_number_passes(number: str) -> None:
    assert fr_problem(number) is None


@pytest.mark.parametrize(
    ("number", "says"),
    [
        ("", "needed"),
        ("9907-", "hyphen"),
        ("99070-L", "5 digits"),
        ("9907-M", "form"),
        ("9907-LL", "form"),
        ("9907-Lmm", "form"),
        ("9907-mL", "form"),
        ("9908-B XGS", "form"),
        ("9908-BLGS", "form"),
        ("9908-B LGS*", "form"),
        ("L-9907", "form"),
        ("99 07", "form"),
        # Digits are 0 to 9 only, as the console's copy of the rule reads
        # them: full-width or other scripts' digits are not a number's.
        ("\uff19\uff19\uff10\uff17-L", "form"),
        ("٩٩٠٧", "form"),
    ],
)
def test_a_slip_is_named(number: str, says: str) -> None:
    problem = fr_problem(number)
    assert problem is not None
    assert says in problem


@pytest.mark.parametrize(
    ("number", "traits"),
    [
        ("9901-L", (False, False)),
        ("9901-L*", (True, False)),
        ("9901-Lm", (False, True)),
        ("9901-Lm*", (True, True)),
        ("9901m", (False, True)),
        ("12a", (False, False)),
        ("9901-L LGS", (False, False)),
        ("9901-L* LGS", (True, False)),
        ("9901-Lm DGS", (False, True)),
        ("9901-Lm* DGS", (True, True)),
    ],
)
def test_star_and_mule_are_read_from_the_number(
    number: str, traits: tuple[bool, bool]
) -> None:
    assert fr_traits(number) == traits
