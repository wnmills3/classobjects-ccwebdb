"""`python -m app.create_admin`: a manager for a new shop, with no demo data.

`main` is only ever called here **with this test's session**: called with
none it opens the application's own `SessionLocal`, the live database.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from app.config import settings
from app.create_admin import OK, REFUSED, USAGE, main
from app.models import InventoryItem, User, UserRole
from app.security import hash_password, verify_password
from sqlalchemy import func, select
from sqlalchemy.orm import Session

STRONG = "a-long-unpublished-one"


@pytest.fixture(autouse=True)
def _no_password_in_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever .env holds, the password is asked for unless a test sets it."""
    monkeypatch.setattr(settings, "first_admin_password", "")


def _typed(*answers: str) -> Callable[[str], str]:
    """A prompt that answers with these, in order, and fails if asked again."""
    queue = list(answers)

    def ask(_prompt: str) -> str:
        """The next answer."""
        return queue.pop(0)

    return ask


def _managers(db: Session) -> list[User]:
    """Every manager, oldest first."""
    return list(
        db.scalars(select(User).where(User.role == UserRole.manager).order_by(User.id))
    )


def _manager(db: Session, email: str) -> None:
    """An existing manager with this address."""
    db.add(
        User(
            email=email,
            full_name="Existing",
            hashed_password=hash_password(STRONG),
            role=UserRole.manager,
        )
    )
    db.commit()


def test_an_empty_database_gets_its_manager_and_nothing_else(db: Session) -> None:
    code = main(["--email", "Owner@Example.COM"], db=db, ask=_typed(STRONG, STRONG))

    assert code == OK
    managers = _managers(db)
    assert [m.email for m in managers] == ["Owner@example.com"]
    assert verify_password(STRONG, managers[0].hashed_password)
    assert db.scalar(select(func.count()).select_from(InventoryItem)) == 0


def test_the_password_comes_from_the_setting_when_it_is_set(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is asked: a prompt that is called fails the test."""
    monkeypatch.setattr(settings, "first_admin_password", STRONG)
    code = main(["--email", "owner@example.com"], db=db, ask=_typed())

    assert code == OK
    assert verify_password(STRONG, _managers(db)[0].hashed_password)


def test_two_different_entries_are_refused(db: Session) -> None:
    code = main(
        ["--email", "owner@example.com"], db=db, ask=_typed(STRONG, STRONG + "x")
    )
    assert code == REFUSED
    assert _managers(db) == []


@pytest.mark.parametrize("password", ["elevenchars", "adminpassword"])
def test_a_weak_password_is_refused_by_seeds_rule(db: Session, password: str) -> None:
    code = main(["--email", "owner@example.com"], db=db, ask=_typed(password, password))
    assert code == REFUSED
    assert _managers(db) == []


def test_a_database_with_a_manager_is_refused(db: Session) -> None:
    _manager(db, "first@example.com")
    code = main(["--email", "second@example.com"], db=db, ask=_typed(STRONG, STRONG))
    assert code == REFUSED
    assert [m.email for m in _managers(db)] == ["first@example.com"]


def test_another_manager_is_added_when_asked_for(db: Session) -> None:
    _manager(db, "first@example.com")
    code = main(
        ["--email", "second@example.com", "--another"],
        db=db,
        ask=_typed(STRONG, STRONG),
    )
    assert code == OK
    assert [m.email for m in _managers(db)] == [
        "first@example.com",
        "second@example.com",
    ]


def test_an_address_taken_in_any_case_is_refused(db: Session) -> None:
    _manager(db, "owner@example.com")
    code = main(
        ["--email", "OWNER@example.com", "--another"],
        db=db,
        ask=_typed(STRONG, STRONG),
    )
    assert code == REFUSED
    assert len(_managers(db)) == 1


@pytest.mark.parametrize(
    "argv", [[], ["--email", "not-an-address"], ["--email", "a@example.com", "-x"]]
)
def test_a_usage_error_exits_2_and_writes_nothing(db: Session, argv: list[str]) -> None:
    assert main(argv, db=db, ask=_typed()) == USAGE
    assert _managers(db) == []
