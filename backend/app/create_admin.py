"""Open a manager's account in a database, with no demo data.

    python -m app.create_admin --email EMAIL
    python -m app.create_admin --email EMAIL --another

For a new shop: `app.seed` adds the demo catalog as well, which a real shop
does not want. The password is `FIRST_ADMIN_PASSWORD` when that is set, in
the environment or `.env`; otherwise it is asked for twice. It must pass the
rule `app.seed` applies. A database that already has a manager is refused
unless `--another` is given.

Exit codes: 0 the account was made; 1 refused (a manager exists, the address
is taken, the password is weak or the two entries differ); 2 usage.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal
from .models import User, UserRole
from .security import account_by_email, hash_password, normalize_email
from .seed import weak_admin_password_problem

#: Exit codes.
OK, REFUSED, USAGE = 0, 1, 2


def _password(ask: Callable[[str], str]) -> str | None:
    """`FIRST_ADMIN_PASSWORD`, or one typed twice; None when the two differ."""
    if settings.first_admin_password:
        return settings.first_admin_password
    first = ask("Password: ")
    if ask("Password again: ") != first:
        return None
    return first


def _refused(message: str) -> int:
    """Say why nothing was written, and give the refusal's exit code."""
    print(f"create_admin: {message}, so nothing was written.", file=sys.stderr)
    return REFUSED


def _create(
    db: Session, email: str, name: str, another: bool, ask: Callable[[str], str]
) -> int:
    """Make the manager in this session, or refuse."""
    managers = db.scalar(
        select(func.count()).select_from(User).where(User.role == UserRole.manager)
    )
    if managers and not another:
        return _refused(
            "this database already has a manager; pass --another to add one more"
        )
    if account_by_email(db, email) is not None:
        return _refused(f"an account with the address {email} already exists")
    password = _password(ask)
    if password is None:
        return _refused("the two passwords differ")
    problem = weak_admin_password_problem(password)
    if problem is not None:
        return _refused(f"the password {problem}")
    db.add(
        User(
            email=email,
            full_name=name,
            hashed_password=hash_password(password),
            role=UserRole.manager,
        )
    )
    db.commit()
    print(f"created manager {email}")
    return OK


def main(
    argv: Sequence[str] | None = None,
    *,
    db: Session | None = None,
    ask: Callable[[str], str] = getpass.getpass,
) -> int:
    """Open the manager's account; the exit code says how it went.

    `db` is the session to work in; run as a module, the application's own
    `SessionLocal` is opened. A caller that already has a session -- the
    tests, which must never let this open the live database -- passes it.
    `ask` reads a password without echoing it.
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.create_admin",
        description="Open a manager's account in a database, with no demo data.",
    )
    parser.add_argument("--email", required=True, help="the manager's address")
    parser.add_argument(
        "--name", default="Site Administrator", help="the name the console shows"
    )
    parser.add_argument(
        "--another",
        action="store_true",
        help="add a manager to a database that already has one",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return USAGE if stop.code else OK
    try:
        email = normalize_email(args.email)
    except ValueError:
        print(f"create_admin: {args.email!r} is not an email address", file=sys.stderr)
        return USAGE

    if db is None:
        with SessionLocal() as own:
            return _create(own, email, args.name, args.another, ask)
    return _create(db, email, args.name, args.another, ask)


if __name__ == "__main__":
    sys.exit(main())
