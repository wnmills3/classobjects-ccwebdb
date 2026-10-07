"""`app.pass_cli`: the command line the passes that write History share."""

from __future__ import annotations

import argparse

import pytest
from app import pass_cli
from app.models import User
from sqlalchemy.orm import Session


def _parser() -> argparse.ArgumentParser:
    """A pass's parser, with the two shared arguments and one of its own."""
    parser = argparse.ArgumentParser(prog="a_pass")
    pass_cli.add_commit_arguments(parser, "write the changes")
    parser.add_argument("--show", type=int, default=3)
    return parser


def _args(*argv: str) -> argparse.Namespace:
    """`argv` as the shared parser reads it."""
    return pass_cli.parse_args(_parser(), list(argv))


class _Write:
    """Stands in for a pass's `apply`: remembers who it was called for."""

    def __init__(self) -> None:
        """Nobody yet."""
        self.calls: list[int] = []

    def __call__(self, user_id: int) -> dict[str, int]:
        """Record the person and answer what a pass would report as written."""
        self.calls.append(user_id)
        return {"titles": 2}


@pytest.fixture
def commits(db: Session, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each commit `pass_cli` makes on `db`, counted without ending the test's own."""
    made: list[str] = []
    monkeypatch.setattr(db, "commit", lambda: made.append("commit"))
    return made


def test_a_dry_run_needs_no_person() -> None:
    args = _args()
    assert (args.commit, args.by, args.show) == (False, None, 3)


def test_a_commit_that_names_nobody_is_refused_before_anything_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as stopped:
        _args("--commit")
    assert stopped.value.code == 2
    assert "--commit needs --by" in capsys.readouterr().err


def test_a_commit_with_a_person_parses_with_the_pass_s_own_arguments() -> None:
    args = _args("--commit", "--by", "admin@example.com", "--show", "5")
    assert (args.commit, args.by, args.show) == (True, "admin@example.com", 5)


def test_a_person_is_found_whatever_capitals_the_address_was_typed_in(
    db: Session, admin_user: User, customer_user: User
) -> None:
    assert pass_cli.user_id_by_email(db, "Admin@Example.COM") == admin_user.id
    assert pass_cli.user_id_by_email(db, "customer@example.com") == customer_user.id


def test_an_address_nobody_has_finds_no_one(db: Session, admin_user: User) -> None:
    assert pass_cli.user_id_by_email(db, "nobody@example.com") is None


def test_a_dry_run_says_so_and_writes_nothing(
    db: Session,
    admin_user: User,
    commits: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    write = _Write()
    # `--by` is given: it is `--commit`, not a named person, that writes.
    status = pass_cli.commit_or_report(db, _args("--by", admin_user.email), write)

    assert status == 0
    assert write.calls == []
    assert commits == []
    assert capsys.readouterr().out.strip() == pass_cli.DRY_RUN


def test_a_commit_for_an_unknown_person_stops_before_writing(
    db: Session,
    admin_user: User,
    commits: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    write = _Write()
    args = _args("--commit", "--by", "nobody@example.com")
    status = pass_cli.commit_or_report(db, args, write)

    assert status == 1
    assert write.calls == []
    assert commits == []
    printed = capsys.readouterr()
    assert printed.err.strip() == "no user nobody@example.com"
    assert printed.out == ""


def test_a_commit_writes_as_the_person_named_then_commits_and_reports(
    db: Session,
    admin_user: User,
    customer_user: User,
    commits: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    write = _Write()
    args = _args("--commit", "--by", "ADMIN@example.com")
    status = pass_cli.commit_or_report(db, args, write)

    assert status == 0
    # The admin, not merely some account: a customer exists beside them.
    assert write.calls == [admin_user.id]
    assert commits == ["commit"]
    assert capsys.readouterr().out.strip() == "written: {'titles': 2}"
