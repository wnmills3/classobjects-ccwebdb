"""Which database the test session may build and drop.

The `engine` fixture drops the database `conftest._test_database_url` names
before it creates it, so that function is what stands between a mistyped
`TEST_DATABASE_URL` and a real database. Nothing here opens a connection.
"""

from __future__ import annotations

import pytest
from app.config import settings
from sqlalchemy.engine import make_url

from tests.conftest import _test_database_url


def _elsewhere(database: str) -> str:
    """The application's own URL, pointed at another database on the same server."""
    return (
        make_url(settings.database_url)
        .set(database=database)
        .render_as_string(hide_password=False)
    )


def test_the_default_is_the_configured_database_with_test_appended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    configured = make_url(settings.database_url).database

    assert _test_database_url().database == f"{configured}_test"


def test_an_override_naming_a_test_database_is_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", _elsewhere("scratch_test"))

    assert _test_database_url().database == "scratch_test"


def test_an_override_naming_the_applications_database_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The URL from `.env`, pasted in, must never reach `DROP DATABASE`."""
    monkeypatch.setenv("TEST_DATABASE_URL", settings.database_url)

    with pytest.raises(RuntimeError, match="would drop"):
        _test_database_url()


@pytest.mark.parametrize("database", ["scratch", "scratch_test_copy", "test"])
def test_an_override_not_named_as_a_test_database_is_refused(
    monkeypatch: pytest.MonkeyPatch, database: str
) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", _elsewhere(database))

    with pytest.raises(RuntimeError, match="_test"):
        _test_database_url()


def test_the_applications_own_name_is_refused_even_when_it_ends_in_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A development database called `x_test` is still not the suite's to drop."""
    own = _elsewhere("dev_test")
    monkeypatch.setattr(settings, "database_url", own)
    monkeypatch.setenv("TEST_DATABASE_URL", own)

    with pytest.raises(RuntimeError, match="would drop"):
        _test_database_url()
