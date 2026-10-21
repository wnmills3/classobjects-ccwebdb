"""The settings object must never display its secrets.

Anything that prints a `Settings` -- pytest naming it in an error, a traceback,
a log line, a debugger -- calls its repr, which would otherwise list every
field and value, `JWT_SECRET` included.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import PUBLISHED_SECRETS, Settings
from pydantic import ValidationError

#: Fields whose values are credentials. `database_url` is one because it
#: carries the database password.
SECRET_FIELDS = ("database_url", "jwt_secret", "first_admin_password")

#: A value of its own for each secret, so the tests do not depend on what the
#: checkout's .env holds: an empty one there is "found" in any text, and a
#: real one could be printed by a failure.
KNOWN_SECRETS = {
    "database_url": "postgresql+psycopg://u:dbpass-q7Kx2@db.invalid:5432/d",
    "jwt_secret": "jwt-q7Kx2" * 4,
    "first_admin_password": "adminpass-q7Kx2",
}
assert set(KNOWN_SECRETS) == set(SECRET_FIELDS)


def _settings_holding_known_secrets() -> Settings:
    """Settings carrying `KNOWN_SECRETS`, the environment and .env ignored."""
    return Settings(
        _env_file=None,
        database_url=KNOWN_SECRETS["database_url"],
        jwt_secret=KNOWN_SECRETS["jwt_secret"],
        first_admin_password=KNOWN_SECRETS["first_admin_password"],
    )


def test_printing_the_settings_shows_no_secret() -> None:
    """Compare names, not values, so a failure cannot print the secret either.

    `assert value not in shown` would have pytest print both sides on failure.
    """
    held = _settings_holding_known_secrets()
    shown = repr(held) + str(held)
    leaked = [name for name, value in KNOWN_SECRETS.items() if value in shown]
    assert leaked == [], f"printing the settings shows: {leaked}"


def test_every_secret_field_is_declared_hidden() -> None:
    """The declaration, not a value: holds whatever this checkout's .env says."""
    shown = [name for name in SECRET_FIELDS if Settings.model_fields[name].repr]
    assert shown == []


def test_the_secrets_are_still_readable_by_the_code_that_needs_them() -> None:
    """Hiding them from display must not hide them from use."""
    held = _settings_holding_known_secrets()
    assert {name: getattr(held, name) for name in SECRET_FIELDS} == KNOWN_SECRETS


def test_the_photograph_library_defaults_beside_the_repository() -> None:
    """Unconfigured, the import pass looks next to the checkout."""
    from app.config import REPO_ROOT, Settings

    assert Settings().photo_library_root == REPO_ROOT / "photos"


def test_the_photograph_library_can_be_moved_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PHOTO_LIBRARY_ROOT overrides the default, like media_root's own var."""
    from app.config import Settings

    monkeypatch.setenv("PHOTO_LIBRARY_ROOT", "/tmp/elsewhere")
    assert Settings().photo_library_root == Path("/tmp/elsewhere")


def test_cors_follows_the_frontend_port(monkeypatch: pytest.MonkeyPatch) -> None:
    """Move the dev server and the API still accepts its pages' requests."""
    from app.config import Settings

    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.setenv("CCWEB_FRONTEND_PORT", "6000")
    assert Settings().cors_origins == [
        "http://localhost:6000",
        "http://127.0.0.1:6000",
    ]


def test_cors_defaults_to_the_dev_servers_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset, the two addresses the dev server answers on, as before."""
    from app.config import Settings

    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("CCWEB_FRONTEND_PORT", raising=False)
    assert Settings(_env_file=None, jwt_secret="k" * 32).cors_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]


def test_the_archive_folder_defaults_beside_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Database dumps and media copies go next to the checkout, not in it."""
    from app.config import REPO_ROOT, Settings

    monkeypatch.delenv("CCWEB_ARCHIVE_DIR", raising=False)
    assert (
        Settings(_env_file=None, jwt_secret="k" * 32).ccweb_archive_dir
        == REPO_ROOT.parent / "ccwebdb-backups"
    )


@pytest.mark.parametrize(
    "name", ["CCWEB_ARCHIVE_DIR", "MEDIA_ROOT", "PHOTO_LIBRARY_ROOT", "WEB_MAX_PX"]
)
def test_an_application_setting_left_empty_means_its_default(
    tmp_path: Path, name: str
) -> None:
    """`MEDIA_ROOT=` must not mean the folder the API happened to start in."""
    from app.config import Settings

    env = tmp_path / ".env"
    env.write_bytes(f"{name}=\n".encode())
    field = name.lower()
    assert getattr(Settings(_env_file=env, jwt_secret="k" * 32), field) == getattr(
        Settings(_env_file=None, jwt_secret="k" * 32), field
    )


def test_an_empty_text_setting_stays_empty(tmp_path: Path) -> None:
    """For text, empty is a value: TIME_ZONE= means the host's zone."""
    from app.config import Settings

    env = tmp_path / ".env"
    env.write_bytes(b"FIRST_ADMIN_EMAIL=\n")
    assert Settings(_env_file=env, jwt_secret="k" * 32).first_admin_email == ""


def test_the_backups_follow_the_archive_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both default backup locations are under CCWEB_ARCHIVE_DIR."""
    from app import media_backup, workbook_backup
    from app.config import settings

    monkeypatch.setattr(settings, "ccweb_archive_dir", tmp_path)
    assert media_backup.default_folder() == tmp_path / "media"
    assert workbook_backup.default_path().parent == tmp_path


@pytest.mark.parametrize(
    ("key", "problem"),
    [
        ("", "not set"),
        ("q" * 31, "shorter than 32"),
        *[(published, "published") for published in sorted(PUBLISHED_SECRETS)],
    ],
)
def test_a_signing_key_a_stranger_could_know_is_refused(key: str, problem: str) -> None:
    """Anyone holding the key can sign a manager's token: refuse to start.

    And the refusal does not print the key it refused.
    """
    from app.config import Settings

    with pytest.raises(ValidationError, match=problem) as refused:
        Settings(jwt_secret=key)
    if key:
        assert key not in str(refused.value)


def test_a_long_unpublished_signing_key_is_accepted() -> None:
    """The other side of the refusal: a real key starts."""
    from app.config import Settings

    assert Settings(jwt_secret="k" * 32).jwt_secret == "k" * 32


def _with_database(url: str) -> Settings:
    """Settings naming this database, the environment's and .env's ignored."""
    from app.config import Settings

    return Settings(_env_file=None, jwt_secret="k" * 32, database_url=url)


@pytest.mark.parametrize(
    "host", ["db.example.com", "192.0.2.10", "[2001:db8::1]", "ccwebdb-db"]
)
def test_the_published_database_password_is_refused_on_another_machine(
    host: str,
) -> None:
    """The development password is in this repository: no password elsewhere.

    And the refusal names the setting, not the address it refused.
    """
    url = f"postgresql+psycopg://ccwebdb:devpassword@{host}:5432/ccwebdb"
    with pytest.raises(ValidationError, match="DATABASE_URL") as refused:
        _with_database(url)
    assert "devpassword" not in str(refused.value)
    assert "real password" in str(refused.value)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://ccwebdb:devpassword@localhost:5432/ccwebdb",
        "postgresql+psycopg://ccwebdb:devpassword@127.0.0.1:5432/ccwebdb",
        "postgresql+psycopg://ccwebdb:devpassword@[::1]:5432/ccwebdb",
        "postgresql+psycopg://ccwebdb:devpassword@/ccwebdb",
        "postgresql+psycopg://ccwebdb:devpassword@/ccwebdb?host=/var/run/postgresql",
    ],
)
def test_the_published_database_password_is_accepted_on_this_machine(
    url: str,
) -> None:
    """On the machine's own cluster it guards nothing a local user lacks."""
    assert _with_database(url).database_url == url


def test_a_real_database_password_is_accepted_anywhere() -> None:
    """The other side of the refusal: a server with its own password starts."""
    url = "postgresql+psycopg://ccwebdb:a-real-one-q7Kx2@db.example.com:5432/ccwebdb"
    assert _with_database(url).database_url == url


def test_the_shop_name_and_owner_are_empty_unless_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty is the frontend's own defaults; .env may name both."""
    monkeypatch.delenv("VITE_SHOP_NAME", raising=False)
    monkeypatch.delenv("VITE_SHOP_OWNER", raising=False)
    plain = Settings(_env_file=None, jwt_secret="k" * 32)
    assert (plain.vite_shop_name, plain.vite_shop_owner) == ("", "")

    monkeypatch.setenv("VITE_SHOP_NAME", "Example Coins")
    monkeypatch.setenv("VITE_SHOP_OWNER", "Example Coins LLC")
    named = Settings(_env_file=None, jwt_secret="k" * 32)
    assert (named.vite_shop_name, named.vite_shop_owner) == (
        "Example Coins",
        "Example Coins LLC",
    )


def test_a_misspelt_name_in_env_is_refused(tmp_path: Path) -> None:
    """A misspelt JWT_SECRET is an error, not a setting left at its default."""
    from app.config import Settings

    env = tmp_path / ".env"
    env.write_bytes(b"JWT_SECRT=" + b"k" * 64 + b"\n")
    with pytest.raises(ValidationError, match="jwt_secrt"):
        Settings(_env_file=env, jwt_secret="k" * 32)
