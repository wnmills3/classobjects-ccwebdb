"""The command line a pass over the stored items shares with the others.

A pass that writes History reports by default and writes only with
`--commit --by EMAIL`: every change it makes is logged under a person, so a
commit with nobody named is refused before anything is read. The passes
differ in what they plan and print; what is the same -- the two arguments,
that refusal, finding the person, and the commit -- is here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import User

#: What a pass prints when it was not asked to write.
DRY_RUN = "dry run: nothing written (--commit --by EMAIL to apply)"


def add_commit_arguments(parser: argparse.ArgumentParser, commit_help: str) -> None:
    """Give `parser` the `--commit` and `--by` arguments every such pass takes."""
    parser.add_argument("--commit", action="store_true", help=commit_help)
    parser.add_argument("--by", help="the person the History rows name (email)")


def parse_args(
    parser: argparse.ArgumentParser, argv: Sequence[str] | None
) -> argparse.Namespace:
    """Parse `argv`, refusing a `--commit` that names nobody."""
    args = parser.parse_args(argv)
    if args.commit and not args.by:
        parser.error("--commit needs --by: every change is logged under a person")
    return args


def user_id_by_email(db: Session, email: str) -> int | None:
    """The account with this email address, whatever its capitals; None for no one."""
    return db.scalar(select(User.id).where(func.lower(User.email) == email.lower()))


def commit_or_report(
    db: Session, args: argparse.Namespace, write: Callable[[int], object]
) -> int:
    """Finish a pass: say it was a dry run, or write as the person named.

    `write` is handed that person's id, makes the changes in `db` and returns
    what to print as written; the commit is made here. The exit status: 0
    for a dry run or a commit, 1 when `--by` names no account.
    """
    if not args.commit:
        print(DRY_RUN)
        return 0
    user_id = user_id_by_email(db, args.by)
    if user_id is None:
        print(f"no user {args.by}", file=sys.stderr)
        return 1
    written = write(user_id)
    db.commit()
    print(f"written: {written}")
    return 0
