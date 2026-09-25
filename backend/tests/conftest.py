"""Shared test fixtures.

Tests run against a dedicated PostgreSQL database (``<dbname>_test``) that is
dropped and recreated once per session, so they never touch development data.

Each test then runs inside a transaction that is rolled back afterwards, which
keeps tests independent without paying to rebuild the schema every time.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Sequence
from decimal import Decimal
from typing import cast

import pytest
from app import lot_writes, offering_writes
from app.config import settings
from app.database import Base, get_db
from app.grades import GRADE_DISPLAY_SQL, split_fields
from app.main import app
from app.models import (
    Auction,
    AuctionLot,
    AuctionLotResult,
    AuctionStatus,
    Authenticity,
    ClaimState,
    Country,
    Currency,
    Disposition,
    Grade,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    ListingStatusHistory,
    OfferClaim,
    ReferenceMixin,
    SalesFeeKind,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesVenue,
    SalesVenueKind,
    StorageForm,
    StrikeType,
    User,
    UserRole,
    ValuationBasis,
)
from app.models.views import CREATE_VIEWS
from app.references import require_code
from app.sales_venues import ensure_store_venue, store_venue_id
from app.security import hash_password
from app.seeding import seed_all
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session


def _test_database_url() -> URL:
    """Same server and credentials as the app, but a separate database.

    Returns a ``URL`` object rather than a string on purpose: ``str(url)``
    renders the password as ``***``, which silently produces a URL that cannot
    authenticate. Render with ``hide_password=False`` wherever a string is
    genuinely needed.
    """
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        return make_url(override)
    url = make_url(settings.database_url)
    return url.set(database=f"{url.database}_test")


TEST_URL = _test_database_url()

#: Matches the rows the baseline migration seeds `sales_fee_kind` with -- see
#: `backend/alembic/baseline.sql`.
_FEE_KINDS: tuple[tuple[str, str, int], ...] = (
    ("commission", "Commission", 10),
    ("processing", "Payment processing", 20),
    ("listing", "Listing fee", 30),
    ("shipping_label", "Shipping label", 40),
    ("promotion", "Promotion", 50),
    ("other", "Other", 60),
)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Create a fresh test database for the session and tear it down after."""
    url = TEST_URL
    admin_url = url.set(database="postgres")

    # CREATE/DROP DATABASE cannot run inside a transaction.
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid() "
                # Client sessions only. An autovacuum worker may be running
                # on this database, and signalling one needs the
                # pg_signal_autovacuum_worker role -- which the application
                # user does not have, so the attempt raises and the teardown
                # fails intermittently. Workers exit when the database is
                # dropped, so there is nothing to terminate here anyway.
                "AND backend_type = 'client backend'"
            ),
            {"name": url.database},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}"'))
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))

    test_engine = create_engine(TEST_URL, pool_pre_ping=True)
    Base.metadata.create_all(test_engine)

    # Views are not in Base.metadata -- create_all builds tables only -- so
    # they are created from the same definitions the migration uses. Without
    # this, a test of the public_catalog authorisation boundary would silently
    # have nothing to check.
    with test_engine.begin() as conn:
        # The views compose grades with this function, which the migration
        # that split strike type from grade creates.
        conn.execute(text(GRADE_DISPLAY_SQL))
        for statement in CREATE_VIEWS:
            conn.execute(text(statement))

    # Also outside Base.metadata: an extension lives in the database's own
    # catalog, not in anything the ORM tracks. levenshtein() is needed for
    # near_duplicate_serial, and this test database is built fresh from the
    # models rather than by running the migrations, so the baseline migration
    # that installs it on a real deployment never runs here.
    with test_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch"))

    # Reference data is seeded once and committed, not per test: inventory_item
    # has NOT NULL foreign keys into half a dozen classifier tables, so almost
    # nothing can be inserted without it. The per-test transaction rolls back
    # around this, leaving the vocabulary in place.
    with Session(test_engine) as session:
        seed_all(session)
        # The baseline migration creates the web store platform on a real
        # database;
        # this one is built from the models, so it is created here.
        ensure_store_venue(session)
        # Likewise the fee kind vocabulary: the baseline seeds it with an
        # INSERT because it is a closed vocabulary the product defines, not
        # data from backend/data/reference/ that seed_all would pick up.
        session.add_all(
            SalesFeeKind(code=code, label=label, sort_order=order, is_active=True)
            for code, label, order in _FEE_KINDS
        )
        session.commit()

    yield test_engine

    test_engine.dispose()
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid() "
                # Client sessions only. An autovacuum worker may be running
                # on this database, and signalling one needs the
                # pg_signal_autovacuum_worker role -- which the application
                # user does not have, so the attempt raises and the teardown
                # fails intermittently. Workers exit when the database is
                # dropped, so there is nothing to terminate here anyway.
                "AND backend_type = 'client backend'"
            ),
            {"name": url.database},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}"'))
    admin.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    """A session wrapped in a transaction that is rolled back after the test.

    ``join_transaction_mode="create_savepoint"`` means a commit inside the
    application code becomes a savepoint release rather than a real commit, so
    the outer rollback still discards everything.
    """
    connection = engine.connect()
    transaction = connection.begin()
    # No `autoflush` argument, so this session flushes on every query --
    # production's `SessionLocal` (app/database.py) sets `autoflush=False`.
    # The divergence hides one shape of bug: code that assigns a column and
    # then calls a writer which re-reads that row with `populate_existing`
    # loses the assignment in production, but not here, because the query
    # inside the writer flushes it first. A test that needs production's
    # ordering has to opt out for itself, with `db.autoflush = False` or
    # `db.no_autoflush` -- see `test_for_sale_guards.py`.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


#: Every listing status paired with the claim state `offering_writes` gives
#: it. `docs/specs/selling-design.md` (*Invariants checked after every test*)
#: requires this to hold after
#: every write in the suite -- `offering_writes` is the only writer of either
#: column, in one transaction, so a disagreement means something wrote around
#: it.
_EXPECTED_CLAIM_STATE = {
    ListingStatus.active: ClaimState.active,
    ListingStatus.paused: ClaimState.paused,
    ListingStatus.ended: ClaimState.released,
}


class ClaimInvariantViolation(AssertionError):
    """A claim's state disagrees with its listing's status.

    A distinct subclass of `AssertionError`, not a bare one, so
    `test_claim_invariant.py`'s `xfail(raises=...)` can narrow to exactly
    this failure rather than any assertion failure in the suite. The
    original plan was to narrow with `xfail(match=...)` instead, on the
    message text -- but `match` is `pytest.raises`'s parameter, not
    `xfail`'s; mypy caught this (`xfail` has no such overload) before it
    ever ran. `raises=<type>` is the only narrowing lever `xfail` actually
    exposes, so a dedicated type is how this gets the same protection: an
    unrelated crash elsewhere in either phase still fails the suite instead
    of being absorbed as "expected."
    """


def check_claim_invariant(db: Session) -> None:
    """Assert every claim's state equals its listing's status, right now.

    The join matches a claim to its listing on `listing_id` alone -- nothing
    more. A tempting refinement is to also require
    `OfferClaim.inventory_item_id == Listing.inventory_item_id`, matching only
    a listing's "own" claim; an earlier version of this function did exactly
    that, to wave off some `test_offering_writes.py` scaffolding that builds a
    claim for a different item on an existing `listing_id`. That refinement is
    wrong, not merely narrower, for two reasons `docs/specs/selling-design.md`
    makes concrete:

    - Phase 3 makes `Listing.inventory_item_id` **nullable** (a lot listing
      carries `sales_lot_id` instead, enforced by a check constraint) and a
      lot's claims carry *member* item ids, never the (absent) listing item
      id. Under the item-id join, `NULL = <member id>` is unknown in SQL, so
      the inner join drops every member claim -- the invariant would grade
      **zero** rows for a lot listing, and the day lots ship, this check's
      docstring's promise silently inverts into "nothing here is ever
      wrong," with nothing failing to announce it.
    - Even in phase 2, `uq_offer_claim_pair` is already a unique constraint
      on `(listing_id, inventory_item_id)`, so the item-id join reduces any
      listing to *at most one* matching claim row by construction -- the
      wrong cardinality for the very shape phase 3 introduces (one claim per
      lot member, several rows sharing a `listing_id`).
    - `offer` and `_move_claims` (`app/offering_writes.py`) are the only two
      places in `backend/app/` that construct an `OfferClaim`, and both set
      `inventory_item_id` from the listing's own item. A row where the claim's
      item differs from the listing's item is, by definition, one the
      sanctioned writer cannot produce -- exactly the anomaly this check
      exists to surface. A join whose exclusion rule is "drop everything the
      writer could not have written" is inverted with respect to "a
      disagreement means something wrote around it": it hides the very rows
      that would prove that.

    The right fix for that scaffolding is the `claim_invariant_waiver` marker
    (registered in `pyproject.toml`), applied per test with a `reason=`, not a
    join condition that quietly matches less everywhere.

    One query, no per-row loads, because the autouse fixture below calls this
    roughly 1,300 times. Pulled out as its own function -- rather than written
    inline in the fixture -- so `test_claim_invariant.py` can call the exact
    check the fixture runs and prove it actually raises on a broken claim,
    without needing a second, hand-rolled copy of the query that could drift
    from the real one or pass for a different reason than the fixture would.
    """
    rows = db.execute(
        select(Listing.id, Listing.status, OfferClaim.state).join(
            OfferClaim, OfferClaim.listing_id == Listing.id
        )
    ).all()
    wrong = [
        (listing_id, status, state)
        for listing_id, status, state in rows
        if state is not _EXPECTED_CLAIM_STATE[status]
    ]
    if wrong:
        raise ClaimInvariantViolation(
            f"claim state disagrees with listing status: {wrong}"
        )


class LotInvariantViolation(AssertionError):
    """An open lot membership disagrees with its lot's status.

    A distinct type from `ClaimInvariantViolation`, for two reasons. The
    `xfail(raises=...)` proof in `test_claim_invariant.py` narrows on the
    exact type, so sharing one would let either proof pass on the other's
    failure. And `claim_invariant_waiver` absorbs *any*
    `ClaimInvariantViolation` (the documented limit at this fixture's
    docstring): reusing that type would silently exempt the four waived
    tests from this rule as well, which is exactly the accident this check
    exists to prevent.
    """


def check_lot_invariant(db: Session) -> None:
    """Assert every open lot membership agrees with its lot's status, right now.

    One direction only: an open membership (`released_at IS NULL`) implies
    its lot is `assembling` or `offered`. The converse -- that an
    `assembling` lot has members -- is false by design: a lot is created
    empty and is assembled a coin at a time.

    One query, no per-row loads: the autouse fixture below calls this
    roughly 1,300 times.
    """
    open_in_closed = db.execute(
        select(SalesLotItem.sales_lot_id, SalesLot.status)
        .join(SalesLot, SalesLot.id == SalesLotItem.sales_lot_id)
        .where(
            SalesLotItem.released_at.is_(None),
            # Named by the good statuses, matching the docstring above, and
            # failing closed: a fifth `SalesLotStatus` this does not yet know
            # about is caught here (an open membership disagrees with it)
            # rather than silently passed as an inferred "closed" status
            # would be under `.in_((sold, dissolved))`.
            SalesLot.status.not_in((SalesLotStatus.assembling, SalesLotStatus.offered)),
        )
    ).all()
    if open_in_closed:
        raise LotInvariantViolation(
            f"lot membership is still open on a finished lot: {open_in_closed}"
        )


class DispositionInvariantViolation(AssertionError):
    """A held claim disagrees with its item's disposition.

    A distinct type from `ClaimInvariantViolation` and `LotInvariantViolation`,
    for the same two reasons `LotInvariantViolation`'s docstring gives: the
    `xfail(raises=...)` proofs in `test_claim_invariant.py` narrow on the
    exact type, and `claim_invariant_waiver` absorbs only
    `ClaimInvariantViolation` by design -- reusing either other type would
    let a waived test's claim scenario silently exempt this rule too.
    """


#: What a `HELD_BY` claim's item may legitimately be filed as, one direction
#: only. `listed` is the ordinary case. `offering_writes.SOLD_AWAY` is the
#: allowance `check_disposition_invariant` documents: not the biconditional,
#: and not slack -- see that function's docstring for why each is true of the
#: code as it actually is.
_DISPOSITION_ALLOWED_UNDER_CLAIM = frozenset({"listed"}) | offering_writes.SOLD_AWAY


def check_disposition_invariant(db: Session) -> None:
    """Assert a held claim's item is filed `listed` or already sold away.

    One direction only: a claim in `offering_writes.HELD_BY` (`active` or
    `paused`) implies its item's disposition is `listed` -- or in
    `offering_writes.SOLD_AWAY`, which a sale wrote. Two allowances make this
    true of the code as it actually is, rather than of a stricter rule that
    would be nicer to have:

    - **Not the biconditional.** `build_listing` (this file) creates a
      `listed` item with **no claim**, and `listing` / `make_listing` are
      used by dozens of tests. "A `listed` item has a held claim" fails all
      of them on the first run.
    - **The `SOLD_AWAY` allowance is not slack.** A shop checkout that takes
      the last unit sets the item to `sold`
      (`order_writes._after_stock_change`) while its store listing stays
      `active` with an `active` claim -- `end_offer`'s own comment, beside
      `if item is not None and item.disposition_id == listed:`
      (`offering_writes.py`, in `end_offer`'s disposition loop), names that
      shape as intended. Without
      the allowance the rule is false the first time a test buys out a
      listing.

    What is left after both allowances is worth having: it catches a held
    claim on an item filed as `held`, the shape "a lot's members were never
    moved to `listed`" and "an ending moved an item back while something
    still holds it" both produce.

    One query, no per-row loads, for the same reason `check_claim_invariant`
    is one.
    """
    rows = db.execute(
        select(OfferClaim.id, InventoryItem.id, Disposition.code)
        .join(InventoryItem, InventoryItem.id == OfferClaim.inventory_item_id)
        .join(Disposition, Disposition.id == InventoryItem.disposition_id)
        .where(OfferClaim.state.in_(offering_writes.HELD_BY))
    ).all()
    wrong = [row for row in rows if row[2] not in _DISPOSITION_ALLOWED_UNDER_CLAIM]
    if wrong:
        raise DispositionInvariantViolation(
            f"a held claim disagrees with its item's disposition: {wrong}"
        )


class HistoryInvariantViolation(AssertionError):
    """A listing's recorded history disagrees with its current status.

    Its own type for the reason `LotInvariantViolation` gives: the claim
    waiver absorbs only `ClaimInvariantViolation`, and must not exempt this.
    """


def check_listing_history_invariant(db: Session) -> None:
    """Assert each listing's latest history row names its current status.

    One direction only: a listing **with** history must agree with it. A
    listing with none is allowed, because `build_listing` (this file)
    inserts listings directly for dozens of tests, the same allowance
    `check_disposition_invariant` makes. What is left catches the shape that
    matters: something wrote `listing.status` on an offered listing without
    going through `offering_writes._set_status`, and the history silently
    stopped being true.

    One query, no per-row loads, for the same reason `check_claim_invariant`
    is one.
    """
    latest = (
        select(
            ListingStatusHistory.listing_id,
            func.max(ListingStatusHistory.id).label("last_id"),
        )
        .group_by(ListingStatusHistory.listing_id)
        .subquery()
    )
    wrong = db.execute(
        select(Listing.id, Listing.status, ListingStatusHistory.to_status)
        .join(latest, latest.c.listing_id == Listing.id)
        .join(ListingStatusHistory, ListingStatusHistory.id == latest.c.last_id)
        .where(Listing.status != ListingStatusHistory.to_status)
    ).all()
    if wrong:
        raise HistoryInvariantViolation(
            f"listing status disagrees with its latest history row: {wrong}"
        )


class AuctionInvariantViolation(AssertionError):
    """An auction, its lots and the listings they detail disagree.

    Its own type for the reason `LotInvariantViolation` gives: the claim
    waiver absorbs only `ClaimInvariantViolation`, and must not exempt this.
    """


#: Statuses in which an auction's lots are still on offer: nothing has been
#: settled, and `cancel` has not removed them. `closed` is here because the
#: sale has happened but its results have not been entered -- `settle` is
#: what ends the listings, not `close`.
_AUCTION_LOTS_ON_OFFER = frozenset(
    {
        AuctionStatus.draft,
        AuctionStatus.scheduled,
        AuctionStatus.consigned,
        AuctionStatus.closed,
    }
)

#: Statuses in which the house may still hold an auction's coins. `close`
#: accepts a `consigned` auction without clearing `consigned_on` (ruling R13),
#: so `closed` is one of them; `cancel` and `settle` clear the date.
_AUCTION_CUSTODY_POSSIBLE = frozenset({AuctionStatus.consigned, AuctionStatus.closed})


def _auction_lot_problems(
    status: AuctionStatus,
    result: AuctionLotResult | None,
    hammer_price: Decimal | None,
    buyer_customer_id: int | None,
    listing_status: ListingStatus,
    listing_format: ListingFormat,
) -> list[str]:
    """Every rule one `auction_lot` row breaks, named, given its auction and listing.

    Fails closed on an `AuctionStatus` this does not know: a new status is a
    violation until someone decides what its lots should look like.
    """
    problems: list[str] = []
    if listing_format is not ListingFormat.auction:
        problems.append(f"details a {listing_format.value} listing")
    if status in _AUCTION_LOTS_ON_OFFER:
        if result is not None:
            problems.append(f"has a result on a {status.value} auction")
        if listing_status is not ListingStatus.active:
            problems.append(
                f"its listing is {listing_status.value} on a {status.value} auction"
            )
    elif status is AuctionStatus.settled:
        if result is None:
            problems.append("has no result on a settled auction")
        if listing_status is not ListingStatus.ended:
            problems.append(f"its listing is {listing_status.value} after settlement")
    elif status is AuctionStatus.cancelled:
        problems.append("survived its auction's cancellation")
    else:
        problems.append(f"belongs to an auction in unknown status {status.value}")
    if result is AuctionLotResult.sold:
        if hammer_price is None or buyer_customer_id is None:
            problems.append("sold with no hammer price or no buyer")
    elif hammer_price is not None or buyer_customer_id is not None:
        problems.append("carries a hammer price or buyer but did not sell")
    return problems


def check_auction_invariant(db: Session) -> None:
    """Assert every auction agrees with its lots, and every lot with its listing.

    `app.auctions` is the sole writer of `auction` and `auction_lot`, and it
    reaches listings only through `offering_writes`, so these hold after every
    write unless something wrote around them:

    - An auction whose lots are still on offer (`draft` through `closed`) has
      no lot results, and every lot's listing is `active`. An auction listing
      is never paused -- only a store listing is.
    - A `settled` auction has a result on every lot, and every lot's listing
      is `ended`.
    - A `cancelled` auction has no lots: `cancel` deletes them (ruling R11).
    - A `sold` lot has a hammer price and a buyer; any other lot has neither.
    - A lot details an auction-format listing. Not the converse: the Offer
      dialog puts a coin on eBay by auction with no `auction` behind it.
    - `consigned_on` is set exactly when the house may hold the coins: always
      on a `consigned` auction, possibly on a `closed` one, never otherwise.

    Two queries, no per-row loads, for the same reason `check_claim_invariant`
    is one.
    """
    wrong: list[str] = []
    lot_rows = db.execute(
        select(
            AuctionLot.id,
            Auction.status,
            AuctionLot.result,
            AuctionLot.hammer_price,
            AuctionLot.buyer_customer_id,
            Listing.status,
            Listing.format,
        )
        .join(Auction, Auction.id == AuctionLot.auction_id)
        .join(Listing, Listing.id == AuctionLot.listing_id)
    ).all()
    for lot_id, *fields in lot_rows:
        wrong.extend(
            f"auction_lot {lot_id} {problem}"
            for problem in _auction_lot_problems(*fields)
        )
    custody_rows = db.execute(
        select(Auction.id, Auction.status, Auction.consigned_on)
    ).all()
    for auction_id, status, consigned_on in custody_rows:
        if consigned_on is not None and status not in _AUCTION_CUSTODY_POSSIBLE:
            wrong.append(f"auction {auction_id} is {status.value} but still consigned")
        if consigned_on is None and status is AuctionStatus.consigned:
            wrong.append(f"auction {auction_id} is consigned with no consigned_on")
    if wrong:
        raise AuctionInvariantViolation("; ".join(wrong))


@pytest.fixture(autouse=True)
def _claim_invariant(request: pytest.FixtureRequest) -> Iterator[None]:
    """After every test, each claim's state must equal its listing's status.

    Also runs `check_lot_invariant`, `check_disposition_invariant`,
    `check_listing_history_invariant` and `check_auction_invariant`, all
    ahead of the claim waiver handling below. `claim_invariant_waiver`
    absorbs a `ClaimInvariantViolation` only -- it is a waiver of the claim
    half of this fixture, never of another rule, and none of them consults
    it. The auction check has its own `auction_invariant_waiver`, graded the
    same way (a `reason=` is required, and a waiver that stops biting fails)
    and equally narrow: it absorbs an `AuctionInvariantViolation` and nothing
    else.

    ``db`` is fetched with ``request.getfixturevalue("db")`` -- and only when
    ``"db" in request.fixturenames``, i.e. only for a test that already has a
    session in its own fixture closure -- rather than taken as a normal
    parameter. A test with no `db` anywhere never pays for a session or a
    live PostgreSQL connection it would otherwise not have needed; a dozen
    test files (`test_config.py`, `test_logpipe.py`, `test_photo_names.py`
    and others) have no `db` parameter anywhere and stay exactly as
    database-free as before this fixture existed.

    The fetch happens *before* ``yield``, not after, and that placement is
    load-bearing, confirmed with a throwaway probe before relying on it: a
    fixture that calls ``getfixturevalue("db")`` during its own setup is
    registered as one of `db`'s dependents in the same way a static `db`
    parameter would be, so pytest still defers `db`'s rollback-and-close
    until after this fixture's teardown runs. Calling it *after* `yield`
    instead does not get that ordering for free -- the probe reproduced
    exactly that failure: `db` was already torn down by the time this
    fixture's teardown tried to fetch it, because nothing had told pytest
    this fixture needed `db` kept alive that long. (`request.getfixturevalue`
    during teardown is also documented as deprecated for a fixture not
    already requested, which is the same trap from a different angle.)
    ``client`` depends on `db`, so any test using `client` still has `db` in
    its closure and stays graded; only a test with no `db` anywhere, directly
    or transitively, is skipped.

    Two further consequences of running for every test in the suite, worth
    naming rather than discovering later:

    - `test_offer_races.py`, `test_concurrency.py`, `test_concurrent_writes.py`
      and `test_order_revision_race.py` -- four files, all of whose tests
      take ``committed`` as their only fixture argument -- each race real,
      independently committing sessions against that shared fixture (not
      ``db``) and delete the rows the race made in the fixture's own
      teardown. `db` is in none of those tests' closures, so **this autouse
      check does not run for any of them at all** -- not "runs against an
      emptied table," which was true before this paragraph named the actual
      mechanism, but genuinely skipped, the same way a `db`-free test is.
      Measured directly: a fixture requested
      explicitly by a test (``committed``) is torn down *before* an autouse
      fixture the test never named (confirmed with a throwaway probe:
      `committed teardown` then `auto teardown` then a fixture `committed`
      itself depends on) -- so even where `db` did happen to be in scope,
      ``committed``'s cleanup would already have deleted whatever the race
      wrote before this fixture's check could see it. `test_offer_races.py`
      is the one of the four that actually writes `OfferClaim` rows, and its
      own `committed` fixture now calls `check_claim_invariant` on the
      `cleanup` session immediately before deleting anything (inside a
      `try`/`finally` so a real violation still leaves the database clean
      for the rest of the run -- see that fixture's own docstring), closing
      the gap for real committed claim data. The other three never create an
      `OfferClaim`, `offering_writes`, or `record_sale` at all, so the same
      gap exists there in principle but has nothing to grade in practice;
      anyone adding claim-writing to one of them needs to call
      `check_claim_invariant` explicitly the way `test_offer_races.py` does,
      because the autouse fixture here cannot reach a `committed`-only test.
    - A test that caught an `IntegrityError` from an ORM flush and never
      called `db.rollback()` afterward is the one real case this skips:
      SQLAlchemy deactivates the session's transaction on that specific
      failure (measured directly against this project's own Postgres setup,
      not assumed -- `rollback()` and `close()` both leave `db.is_active`
      `True` again, so neither is what this guards against). Querying a
      session in that state raises a `PendingRollbackError` unrelated to the
      invariant, which would turn "invariant broken" into a misleading
      fixture crash, so this returns early instead. The handful of tests
      that end this way (a handful of `pytest.raises(IntegrityError)`
      constraint tests in `test_sales_fees_schema.py`, `test_sales_venues.py`
      and one in `test_offering_writes.py`) go **unchecked** by this fixture
      -- not verified, skipped. That is an honest gap, not a guarantee.

    A test whose ``claim_invariant_waiver`` marker names a scenario this
    check must still be seen to catch (see `check_claim_invariant`'s
    docstring) makes this fixture run the check *and* require it to raise:
    a waiver that has stopped biting -- because the scenario it names no
    longer disagrees with the invariant -- is a stale exemption quietly
    hiding that the thing it was written to prove is no longer true, and
    fails loudly instead. The marker's `reason=` is mandatory, not merely
    documented as expected: a waiver with no `reason` (or an empty one) fails
    the test outright, so the one artefact a human actually has to read and
    agree with cannot be silently omitted. `--strict-markers` only enforces
    that the *marker name* is registered; it has no idea the marker's own
    convention calls for a `reason`, so this fixture is what enforces that
    half.

    Known limit of this whole waiver design, left as-is rather than closed:
    a marked test currently absorbs *any* `ClaimInvariantViolation`, not
    specifically the one its `reason` describes. A `shape=` refinement -- the
    waiver declaring which disagreement it expects, so a second, unrelated,
    coincident bug inside an already-waived test could not hide behind it --
    would close that, at the cost of roughly ten lines of mechanism for a
    hole that needs two simultaneous bugs to matter. Not built. Whether a
    waiver's `reason` is *true* is a code-review question in either case;
    nothing mechanical here or with `shape=` can check that.
    """
    db: Session | None = None
    if "db" in request.fixturenames:
        db = request.getfixturevalue("db")
    yield
    auction_waiver = request.node.get_closest_marker("auction_invariant_waiver")
    if (
        auction_waiver is not None
        and not str(auction_waiver.kwargs.get("reason") or "").strip()
    ):
        pytest.fail(
            f"{request.node.name} is marked auction_invariant_waiver with no "
            "reason= naming the scenario it waives; add one"
        )
    if auction_waiver is not None and db is None:
        pytest.fail(
            f"{request.node.name} is marked auction_invariant_waiver "
            f"({auction_waiver.kwargs['reason']!r}) but has no `db` fixture in "
            "its closure, so the auction invariant is never checked for it "
            "and the waiver waives nothing; remove it"
        )
    waiver = request.node.get_closest_marker("claim_invariant_waiver")
    # `str(...).strip()`, not a bare truthiness check: `not "   "` is `False`,
    # so a whitespace-only reason -- "   " or "\n\t" -- would otherwise pass
    # this exactly the way an empty one is meant to be caught failing.
    if waiver is not None and not str(waiver.kwargs.get("reason") or "").strip():
        pytest.fail(
            f"{request.node.name} is marked claim_invariant_waiver with no "
            "reason= naming the scenario it waives; add one"
        )
    if db is None:
        # Graded *before* the early return, the same way the missing-`reason`
        # check above is: this fixture never checks the invariant for a test
        # with no `db` in its closure, so a waiver on such a test cannot be
        # waiving anything -- it is stale the moment it is written, and
        # returning first would let it sit there reading like a live
        # exemption. `not db.is_active` below is a different case and keeps
        # its plain return: there the check is genuinely unable to run (see
        # this docstring's second consequence, the deactivated-transaction
        # gap), so a waiver is undecidable rather than provably stale.
        if waiver is not None:
            pytest.fail(
                f"{request.node.name} is marked claim_invariant_waiver "
                f"({waiver.kwargs['reason']!r}) but has no `db` fixture in "
                "its closure, so the claim invariant is never checked for it "
                "and the waiver waives nothing; remove it"
            )
        return
    if not db.is_active:
        return
    # Before the waiver branch below, and never waived: the
    # `claim_invariant_waiver` marker absorbs any `ClaimInvariantViolation`,
    # and a lot or disposition violation inside an already-waived test must
    # still fail. That is why each raises its own type.
    check_lot_invariant(db)
    check_disposition_invariant(db)
    check_listing_history_invariant(db)
    # Its own waiver, graded the same way as the claim waiver below: a marked
    # test must still break the rule, or the waiver is stale.
    if auction_waiver is None:
        check_auction_invariant(db)
    else:
        try:
            check_auction_invariant(db)
        except AuctionInvariantViolation:
            pass
        else:
            pytest.fail(
                f"{request.node.name} is marked auction_invariant_waiver "
                f"({auction_waiver.kwargs['reason']!r}) but the auction "
                "invariant no longer disagrees -- the waiver is stale; fix or "
                "remove it"
            )
    if waiver is None:
        check_claim_invariant(db)
        return
    try:
        check_claim_invariant(db)
    except ClaimInvariantViolation:
        return
    reason = waiver.kwargs["reason"]
    pytest.fail(
        f"{request.node.name} is marked claim_invariant_waiver "
        f"({reason!r}) but the claim invariant no longer disagrees -- "
        "the waiver is stale; fix or remove it"
    )


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """A TestClient whose requests all share the rolled-back test session."""
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Data helpers
# --------------------------------------------------------------------------

ADMIN_PASSWORD = "adminpassword"
CUSTOMER_PASSWORD = "customerpassword"


@pytest.fixture
def admin_user(db: Session) -> User:
    user = User(
        email="admin@example.com",
        full_name="Test Admin",
        hashed_password=hash_password(ADMIN_PASSWORD),
        role=UserRole.admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def customer_user(db: Session) -> User:
    user = User(
        email="customer@example.com",
        full_name="Test Customer",
        hashed_password=hash_password(CUSTOMER_PASSWORD),
        role=UserRole.customer,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _token_headers(client: TestClient, email: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", data={"username": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def admin_headers(client: TestClient, admin_user: User) -> dict[str, str]:
    return _token_headers(client, admin_user.email, ADMIN_PASSWORD)


@pytest.fixture
def customer_headers(client: TestClient, customer_user: User) -> dict[str, str]:
    return _token_headers(client, customer_user.email, CUSTOMER_PASSWORD)


@pytest.fixture
def ebay_venue(db: Session) -> SalesVenue:
    """A marketplace platform to offer and sell on, separate from the store."""
    venue = SalesVenue(
        code="ebay",
        name="eBay",
        sales_venue_kind_id=require_code(db, SalesVenueKind, "marketplace", "kind"),
        commission_rate=Decimal("0.1325"),
    )
    db.add(venue)
    db.flush()
    return venue


@pytest.fixture
def whatnot_venue(db: Session) -> SalesVenue:
    """A second marketplace platform, distinct from `ebay_venue`."""
    venue = SalesVenue(
        code="whatnot",
        name="Whatnot",
        sales_venue_kind_id=require_code(db, SalesVenueKind, "marketplace", "kind"),
        commission_rate=Decimal("0.08"),
    )
    db.add(venue)
    db.flush()
    return venue


@pytest.fixture
def heritage_venue(db: Session) -> SalesVenue:
    """An auction house that sells on the owner's behalf without naming buyers."""
    venue = SalesVenue(
        code="heritage",
        name="Heritage",
        sales_venue_kind_id=require_code(db, SalesVenueKind, "auction_house", "kind"),
        commission_rate=Decimal("0.20"),
    )
    db.add(venue)
    db.flush()
    return venue


@pytest.fixture
def received_item(make_item: Callable[..., InventoryItem]) -> InventoryItem:
    """An item ready to be offered: received, not yet listed anywhere.

    `make_item` (below) defaults an item's status to `received`, which is
    exactly the state `offering_writes.offer` requires -- so this fixture is
    only a name for that default, not a second way to build one.
    """
    return make_item()


@pytest.fixture
def ebay_listing(
    db: Session, received_item: InventoryItem, ebay_venue: SalesVenue
) -> Listing:
    """An item offered on eBay, with the claim that `offer` creates.

    Built through `offering_writes.offer` rather than a bare `Listing(...)`
    so this listing carries the same `OfferClaim` a real eBay offer would --
    which is the fixture's whole point: it is a listing the shop's checkout
    guards must refuse, not a listing that merely looks like one.
    """
    return offering_writes.offer(
        db,
        item=received_item,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("120.00"),
        title="1881-S Morgan Dollar",
        description="",
        external_id="123456",
    )


@pytest.fixture
def heritage_listing(
    db: Session, received_item: InventoryItem, heritage_venue: SalesVenue
) -> Listing:
    """An item consigned to an auction house, which ships what it sells.

    Same shape as `ebay_listing`, on a venue whose kind is `auction_house`
    rather than `marketplace` -- the distinction `record_sale` reads to pick
    an order's starting status.
    """
    return offering_writes.offer(
        db,
        item=received_item,
        venue=heritage_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("500.00"),
        title="1893-S Morgan Dollar",
        description="",
        external_id=None,
    )


@pytest.fixture
def stored_then_ebay(
    db: Session, listing: Listing, ebay_venue: SalesVenue
) -> tuple[Listing, Listing]:
    """A store listing paused because the same item is also offered on eBay.

    Built in that order, through `offering_writes.offer`, so the store
    listing carries the real `paused` state and `paused_by_listing_id` an
    actual second offer produces -- not a fixture that merely looks paused.
    """
    ebay_listing_ = offering_writes.offer(
        db,
        item=listing.inventory_item,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("120.00"),
        title="1881-S Morgan Dollar",
        description="",
        external_id="123456",
    )
    db.flush()
    db.refresh(listing)
    return listing, ebay_listing_


@pytest.fixture
def three_item_costs() -> list[Decimal]:
    """Three equal cost bases -- the case an equal split cannot divide evenly."""
    return [Decimal("40.00"), Decimal("40.00"), Decimal("40.00")]


# --------------------------------------------------------------------------
# Catalogue fixtures
#
# A catalogue entry is a `listing` plus the `inventory_item` behind it, so a
# fixture has to build both. The item carries what the object *is*; the
# listing carries what it is being sold for.
# --------------------------------------------------------------------------


def _code_id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def _grade_ids(db: Session, grade: object) -> dict[str, int | None]:
    """A fixture grade as collectors write it (MS64), as the two columns."""
    code, strike = split_fields(str(grade), None) if grade else (None, None)
    return {
        "grade_id": _code_id(db, Grade, code) if code else None,
        "strike_type_id": _code_id(db, StrikeType, strike) if strike else None,
    }


def build_item(db: Session, **overrides: object) -> InventoryItem:
    """One inventory item, with every NOT NULL classifier resolved.

    Separate from `build_listing` because an item need not be for sale --
    most of the collection is not, and the search tests care about items
    rather than about what is offered.
    """
    # `storage_quantity` is this fixture's own spelling of the piece count and
    # `piece_count` is the column's own name. Both are popped, so whichever
    # spelling the caller used cannot also reach the constructor through
    # `**overrides` and collide with the keyword below -- which is what made
    # `make_item(piece_count=4)` raise "got multiple values for piece_count".
    piece_count = overrides.pop("storage_quantity", overrides.pop("piece_count", 1))
    # `**overrides: object` erases the value type. These keys are classifier
    # codes by this helper's contract, and `_code_id` fails loudly (no row
    # found) rather than silently on anything that is not one.
    kind = cast("str", overrides.pop("kind", "coin"))

    item = InventoryItem(
        source_title=overrides.pop("title", "1881-S Morgan Silver Dollar"),
        description=overrides.pop("description", "Test fixture item."),
        year_start=overrides.pop("year_start", 1881),
        year_end=overrides.pop("year_end", None),
        piece_count=piece_count,
        item_kind_id=_code_id(db, ItemKind, kind),
        country_id=_code_id(db, Country, "US"),
        storage_form_id=_code_id(db, StorageForm, "single"),
        authenticity_id=_code_id(db, Authenticity, "unverified"),
        status_id=_code_id(db, ItemStatus, "received"),
        disposition_id=_code_id(db, Disposition, "held"),
        valuation_basis_id=_code_id(db, ValuationBasis, "numismatic"),
        **{
            **_grade_ids(db, "MS64"),
            **overrides,
        },
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def make_item(db: Session) -> Callable[..., InventoryItem]:
    """Factory for inventory items inside one test."""

    def factory(**overrides: object) -> InventoryItem:
        return build_item(db, **overrides)

    return factory


def build_listing(db: Session, **overrides: object) -> Listing:
    """One catalogue entry, with every NOT NULL classifier resolved.

    An explicit ``inventory_item_id`` override reuses that item instead of
    creating a new one -- how a second listing on the same item is built, as
    the one-active-claim tests need.
    """
    inventory_item_id = overrides.pop("inventory_item_id", None)
    if inventory_item_id is None:
        item_fields = {
            "source_title": overrides.pop("title", "1881-S Morgan Silver Dollar"),
            "description": overrides.pop("description", "Test fixture item."),
            "year_start": overrides.pop("year_start", 1881),
            "year_end": overrides.pop("year_end", None),
            "piece_count": overrides.pop("storage_quantity", 1),
        }
        # `**overrides: object` erases the value type. These three are
        # classifier codes by this helper's contract -- `country` may also be
        # None, meaning "no country" -- and `_code_id` fails loudly (no row
        # found) rather than silently on anything that is not one.
        kind = cast("str", overrides.pop("kind", "coin"))
        country = cast("str | None", overrides.pop("country", "US"))
        grade = overrides.pop("grade", "MS64")

        item = InventoryItem(
            **item_fields,
            item_kind_id=_code_id(db, ItemKind, kind),
            country_id=_code_id(db, Country, country) if country else None,
            **_grade_ids(db, grade),
            storage_form_id=_code_id(db, StorageForm, "single"),
            authenticity_id=_code_id(db, Authenticity, "unverified"),
            status_id=_code_id(db, ItemStatus, "received"),
            disposition_id=_code_id(db, Disposition, "listed"),
            valuation_basis_id=_code_id(db, ValuationBasis, "numismatic"),
        )
        db.add(item)
        db.flush()
        inventory_item_id = item.id

    listing = Listing(
        inventory_item_id=inventory_item_id,
        price=overrides.pop("price", Decimal("189.00")),
        currency_id=_code_id(db, Currency, "USD"),
        quantity_available=overrides.pop("quantity_available", 5),
        status=(
            ListingStatus.active
            if overrides.pop("is_active", True)
            else ListingStatus.ended
        ),
        sales_venue_id=overrides.pop("sales_venue_id", store_venue_id(db)),
        **overrides,
    )
    db.add(listing)
    db.commit()
    db.refresh(listing)
    return listing


def item_of(listing: Listing) -> InventoryItem:
    """The one item a listing names, for a test that depends on there being one.

    `Listing.inventory_item` became optional when a listing was allowed to
    name a sales lot instead of a single coin (selling design, phase 3): a
    lot listing leaves `inventory_item_id` NULL and reaches its coins through
    `sales_lot_item`. Every listing `build_listing` makes is a single-item
    one, so a test built on it genuinely depends on the item being there.

    Stating that dependence once, here, is the point. Without it each use
    site would fail on `None` having no `item_code` -- a message about the
    attribute, naming neither the listing nor the assumption that broke. With
    it the assumption is checked where it is made and fails by name, and the
    type narrows because the check is real rather than because a `cast` said
    so. A test that means to exercise a *lot* listing must not call this; it
    reads `sales_lot_item` the way the application does.
    """
    item = listing.inventory_item
    assert item is not None, (
        f"listing #{listing.id} names no single item -- it is a lot listing, "
        "whose members are reached through `sales_lot_item`"
    )
    return item


def item_id_of(listing: Listing) -> int:
    """The id of the one item a listing names. The precondition is `item_of`'s.

    Separate from `item_of` only to avoid loading the item for a caller that
    wants the id it already has on the row.
    """
    item_id = listing.inventory_item_id
    assert item_id is not None, (
        f"listing #{listing.id} names no single item -- it is a lot listing, "
        "whose members are reached through `sales_lot_item`"
    )
    return item_id


@pytest.fixture
def listing(db: Session) -> Listing:
    return build_listing(db)


@pytest.fixture
def make_listing(db: Session) -> Callable[..., Listing]:
    """Factory for additional catalogue entries within a test."""

    def _make(**overrides: object) -> Listing:
        overrides.pop("n", None)
        return build_listing(db, **overrides)

    return _make


def build_lot(
    db: Session,
    items: Sequence[InventoryItem],
    *,
    title: str = "Three Morgan Dollars",
    description: str = "",
) -> SalesLot:
    """An assembling lot holding these items, built through `lot_writes`.

    Through the writer rather than by hand, for the reason `ebay_listing`
    goes through `offering_writes.offer`: a lot assembled around the rules is
    a lot the rules have accepted, and a fixture that inserted the rows
    directly would state the membership rules a second time.
    """
    lot = lot_writes.create_lot(db, title=title, description=description)
    for item in items:
        lot_writes.add_member(db, lot, item)
    db.flush()
    return lot


@pytest.fixture
def make_lot(db: Session) -> Callable[..., SalesLot]:
    """Factory for sales lots within one test."""

    def _make(items: Sequence[InventoryItem], **overrides: str) -> SalesLot:
        return build_lot(db, items, **overrides)

    return _make


@pytest.fixture
def lot_of_three(db: Session, make_item: Callable[..., InventoryItem]) -> SalesLot:
    """An assembling lot of three items with deliberately uneven cost bases.

    `item_cost`, never `total_cost`: `total_cost` is a generated column
    (`item_cost + shipping_cost + sales_tax`) and cannot be assigned.
    `tax_rate=0` makes the two equal, which is what lets a share assertion be
    exact rather than approximate -- every other item in the suite carries
    the configured 0.0635.
    """
    costs = (Decimal("500.00"), Decimal("300.00"), Decimal("200.00"))
    items = [
        make_item(
            title=f"Lot member {index}",
            item_cost=cost,
            tax_rate=Decimal("0"),
        )
        for index, cost in enumerate(costs, start=1)
    ]
    return build_lot(db, items)


@pytest.fixture
def offered_lot_listing(
    db: Session, lot_of_three: SalesLot, ebay_venue: SalesVenue
) -> Listing:
    """A lot of three offered on eBay, with the three claims `offer` creates.

    Built through `offering_writes.offer` for the reason `ebay_listing` is:
    it carries the real claims a real offer produces, not a listing that
    merely looks like one. Price 1,000.00 against member costs of 500, 300
    and 200 -- so a cost-weighted division is 500.00 / 300.00 / 200.00 and an
    equal one is not, which is what makes the weighting assertions in Task 4
    able to fail.
    """
    return offering_writes.offer(
        db,
        lot=lot_of_three,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("1000.00"),
        title="Three Morgan Dollars",
        description="",
        external_id="987654",
    )


@pytest.fixture
def store_lot_listing(db: Session, lot_of_three: SalesLot) -> Listing:
    """A lot of three offered in the web store, so the catalogue can serve it.

    `quantity=1` is the default and is also what `ck_listing_lot_quantity_one`
    requires -- note that `make_listing` would default it to 5, which is why
    this fixture goes through `offering_writes.offer` instead.
    """
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    return offering_writes.offer(
        db,
        lot=lot_of_three,
        venue=store,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("1200.00"),
        title="Three Morgan Dollars",
        description="Three coins, one price.",
        external_id=None,
    )
