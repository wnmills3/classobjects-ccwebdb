"""Races around the "an item is offered once" rule (selling design, phase 2).

Real, committing sessions, each with its own connection -- not ``TestClient``,
which funnels every request through Starlette's single portal and so could
never observe two requests racing at all. Follows
``test_order_revision_race.py``'s pattern: each thread gets its own
``Session``, and a ``threading.Barrier`` releases them together so the two
transactions genuinely overlap.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from app import offering_writes
from app.models import (
    Authenticity,
    ClaimState,
    Disposition,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    OfferClaim,
    ReferenceMixin,
    SalesVenue,
    SalesVenueKind,
    StorageForm,
    ValuationBasis,
)
from app.offering_writes import OfferRefused
from app.sales_venues import store_venue_id
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from tests.conftest import ClaimInvariantViolation, check_claim_invariant

RACE_TITLE = "RACE Offer Contested Item"

Outcome = str


def _cleanup_race_rows(cleanup: Session) -> None:
    """Check the claim invariant against these rows, then delete them regardless.

    The check runs in a `try` and the deletes in its `finally` on purpose: a
    real violation must still fail the suite (the `raise` propagates once the
    `finally` block finishes), but the rows it found are committed by real,
    independent sessions -- `conftest.py`'s `engine` fixture only
    `create_all`s once per test *session*, with no per-test truncate, so
    without this a genuine finding here would leave permanently poisoned rows
    in `ccwebdb_test`, failing every later `_claim_invariant` check in the
    same run and every run after that until someone cleans the database by
    hand. `test_a_forced_violation_still_leaves_the_database_clean`, below,
    proves both halves: that a real violation still raises, and that the
    rows are gone afterward regardless.

    Deletion order respects the FKs a claim and a listing carry:
    ``offer_claim`` first (it points at both ``listing`` and
    ``inventory_item``), then ``listing`` in one statement -- its
    self-referential ``paused_by_listing_id`` is RESTRICT, but a single
    DELETE removing both a paused listing and what it points to never
    violates that -- then the item and the venues this file made.
    """
    item_ids = select(InventoryItem.id).where(InventoryItem.source_title == RACE_TITLE)
    try:
        check_claim_invariant(cleanup)
    finally:
        cleanup.query(OfferClaim).filter(
            OfferClaim.inventory_item_id.in_(item_ids)
        ).delete(synchronize_session=False)
        cleanup.query(Listing).filter(Listing.inventory_item_id.in_(item_ids)).delete(
            synchronize_session=False
        )
        cleanup.query(InventoryItem).filter(
            InventoryItem.source_title == RACE_TITLE
        ).delete(synchronize_session=False)
        cleanup.query(SalesVenue).filter(SalesVenue.code.like("race-%")).delete(
            synchronize_session=False
        )
        cleanup.commit()


@pytest.fixture
def committed(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Real, committing sessions; removes every row the race creates.

    Checks the suite-wide claim invariant against these rows *before*
    deleting them, not after (see `_cleanup_race_rows`). `_claim_invariant`
    (conftest.py) is autouse, but this fixture is requested explicitly by
    name, and pytest tears an explicitly-requested fixture down before an
    autouse one the test never named -- confirmed with a standalone probe,
    not assumed. Left unchecked, this fixture's own cleanup would delete
    every row the race committed before `_claim_invariant` ever got to look
    at them, so the suite-wide check would silently grade nothing for
    exactly the file where a second writer racing `offering_writes` is most
    likely to leave a claim and its listing disagreeing.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    with factory() as cleanup:
        _cleanup_race_rows(cleanup)


def _code_id(session: Session, model: type[ReferenceMixin], code: str) -> int:
    return session.execute(select(model.id).where(model.code == code)).scalar_one()


def _seed_item(factory: sessionmaker[Session]) -> int:
    """One received, unheld item, real and committed."""
    with factory() as session:
        item = InventoryItem(
            source_title=RACE_TITLE,
            item_kind_id=_code_id(session, ItemKind, "coin"),
            storage_form_id=_code_id(session, StorageForm, "single"),
            authenticity_id=_code_id(session, Authenticity, "unverified"),
            status_id=_code_id(session, ItemStatus, "received"),
            disposition_id=_code_id(session, Disposition, "held"),
            valuation_basis_id=_code_id(session, ValuationBasis, "numismatic"),
        )
        session.add(item)
        session.commit()
        return item.id


def _venue(factory: sessionmaker[Session], code: str) -> int:
    """A non-store marketplace platform to offer on, real and committed."""
    with factory() as session:
        kind_id = _code_id(session, SalesVenueKind, "marketplace")
        venue = SalesVenue(code=code, name=code.title(), sales_venue_kind_id=kind_id)
        session.add(venue)
        session.commit()
        return venue.id


def _active_claims(factory: sessionmaker[Session], item_id: int) -> list[OfferClaim]:
    with factory() as session:
        return list(
            session.scalars(
                select(OfferClaim).where(
                    OfferClaim.inventory_item_id == item_id,
                    OfferClaim.state == ClaimState.active,
                )
            ).all()
        )


def test_a_forced_violation_still_leaves_the_database_clean(
    committed: sessionmaker[Session],
) -> None:
    """`_cleanup_race_rows` must delete even when its own check raises.

    Poisons a real, committed claim/listing pair directly -- offered through
    `offering_writes.offer` first, so the row shape is exactly what a real
    race would leave, then flipped by hand the way `test_offering_writes.py`'s
    `_claim()` helper does for the rolled-back suite, bypassing
    `offering_writes` on purpose. Calls `_cleanup_race_rows` on a separate
    session directly, rather than relying on this test's own `committed`
    fixture teardown, so this proves the point without depending on
    cross-test ordering: without the `try`/`finally` split inside it, this
    would leave the poisoned rows behind forever (see that function's
    docstring), and every later test in this run -- and every future run,
    until someone cleans `ccwebdb_test` by hand -- would fail against them.
    """
    item_id = _seed_item(committed)
    venue_id = _venue(committed, "race-poison")
    with committed() as session:
        item = session.get_one(InventoryItem, item_id)
        venue = session.get_one(SalesVenue, venue_id)
        listing = offering_writes.offer(
            session,
            item=item,
            venue=venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
            quantity=1,
        )
        session.commit()
        claim = session.scalar(
            select(OfferClaim).where(OfferClaim.listing_id == listing.id)
        )
        assert claim is not None
        claim.state = ClaimState.released  # the listing is still active
        session.commit()

    with committed() as cleanup, pytest.raises(ClaimInvariantViolation):
        _cleanup_race_rows(cleanup)

    with committed() as verify:
        assert (
            verify.scalar(
                select(InventoryItem.id).where(InventoryItem.source_title == RACE_TITLE)
            )
            is None
        )
        assert (
            verify.scalar(select(SalesVenue.id).where(SalesVenue.code == "race-poison"))
            is None
        )


def test_two_platforms_racing_the_same_item_leave_exactly_one_winner(
    committed: sessionmaker[Session],
) -> None:
    """Two threads offer one item on two platforms at once: one wins.

    Both lock the same item row (``offering_writes._lock_items``) before
    reading what claims it, so the loser should see the winner's committed
    listing and be refused cleanly -- but the backstop is the database's own
    partial unique index (``uq_offer_claim_active``), so an ``IntegrityError``
    is an acceptable loss too, not just ``OfferRefused``.
    """
    item_id = _seed_item(committed)
    venue_ids = [_venue(committed, "race-ebay"), _venue(committed, "race-whatnot")]
    barrier = threading.Barrier(2)

    def attempt(venue_id: int, price: Decimal) -> Outcome:
        session = committed()
        try:
            item = session.get_one(InventoryItem, item_id)
            venue = session.get_one(SalesVenue, venue_id)
            barrier.wait(timeout=10)
            offering_writes.offer(
                session,
                item=item,
                venue=venue,
                listing_format=ListingFormat.fixed_price,
                price=price,
                title="race",
                description="",
                external_id=None,
                quantity=1,
            )
            session.commit()
            return "ok"
        except OfferRefused:
            session.rollback()
            return "refused"
        except IntegrityError:
            session.rollback()
            return "integrity_error"
        finally:
            session.close()

    jobs: list[tuple[int, Decimal]] = list(
        zip(venue_ids, [Decimal("10.00"), Decimal("20.00")], strict=True)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            pool.map(lambda job: attempt(*job), jobs, timeout=30), key=str
        )

    assert outcomes.count("ok") == 1, outcomes
    losses = outcomes.count("refused") + outcomes.count("integrity_error")
    assert losses == 1, outcomes

    active = _active_claims(committed, item_id)
    assert len(active) == 1, active


def test_offering_elsewhere_and_ending_the_store_listing_leave_no_orphan(
    committed: sessionmaker[Session],
) -> None:
    """One thread offers the item elsewhere; another ends the store listing.

    Whatever order the two locks land in, the end state must be consistent:
    no listing left `paused` whose pauser is `ended`, and the item has at
    most one active claim.
    """
    item_id = _seed_item(committed)
    elsewhere_venue_id = _venue(committed, "race-elsewhere")

    with committed() as session:
        item = session.get_one(InventoryItem, item_id)
        store = session.get_one(SalesVenue, store_venue_id(session))
        store_listing = offering_writes.offer(
            session,
            item=item,
            venue=store,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("50.00"),
            title="race store",
            description="",
            external_id=None,
            quantity=1,
        )
        session.commit()
        store_listing_id = store_listing.id

    barrier = threading.Barrier(2)

    def offer_elsewhere() -> Outcome:
        session = committed()
        try:
            item = session.get_one(InventoryItem, item_id)
            venue = session.get_one(SalesVenue, elsewhere_venue_id)
            barrier.wait(timeout=10)
            offering_writes.offer(
                session,
                item=item,
                venue=venue,
                listing_format=ListingFormat.fixed_price,
                price=Decimal("40.00"),
                title="race elsewhere",
                description="",
                external_id=None,
                quantity=1,
            )
            session.commit()
            return "offered"
        except OfferRefused:
            session.rollback()
            return "refused"
        except IntegrityError:
            session.rollback()
            return "integrity_error"
        except StaleDataError:
            session.rollback()
            return "stale"
        finally:
            session.close()

    def end_store() -> Outcome:
        session = committed()
        try:
            # Loaded before the barrier, exactly as a router handler would
            # have it in hand already -- so this is the same object whether
            # or not the other thread changes the row underneath it.
            listing = session.get_one(Listing, store_listing_id)
            barrier.wait(timeout=10)
            offering_writes.end_offer(session, listing, sold=False)
            session.commit()
            return "ended"
        except OfferRefused:
            session.rollback()
            return "refused"
        except IntegrityError:
            session.rollback()
            return "integrity_error"
        except StaleDataError:
            session.rollback()
            return "stale"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            pool.map(lambda f: f(), [offer_elsewhere, end_store], timeout=30), key=str
        )

    with committed() as session:
        orphaned = session.scalars(
            select(Listing).where(
                Listing.status == ListingStatus.paused,
                Listing.paused_by_listing_id.in_(
                    select(Listing.id).where(Listing.status == ListingStatus.ended)
                ),
            )
        ).all()

    assert orphaned == [], (outcomes, orphaned)
    active = _active_claims(committed, item_id)
    assert len(active) <= 1, (outcomes, active)
    # Both operations are legitimate on their own; the item lock the pair
    # share is what has to make them safe together, not a rejection of one of
    # them. `"stale"` names the defect this test found: `end_offer` mutating
    # a `Listing` object the caller loaded before a concurrent `offer`
    # elsewhere paused it underneath them, using a version already gone by
    # the time this function's own item lock let it proceed.
    assert "stale" not in outcomes, outcomes
