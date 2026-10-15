"""The migration that brings recorded Friedberg numbers to the kept case.

Built on a throwaway database taken to the revision before it, given the
numbers, then upgraded -- the path the live database takes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.command import upgrade
from alembic.config import Config
from app.fr_format import normalize_fr
from sqlalchemy import create_engine, text

from tests.conftest import TEST_URL, drop_database

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "b8e2d4f6a1c3"


def _to(url: str, revision: str) -> None:
    """Migrate the database at `url`, and no other, up to the revision."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    upgrade(config, revision)


@pytest.fixture
def before_url() -> Iterator[str]:
    """A database migrated up to the revision before the letter's case."""
    name = f"{TEST_URL.database}_fr_letter"
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


def _record(url: str, numbers: list[str]) -> None:
    """Record each number as a bare row, in the case it is given."""
    engine = create_engine(url)
    with engine.begin() as conn:
        for number in numbers:
            conn.execute(
                text(
                    "insert into friedberg_number "
                    "(fr_number, source, created_at, updated_at) "
                    "values (:n, 'manual', now(), now())"
                ),
                {"n": number},
            )
    engine.dispose()


def _numbers(url: str) -> list[tuple[str, bool]]:
    """Each recorded number with whether the database reads it as a mule's."""
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("select fr_number, is_mule from friedberg_number order by id")
        ).all()
    engine.dispose()
    return [(number, mule) for number, mule in rows]


def test_each_number_takes_the_case_a_new_one_is_kept_in(before_url: str) -> None:
    typed = ["12a", "12A-B", "9912b-c*", "9901M", "9902m", "9907-Em", "1", "9908-B LGS"]
    _record(before_url, typed)

    _to(before_url, "head")

    kept = _numbers(before_url)
    assert [number for number, _ in kept] == [
        "12A",
        "12A-B",
        "9912B-C*",
        "9901m",
        "9902m",
        "9907-Em",
        "1",
        "9908-B LGS",
    ]
    # The rule the API applies to a number typed today gives the same text.
    assert [number for number, _ in kept] == [normalize_fr(n) for n in typed]
    # `9901M` was a plain number to the database; its `m` now reads as a mule's.
    assert [mule for _, mule in kept] == [
        False,
        False,
        False,
        True,
        True,
        True,
        False,
        False,
    ]


def test_two_numbers_that_would_become_one_stop_the_upgrade(
    before_url: str,
) -> None:
    _record(before_url, ["12a", "12A", "9901M", "9901m", "77c"])

    with pytest.raises(RuntimeError) as refused:
        _to(before_url, "head")

    assert "12A and 12a" in str(refused.value)
    assert "9901M and 9901m" in str(refused.value)
    assert "77" not in str(refused.value)
    # Nothing was changed, the number that clashed with nothing included.
    assert [number for number, _ in _numbers(before_url)] == [
        "12a",
        "12A",
        "9901M",
        "9901m",
        "77c",
    ]
