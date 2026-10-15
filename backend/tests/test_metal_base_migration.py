"""The migration that gives the hand-added plated metals their base metal.

Built on a throwaway database taken to the revision before it, given the
metals, then upgraded -- the path the live database takes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import create_engine, text

from tests.conftest import TEST_URL, drop_database

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "d2b6e8f3a5c7"
AFTER = "c4e9a2f7b1d8"


def _to(url: str, revision: str) -> None:
    """Migrate the database at `url`, and no other, up to the revision."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    upgrade(config, revision)


@pytest.fixture
def before_url() -> Iterator[str]:
    """A database migrated up to the revision before the base metal."""
    name = f"{TEST_URL.database}_metal_base"
    admin = create_engine(
        TEST_URL.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        drop_database(conn, name)
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_URL.set(database=name).render_as_string(hide_password=False)
    try:
        _to(url, BEFORE)
        yield url
    finally:
        with admin.connect() as conn:
            drop_database(conn, name)
        admin.dispose()


def test_each_plated_metal_takes_its_base_and_no_other_changes(
    before_url: str,
) -> None:
    engine = create_engine(before_url)
    # Plated rows first, so no id coincides with the row it should name.
    codes = ["24k_gold_plated_clad", "gold_plated_silver", "brass", "clad", "silver"]
    with engine.begin() as conn:
        for order, code in enumerate(codes):
            conn.execute(
                text(
                    "insert into metal (code, label, sort_order, is_active, source,"
                    " symbol, is_precious) values (:c, :c, :o, true, 'manual', '',"
                    " false) on conflict (code) do nothing"
                ),
                {"c": code, "o": order},
            )
    engine.dispose()

    _to(before_url, AFTER)

    engine = create_engine(before_url)
    with engine.connect() as conn:
        bases = dict(
            conn.execute(
                text(
                    "select m.code, b.code from metal m"
                    " left join metal b on b.id = m.base_metal_id"
                )
            )
            .tuples()
            .all()
        )
    engine.dispose()
    assert bases["gold_plated_silver"] == "silver"
    assert bases["24k_gold_plated_clad"] == "clad"
    assert bases["brass"] is None
    assert bases["silver"] is None
    assert bases["clad"] is None
