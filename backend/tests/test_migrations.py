"""Guard against the models and the Alembic migrations drifting apart."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.command import upgrade
from alembic.config import Config
from alembic.migration import MigrationContext
from app.database import Base
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url  # noqa: F401

from tests.conftest import TEST_URL

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def migrated_url() -> str:
    """A throwaway database built purely by running the migrations."""
    url = TEST_URL
    name = f"{url.database}_migrations"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    # hide_password=False is essential: str(url) would render it as "***".
    target = url.set(database=name).render_as_string(hide_password=False)
    try:
        yield target
    finally:
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
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_migrations_match_models(migrated_url: str) -> None:
    """`alembic upgrade head` must produce exactly what the models describe.

    If this fails, a model changed without a matching migration -- generate one
    with `alembic revision --autogenerate`.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", migrated_url)
    upgrade(config, "head")

    engine = create_engine(migrated_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    # alembic_version is created by Alembic itself and is not in our metadata.
    meaningful = [
        entry
        for entry in diff
        if not (
            isinstance(entry, tuple)
            and len(entry) > 1
            and getattr(entry[1], "name", None) == "alembic_version"
        )
    ]
    assert meaningful == [], f"models and migrations disagree: {meaningful}"
