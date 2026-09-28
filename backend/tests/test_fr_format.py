"""The form of a Friedberg number: cleaned, then checked (`app.fr_format`).

The numbers here are forms only, chosen from no catalog: nothing in this file
says which note any of them belongs to.
"""

from __future__ import annotations

import pytest
from app.fr_format import fr_problem, normalize_fr


@pytest.mark.parametrize(
    ("typed", "kept"),
    [
        ("  9901-L ", "9901-L"),
        ("Fr. 9905-D", "9905-D"),
        ("FR#9905-d", "9905-D"),
        ("fr 12", "12"),
        ("FR-9905-D", "9905-D"),
        ("French", "French"),
        ("9907 - l", "9907-L"),
        ("9a", "9a"),
    ],
)
def test_a_number_is_cleaned_before_it_is_kept(typed: str, kept: str) -> None:
    assert normalize_fr(typed) == kept


@pytest.mark.parametrize(
    "number", ["1", "9", "9901", "9901-L", "9901-A*", "12a", "12a-B"]
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
        ("L-9907", "form"),
        ("99 07", "form"),
    ],
)
def test_a_slip_is_named(number: str, says: str) -> None:
    problem = fr_problem(number)
    assert problem is not None
    assert says in problem
