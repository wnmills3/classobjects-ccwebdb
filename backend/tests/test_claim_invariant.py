"""Proof that the suite-wide claim check can actually fail.

Without this, a fixture that silently passed on every input would look
exactly like a fixture that works.

The naive form the brief suggested -- set the state wrong, assert the
now-obvious fact about it, and mark the whole test ``xfail(strict=True)`` --
does not work, and the difference matters. Confirmed by hand while writing
this: with the claim left broken at the end of the test body, the *test
itself* passes (nothing in the body raises; a plain assertion that the state
changed is simply true) and the *autouse* ``_claim_invariant`` fixture's
teardown is what raises, reported as a separate ``ERROR`` on the test rather
than a ``FAILED``. Applying ``xfail(strict=True)`` to that version does not
turn the run green: pytest evaluates ``xfail`` per phase, and a *call* phase
that raises nothing is an unexpected pass, which ``strict`` promotes to a
hard failure -- the teardown's own raise gets quietly reclassified as
``xfailed`` alongside it, but the strict-XPASS failure on the call phase
still fails the suite.

The fix is to make the call phase itself raise the same assertion, by calling
the fixture's own check function directly rather than only setting up the
conditions for the fixture to find it later. Then both phases raise -- the
call, from this test's own call; the teardown, from the autouse fixture
finding the same still-broken claim (nothing here restores it, and nothing
needs to: the per-test transaction rolls it back regardless) -- and ``xfail``
marks both as expected. Nothing unexpectedly passes, so ``strict`` has
nothing to promote, and the run is `xfailed` rather than `failed`, forever.
``raises=ClaimInvariantViolation`` narrows the mark so a genuine, unrelated
crash in either phase still fails the suite instead of being swallowed as
"expected". The first draft tried to narrow further with
``xfail(match=...)``, on the assumption that ``xfail`` works like
``pytest.raises`` -- it does not: ``match`` is not a parameter ``xfail``
accepts at all, and mypy refused the call outright (`Unexpected keyword
argument "match"`) before this was ever run. `raises=<type>` is the only
narrowing lever ``xfail`` actually has, so `check_claim_invariant` raises a
dedicated `ClaimInvariantViolation` (a plain `AssertionError` subclass, see
its docstring in conftest.py) instead of a bare `AssertionError`, and that
type is what gets narrowed on here.

Mutation-tested by hand: commenting out the `claim.state = ...` line below
makes this test XPASS(strict) and fail the run, confirming the proof is not
vacuous.

One thing this file's first test does *not* prove, despite appearances: that
the autouse fixture exists and is wired up. A standalone probe confirmed that
a test whose body raises and whose teardown does not still reports `1
xfailed` and exits 0 -- so if `_claim_invariant` were deleted from
conftest.py entirely, the test below would stay exactly as green as it is
now, because `check_claim_invariant` is called directly in the *call* phase
regardless of whether any fixture also calls it in teardown afterward.
Nothing here asserted there would be a second `xfailed` phase; there just
happened to be one. `test_the_invariant_fixture_is_wired_up`, below, is what
actually proves the fixture (not just the function it calls) is part of the
suite.
"""

from __future__ import annotations

import pytest
from app.models import ClaimState, Listing, OfferClaim
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import ClaimInvariantViolation, check_claim_invariant


@pytest.mark.xfail(
    strict=True,
    raises=ClaimInvariantViolation,
    reason=(
        "proof that the claim invariant can fail: breaking it here must "
        "raise both in this call and in the autouse fixture's teardown"
    ),
)
def test_the_invariant_fixture_catches_a_disagreement(
    db: Session, ebay_listing: Listing
) -> None:
    """Writing a claim around `offering_writes` is what this must catch.

    Deliberately left broken: the per-test transaction (`conftest.db`) rolls
    everything back afterward, so nothing here leaks into the next test.

    `raises=ClaimInvariantViolation` on the marker narrows it to *this*
    check's own exception type, so an unrelated `AssertionError` raised in
    either phase (a real bug in the fixture's own plumbing, say) is not
    quietly absorbed as "expected."
    """
    claim = db.scalar(
        select(OfferClaim).where(OfferClaim.listing_id == ebay_listing.id)
    )
    assert claim is not None
    claim.state = ClaimState.released  # the listing is still active
    db.flush()
    # Raises ClaimInvariantViolation -- the exact check `_claim_invariant`
    # runs in teardown, which will also find this claim still broken and
    # raise again.
    check_claim_invariant(db)


def test_the_invariant_fixture_is_wired_up(request: pytest.FixtureRequest) -> None:
    """The autouse fixture, not just the function it calls, must exist.

    `request.fixturenames` lists every fixture active for this test,
    including autouse ones the test never named -- which is exactly what
    distinguishes this from the test above. Deleting `_claim_invariant` from
    conftest.py (or its `autouse=True`) leaves `check_claim_invariant`
    itself untouched, so the test above would stay green; only a check
    against `request.fixturenames` actually depends on the fixture being
    part of the suite.

    Mutation-tested by hand: commenting out `_claim_invariant`'s
    `@pytest.fixture(autouse=True)` decorator in conftest.py makes this test
    fail with `_claim_invariant` missing from `fixturenames`, confirming the
    proof is not vacuous. Restored afterward.
    """
    assert "_claim_invariant" in request.fixturenames
