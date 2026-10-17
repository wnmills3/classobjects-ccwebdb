"""The settings object must never display its secrets.

Anything that prints the object -- pytest naming it in an error, a traceback,
a log line, a debugger -- calls its repr, which would otherwise list every
field and value, `JWT_SECRET` included.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config import PUBLISHED_SECRETS, settings
from pydantic import ValidationError

#: Fields whose values are credentials. `database_url` is one because it
#: carries the database password.
SECRET_FIELDS = ("database_url", "jwt_secret", "first_admin_password")


def test_printing_the_settings_shows_no_secret() -> None:
    """Compare names, not values, so a failure cannot print the secret either.

    `assert value not in shown` would have pytest print both sides on failure
    -- leaking the very value this test exists to protect.
    """
    shown = repr(settings) + str(settings)
    leaked = [name for name in SECRET_FIELDS if getattr(settings, name) in shown]
    assert leaked == [], f"printing the settings shows: {leaked}"


def test_the_secrets_are_still_readable_by_the_code_that_needs_them() -> None:
    """Hiding them from display must not hide them from use."""
    assert all(getattr(settings, name) for name in SECRET_FIELDS)


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


def test_a_misspelt_name_in_env_is_refused(tmp_path: Path) -> None:
    """A misspelt JWT_SECRET is an error, not a setting left at its default."""
    from app.config import Settings

    env = tmp_path / ".env"
    env.write_bytes(b"JWT_SECRT=" + b"k" * 64 + b"\n")
    with pytest.raises(ValidationError, match="jwt_secrt"):
        Settings(_env_file=env, jwt_secret="k" * 32)
