"""The migration that lets a signature pair have a Register of the Treasury.

Built on a throwaway database seeded at the newest revision and taken back
to the one before it, then upgraded: every small-size pair keeps its
secretary, and a large-size pair can then be recorded with a register.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.command import downgrade, upgrade
from alembic.config import Config
from app.seeding import seed_all
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.conftest import TEST_URL, drop_database

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "b9d4f2a6e8c1"
AFTER = "c8e2a5f1d7b3"


def _config(url: str) -> Config:
    """Alembic, pointed at the database at `url` and no other."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def before_url() -> Iterator[str]:
    """A seeded database taken back to the revision before."""
    name = f"{TEST_URL.database}_large_sigs"
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
        upgrade(_config(url), "head")
        engine = create_engine(url)
        with Session(engine) as session:
            seed_all(session)
            session.commit()
        engine.dispose()
        downgrade(_config(url), BEFORE)
        yield url
    finally:
        with admin.connect() as conn:
            drop_database(conn, name)
        admin.dispose()


def test_small_size_pairs_keep_their_secretary_and_a_register_can_be_added(
    before_url: str,
) -> None:
    engine = create_engine(before_url)
    with engine.connect() as conn:
        before = dict(
            conn.execute(text("select code, secretary from signature_combination"))
            .tuples()
            .all()
        )
    upgrade(_config(before_url), AFTER)

    with engine.connect() as conn:
        after = {
            code: (secretary, register)
            for code, secretary, register in conn.execute(
                text("select code, secretary, register from signature_combination")
            )
        }
    assert after["julian_morgenthau"] == ("Henry Morgenthau Jr.", None)
    # The downgrade gave the seeded large-size pairs an empty secretary; the
    # upgrade leaves every row as it found it.
    assert {code: pair[0] for code, pair in after.items()} == before
    assert {pair[1] for pair in after.values()} == {None}

    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into signature_combination (code, label, treasurer,"
                " register, sort_order, is_active, source) values"
                " ('test_pair', 'Test / Pair', 'A. Treasurer', 'A. Register',"
                " 0, true, 'manual')"
            )
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "insert into signature_combination (code, label, treasurer,"
                " secretary, register, sort_order, is_active, source) values"
                " ('both', 'Both', 'A. Treasurer', 'A. Secretary',"
                " 'A. Register', 0, true, 'manual')"
            )
        )
    engine.dispose()
