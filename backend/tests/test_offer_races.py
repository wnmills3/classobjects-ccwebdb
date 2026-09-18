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

RACE_TITLE = "RACE Offer Contested Item"

Outcome = str


@pytest.fixture
def committed(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Real, committing sessions; removes every row the race creates.

    Deletion order respects the FKs a claim and a listing carry:
    ``offer_claim`` first (it points at both ``listing`` and
    ``inventory_item``), then ``listing`` in one statement -- its
    self-referential ``paused_by_listing_id`` is RESTRICT, but a single
    DELETE removing both a paused listing and what it points to never
    violates that -- then the item and the venues this file made.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    with factory() as cleanup:
        item_ids = select(InventoryItem.id).where(
            InventoryItem.source_title == RACE_TITLE
        )
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


def _code_id(session: Session, model: type, code: str) -> int:
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
            item = session.get(InventoryItem, item_id)
            venue = session.get(SalesVenue, venue_id)
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
        item = session.get(InventoryItem, item_id)
        store = session.get(SalesVenue, store_venue_id(session))
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
            item = session.get(InventoryItem, item_id)
            venue = session.get(SalesVenue, elsewhere_venue_id)
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
            listing = session.get(Listing, store_listing_id)
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
