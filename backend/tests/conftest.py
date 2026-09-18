"""Shared test fixtures.

Tests run against a dedicated PostgreSQL database (``<dbname>_test``) that is
dropped and recreated once per session, so they never touch development data.

Each test then runs inside a transaction that is rolled back afterwards, which
keeps tests independent without paying to rebuild the schema every time.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from app.config import settings
from app.database import Base, get_db
from app.grades import GRADE_DISPLAY_SQL, split_fields
from app.main import app
from app.models import (
    Authenticity,
    Country,
    Currency,
    Disposition,
    Grade,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    ListingStatus,
    StorageForm,
    StrikeType,
    User,
    UserRole,
    ValuationBasis,
)
from app.models.views import CREATE_VIEWS
from app.sales_venues import ensure_store_venue, store_venue_id
from app.security import hash_password
from app.seeding import seed_all
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
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
    # models rather than by running the migrations, so the migration that
    # installs it on a real deployment never runs here.
    with test_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch"))

    # Reference data is seeded once and committed, not per test: inventory_item
    # has NOT NULL foreign keys into half a dozen classifier tables, so almost
    # nothing can be inserted without it. The per-test transaction rolls back
    # around this, leaving the vocabulary in place.
    with Session(test_engine) as session:
        seed_all(session)
        # The migration creates the web store platform on a real database;
        # this one is built from the models, so it is created here.
        ensure_store_venue(session)
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


# --------------------------------------------------------------------------
# Catalogue fixtures
#
# A catalogue entry is a `listing` plus the `inventory_item` behind it, so a
# fixture has to build both. The item carries what the object *is*; the
# listing carries what it is being sold for.
# --------------------------------------------------------------------------


def _code_id(db: Session, model: type, code: str) -> int:
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
    item = InventoryItem(
        source_title=overrides.pop("title", "1881-S Morgan Silver Dollar"),
        description=overrides.pop("description", "Test fixture item."),
        year_start=overrides.pop("year_start", 1881),
        year_end=overrides.pop("year_end", None),
        piece_count=overrides.pop("storage_quantity", 1),
        item_kind_id=_code_id(db, ItemKind, overrides.pop("kind", "coin")),
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
        kind = overrides.pop("kind", "coin")
        country = overrides.pop("country", "US")
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


@pytest.fixture
def listing(db: Session) -> Listing:
    return build_listing(db)


@pytest.fixture
def make_listing(db: Session) -> None:
    """Factory for additional catalogue entries within a test."""

    def _make(**overrides: object) -> Listing:
        overrides.pop("n", None)
        return build_listing(db, **overrides)

    return _make
