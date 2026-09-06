"""Shared test fixtures.

Tests run against a dedicated PostgreSQL database (``<dbname>_test``) that is
dropped and recreated once per session, so they never touch development data.

Each test then runs inside a transaction that is rolled back afterwards, which
keeps tests independent without paying to rebuild the schema every time.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models import Coin, CoinKind, User, UserRole
from app.models.views import CREATE_VIEWS
from app.security import hash_password
from app.seeding import seed_all


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
                "WHERE datname = :name AND pid <> pg_backend_pid()"
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
        for statement in CREATE_VIEWS:
            conn.execute(text(statement))

    # Reference data is seeded once and committed, not per test: inventory_item
    # has NOT NULL foreign keys into half a dozen classifier tables, so almost
    # nothing can be inserted without it. The per-test transaction rolls back
    # around this, leaving the vocabulary in place.
    with Session(test_engine) as session:
        seed_all(session)

    yield test_engine

    test_engine.dispose()
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
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


@pytest.fixture
def coin(db: Session) -> Coin:
    item = Coin(
        sku="TEST-MORGAN-1881S",
        title="1881-S Morgan Silver Dollar",
        description="Test fixture item.",
        kind=CoinKind.coin,
        country="United States",
        year=1881,
        denomination="1 Dollar",
        grade="MS-64",
        price=Decimal("189.00"),
        quantity=5,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def make_coin(db: Session):
    """Factory for additional catalogue items within a test."""

    def _make(**overrides) -> Coin:
        defaults = {
            "sku": f"TEST-SKU-{overrides.pop('n', 1)}",
            "title": "Test Item",
            "price": Decimal("10.00"),
            "quantity": 3,
        }
        defaults.update(overrides)
        item = Coin(**defaults)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    return _make
