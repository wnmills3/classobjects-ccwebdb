"""The migration that reads the hand-added Light Green seal as green.

Built on a throwaway database seeded at the newest revision and taken back
to the one before it, given a Light Green seal as the live database has,
then upgraded.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from app.seeding import seed_all
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tests.conftest import TEST_URL, drop_database

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "e7b3d9a1c5f2"
AFTER = "b9d4f2a6e8c1"


def _config(url: str) -> Config:
    """Alembic, pointed at the database at `url` and no other."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _to(url: str, revision: str) -> None:
    """Migrate the database at `url` up to the revision."""
    upgrade(_config(url), revision)


@pytest.fixture
def before_url() -> Iterator[str]:
    """A seeded database at the revision before, with a Light Green seal."""
    name = f"{TEST_URL.database}_seal_base"
    admin = create_engine(
        TEST_URL.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        drop_database(conn, name)
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_URL.set(database=name).render_as_string(hide_password=False)
    try:
        # Seeded as the models stand, at the newest revision, then taken
        # back: the seed is written for today's columns.
        _to(url, "head")
        engine = create_engine(url)
        with Session(engine) as session:
            seed_all(session)
            session.commit()
        downgrade(_config(url), BEFORE)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into seal_color (code, label, sort_order, is_active,"
                    " source) values ('light_green', 'Light Green Seal', 500,"
                    " true, 'manual')"
                )
            )
        engine.dispose()
        yield url
    finally:
        with admin.connect() as conn:
            drop_database(conn, name)
        admin.dispose()


def test_light_green_is_read_as_green_and_no_other_color_changes(
    before_url: str,
) -> None:
    _to(before_url, AFTER)

    engine = create_engine(before_url)
    with engine.connect() as conn:
        bases = dict(
            conn.execute(
                text(
                    "select s.code, b.code from seal_color s"
                    " left join seal_color b on b.id = s.base_seal_color_id"
                )
            )
            .tuples()
            .all()
        )
    engine.dispose()
    assert bases.pop("light_green") == "green"
    assert set(bases.values()) == {None}
