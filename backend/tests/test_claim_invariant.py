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

import inspect

import pytest
from app.models import ClaimState, Disposition, Listing, OfferClaim, SalesLotStatus
from app.references import require_code
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests import conftest
from tests.conftest import (
    ClaimInvariantViolation,
    DispositionInvariantViolation,
    LotInvariantViolation,
    check_claim_invariant,
    check_disposition_invariant,
    check_lot_invariant,
)


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


@pytest.mark.xfail(
    strict=True,
    raises=LotInvariantViolation,
    reason=(
        "proof that the lot invariant can fail: an open membership on a "
        "dissolved lot must raise both in this call and in the autouse "
        "fixture's teardown"
    ),
)
def test_the_lot_invariant_catches_an_open_member_of_a_finished_lot(
    db: Session, offered_lot_listing: Listing
) -> None:
    """Dissolving a lot without releasing its members is what this must catch.

    Deliberately left broken; the per-test transaction rolls it back.
    Mutation-tested by hand: commenting out the `status = ...` line below
    makes this XPASS(strict) and fails the run, confirming the proof is not
    vacuous.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    lot.status = SalesLotStatus.dissolved  # members left open on purpose
    db.flush()
    check_lot_invariant(db)


def test_the_lot_invariant_is_wired_into_the_autouse_fixture(
    request: pytest.FixtureRequest,
) -> None:
    """The fixture, not just the function, must run the lot check.

    `test_the_invariant_fixture_is_wired_up`'s lesson, applied to the second
    rule -- except the first cut of this test called `check_lot_invariant`
    directly in its own body, the way that lesson's test could not (there is
    no function to call there), and that call phase decides the outcome
    regardless of what the fixture does: mutation-tested by hand, commenting
    out the fixture's own `check_lot_invariant(db)` line in `_claim_invariant`
    (conftest.py) left an equivalent test green (`3 passed, 6 xfailed`, no
    failures), because a clean lot never raises whether or not the fixture
    also checks it. `fixturenames` proves the fixture is in the closure, but
    that was already true with the call deleted too.

    The actual fix, doing literally what `test_the_invariant_fixture_is_wired_up`
    only says: read the fixture's source and assert the call is in it.

    Mutation-tested by hand: commenting out `check_lot_invariant(db)` inside
    `_claim_invariant` now makes this fail with a genuine `AssertionError`,
    not merely lose a redundant `xfail` phase.
    """
    assert "_claim_invariant" in request.fixturenames  # the fixture is in the closure
    src = inspect.getsource(conftest._claim_invariant)
    assert "check_lot_invariant(db)" in src


def test_the_lot_check_runs_ahead_of_the_waiver_branch() -> None:
    """The lot check must run before the waiver branch could ever see it.

    Renamed from `..._even_when_waived`, and no longer a
    `claim_invariant_waiver` test: the first cut set up a claim-and-lot
    scenario under a waiver marker and asserted `check_lot_invariant` still
    raised. That could not prove what its name claimed. `_claim_invariant`
    (conftest.py) calls `check_lot_invariant` *before* its `if waiver is
    None` branch even exists in the control flow, so the scenario's
    `check_lot_invariant(db)` call raised regardless of the marker, the
    same way it raises in the two tests above with no marker at all -- the
    call phase cannot distinguish "the waiver saw a `LotInvariantViolation`
    and declined to catch it" from "the waiver never got a chance to see one
    at all." Worse, because the fixture's own teardown never reaches the
    waiver-handling block either (the same early raise gets there first),
    the marker was never graded: `claim.state = ClaimState.released` was
    dead setup, and a stale waiver on this test could never be caught the
    way the "a waiver that stops biting fails loudly" guarantee promises
    everywhere else. Both are simply removed rather than reworked, per that
    finding.

    Ordering is a property of the fixture's source, not of any one test's
    runtime data, so this reads the source directly: `check_lot_invariant`
    must both appear in `_claim_invariant` and appear before `if waiver is
    None`.

    Mutation-tested by hand: commenting out `check_lot_invariant(db)` inside
    `_claim_invariant`, or moving it to after the `if waiver is None:` line,
    each make this fail with a genuine `AssertionError`.
    """
    src = inspect.getsource(conftest._claim_invariant)
    assert "check_lot_invariant(db)" in src
    assert src.index("check_lot_invariant(db)") < src.index("if waiver is None")


@pytest.mark.xfail(
    strict=True,
    raises=DispositionInvariantViolation,
    reason=(
        "proof that the disposition invariant can fail: a held claim on an "
        "item filed `held` must raise both in this call and in the autouse "
        "fixture's teardown"
    ),
)
def test_the_disposition_invariant_catches_a_held_claim_on_a_held_item(
    db: Session, ebay_listing: Listing
) -> None:
    """Reverting an item's disposition under a live claim is what this must catch.

    Deliberately left broken; the per-test transaction rolls it back.
    Mutation-tested by hand: commenting out the `disposition_id = ...` line
    below makes this XPASS(strict) and fails the run, confirming the proof is
    not vacuous.
    """
    claim = db.scalars(
        select(OfferClaim).where(OfferClaim.listing_id == ebay_listing.id)
    ).first()
    assert claim is not None
    assert claim.state == ClaimState.active  # HELD_BY, so the rule applies
    item = claim.item
    item.disposition_id = require_code(db, Disposition, "held", "disposition")
    db.flush()
    check_disposition_invariant(db)


def test_the_disposition_invariant_is_wired_into_the_autouse_fixture(
    request: pytest.FixtureRequest,
) -> None:
    """The fixture, not just the function, must run the disposition check.

    Same gap `test_the_lot_invariant_is_wired_into_the_autouse_fixture`'s
    docstring names, and the same fix: calling `check_disposition_invariant`
    directly in this test's own body cannot tell "the fixture also calls it"
    from "the fixture does not," because the call phase's own result already
    decides the outcome. Read the fixture's source and assert the call is
    actually in it, instead.

    Mutation-tested by hand: commenting out `check_disposition_invariant(db)`
    inside `_claim_invariant` (conftest.py) now makes this fail with a
    genuine `AssertionError`.
    """
    assert "_claim_invariant" in request.fixturenames  # the fixture is in the closure
    src = inspect.getsource(conftest._claim_invariant)
    assert "check_disposition_invariant(db)" in src
