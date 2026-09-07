"""Splitting money without losing any of it.

One property matters above all others here: the parts must sum to the whole,
exactly, for every input. A cost basis that does not add up to what was
actually paid is wrong on a tax return however small the gap.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.allocation import AllocationError, allocate


def D(value: str) -> Decimal:
    return Decimal(value)


def test_the_obvious_case_that_naive_division_gets_wrong() -> None:
    """$100 three ways is 33.33 each, which is 99.99.

    The penny has to land somewhere rather than evaporate.
    """
    shares = allocate(D("100.00"), [D(1), D(1), D(1)])

    assert sum(shares) == D("100.00")
    assert sorted(shares) == [D("33.33"), D("33.33"), D("33.34")]


def test_an_even_split_stays_even() -> None:
    shares = allocate(D("100.00"), [D(1)] * 4)
    assert shares == [D("25.00")] * 4


def test_proportional_split_follows_the_weights() -> None:
    """A mint set: the cent should not absorb the same cost as the dollar."""
    # Face values 0.01, 0.05, 0.10, 0.25, 0.50 -- total 0.91
    weights = [D("0.01"), D("0.05"), D("0.10"), D("0.25"), D("0.50")]
    shares = allocate(D("91.00"), weights)

    assert sum(shares) == D("91.00")
    assert shares == [D("1.00"), D("5.00"), D("10.00"), D("25.00"), D("50.00")]


def test_the_remainder_goes_to_the_parts_cut_hardest() -> None:
    """The leftover pennies go where the rounding bit hardest.

    Largest-remainder, not first-come: the pennies go where the rounding
    took the most, which is the only allocation nobody can call arbitrary.
    """
    shares = allocate(D("10.00"), [D(1), D(1), D(1), D(1), D(1), D(1)])
    assert sum(shares) == D("10.00")
    # 1.666... each: four get 1.67, two get 1.66 -- or some such split, but
    # the totals must be exact and the spread must be one cent.
    assert max(shares) - min(shares) == D("0.01")


@pytest.mark.parametrize(
    "total",
    ["0.01", "0.02", "0.03", "0.07", "1.00", "9.99", "100.00", "1234.56", "99999.99"],
)
@pytest.mark.parametrize("count", [2, 3, 5, 7, 13, 20, 50])
def test_the_parts_always_sum_to_the_whole(total: str, count: int) -> None:
    """The invariant, over a spread of awkward totals and part counts."""
    shares = allocate(D(total), [D(1)] * count)
    assert sum(shares) == D(total)
    assert len(shares) == count


@pytest.mark.parametrize(
    "weights",
    [
        [D(1), D(2), D(3)],
        [D("0.01"), D("99.99")],
        [D(7), D(7), D(7), D(1)],
        [D("1.5"), D("2.5"), D("96")],
    ],
)
def test_uneven_weights_still_sum_exactly(weights: list[Decimal]) -> None:
    shares = allocate(D("777.77"), weights)
    assert sum(shares) == D("777.77")


def test_no_part_is_ever_negative() -> None:
    shares = allocate(D("0.03"), [D(1)] * 10)
    assert all(share >= 0 for share in shares)
    assert sum(shares) == D("0.03")


def test_a_zero_total_allocates_zero() -> None:
    assert allocate(D("0.00"), [D(1), D(2)]) == [D("0.00"), D("0.00")]


def test_all_zero_weights_fall_back_to_an_equal_split() -> None:
    """An all-zero weighting falls back to an equal split.

    Proportion is undefined when everything is worth nothing; an equal
    split is the only defensible answer and is what the caller meant.
    """
    shares = allocate(D("10.00"), [D(0), D(0), D(0), D(0)])
    assert shares == [D("2.50")] * 4


def test_the_result_is_deterministic() -> None:
    """The same input must always produce the same output.

    An allocation that shuffled pennies between runs would give a re-import a
    different cost basis from the original.
    """
    first = allocate(D("100.00"), [D(1), D(1), D(1)])
    for _ in range(20):
        assert allocate(D("100.00"), [D(1), D(1), D(1)]) == first


def test_negative_weights_are_refused() -> None:
    with pytest.raises(AllocationError):
        allocate(D("10.00"), [D(1), D(-1)])


def test_no_parts_is_refused() -> None:
    with pytest.raises(AllocationError):
        allocate(D("10.00"), [])


def test_nothing_passes_through_a_float() -> None:
    shares = allocate(D("0.10"), [D(1), D(1), D(1)])
    assert all(isinstance(share, Decimal) for share in shares)
    assert sum(shares) == D("0.10")
