"""The settings object must never display its secrets.

On 2026-09-10 a failing test printed `JWT_SECRET` in full: pytest's
monkeypatch named the settings object in its error, and that meant calling
its repr, which listed every field and value. Anything that prints the
object -- a traceback, a log line, a debugger -- leaks the same way.
"""

from __future__ import annotations

from app.config import settings

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
