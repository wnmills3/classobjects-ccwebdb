"""The migration that moves the golden dollars' compositions to manganese brass.

Built on a throwaway database taken to the revision before it and seeded,
with the three designs' rows put back to clad and the metal removed -- the
state the live database is in -- then upgraded.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.command import upgrade
from alembic.config import Config
from app.seeding import seed_all
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from tests.conftest import TEST_URL, drop_database

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "c4e9a2f7b1d8"
AFTER = "e7b3d9a1c5f2"
GOLDEN = ("sacagawea_dollar", "presidential_dollar", "american_innovation_dollar")


def _to(url: str, revision: str) -> None:
    """Migrate the database at `url`, and no other, up to the revision."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    upgrade(config, revision)


@pytest.fixture
def before_url() -> Iterator[str]:
    """A seeded database at the revision before, its golden dollars clad."""
    name = f"{TEST_URL.database}_brass"
    admin = create_engine(
        TEST_URL.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        drop_database(conn, name)
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_URL.set(database=name).render_as_string(hide_password=False)
    try:
        _to(url, BEFORE)
        engine = create_engine(url)
        with Session(engine) as session:
            seed_all(session)
            session.commit()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "update composition set metal_id ="
                    " (select id from metal where code = 'clad')"
                    " where metal_id = (select id from metal"
                    " where code = 'manganese_brass')"
                )
            )
            conn.execute(text("delete from metal where code = 'manganese_brass'"))
        engine.dispose()
        yield url
    finally:
        with admin.connect() as conn:
            drop_database(conn, name)
        admin.dispose()


def _metals(url: str) -> dict[str, str]:
    """Each design's composition metal, for the golden dollars and the Anthony."""
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select s.code, m.code from composition c"
                " join series s on s.id = c.series_id"
                " join metal m on m.id = c.metal_id"
                " where s.code in ('sacagawea_dollar', 'presidential_dollar',"
                " 'american_innovation_dollar', 'anthony_dollar')"
            )
        ).tuples()
        metals = dict(rows.all())
    engine.dispose()
    return metals


def test_the_golden_dollars_move_to_manganese_brass(before_url: str) -> None:
    assert set(_metals(before_url).values()) == {"clad"}

    _to(before_url, AFTER)

    metals = _metals(before_url)
    assert {metals[design] for design in GOLDEN} == {"manganese_brass"}
    # Copper-nickel clad, and not a golden dollar: left alone.
    assert metals["anthony_dollar"] == "clad"
