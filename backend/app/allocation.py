"""Splitting an amount of money across parts without losing any of it.

The obvious approach loses money. Dividing $100 three ways gives $33.33 each,
which totals $99.99 -- and the missing penny has to go *somewhere*, because a
cost basis that does not add up to what was actually paid is wrong on a tax
return regardless of how small the discrepancy is.

This uses the largest-remainder method: give every part its floor, then hand
the leftover pennies out one at a time to whichever parts were cut hardest.
The result sums to the original exactly, by construction.

Everything here is `Decimal`. A float cannot represent a cent exactly, so a
float allocation would drift before it even started.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

__all__ = ["AllocationError", "allocate"]

CENT = Decimal("0.01")


class AllocationError(ValueError):
    """The allocation cannot be performed as asked."""


def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` in proportion to `weights`, to the cent, losing nothing.

    Returns one amount per weight, summing to exactly `total`.

    Weights need no particular scale -- they are proportions. Equal weights
    give an equal split; weights of face or market value give a split in
    proportion to value, which is what splitting a mint set calls for: the
    cent should not absorb the same share of the price as the dollar.

    Ties in the remainder are broken by position, so the same input always
    produces the same output. That matters more than fairness between equal
    claimants: an allocation that shuffled pennies between runs would make a
    re-import produce different cost bases.
    """
    if not weights:
        raise AllocationError("nothing to allocate to")
    if any(w < 0 for w in weights):
        raise AllocationError("weights cannot be negative")

    total = Decimal(total).quantize(CENT)
    weight_sum = sum(weights, Decimal(0))

    if weight_sum == 0:
        # Every part is worth nothing, so proportion is undefined. Falling
        # back to an equal split is the only defensible answer, and is what
        # the caller meant if they passed all-zero values.
        weights = [Decimal(1)] * len(weights)
        weight_sum = Decimal(len(weights))

    # 1. Floor each share to the cent. Rounding down, never to nearest --
    #    rounding to nearest can overshoot the total, and there is no way to
    #    take a penny back that does not create a negative remainder.
    exact = [total * w / weight_sum for w in weights]
    shares = [value.quantize(CENT, rounding=ROUND_DOWN) for value in exact]

    # 2. Hand out what is left, one cent at a time, to the parts with the
    #    largest fractional remainder.
    shortfall = total - sum(shares, Decimal(0))
    pennies = int((shortfall / CENT).to_integral_value())

    if pennies:
        order = sorted(
            range(len(weights)),
            key=lambda i: (-(exact[i] - shares[i]), i),
        )
        for position in range(pennies):
            shares[order[position % len(order)]] += CENT

    # 3. The invariant this module exists for. An assertion rather than a
    #    comment, because a silent penny leak is exactly the kind of thing
    #    that survives review.
    if sum(shares, Decimal(0)) != total:
        raise AllocationError(
            f"allocation lost money: {sum(shares, Decimal(0))} != {total}"
        )

    return shares
