"""Refuse to start against a photograph folder that holds none of the photographs.

`MEDIA_ROOT` naming the wrong folder breaks nothing at start-up: the API runs,
the shop and the console load, and every photograph answers 404 "Image data
missing" -- found only when someone looks at a picture. So the API checks at
start-up and stops, naming the folder, rather than serve a collection without
its photographs.

**A sample of the newest photographs, and only all of them missing.** One file
gone from a folder that holds the rest is a storage fault the image routes
already report, and no reason to keep the whole site down; none of the newest
few in the folder means the folder is the wrong one -- another folder, or an
old copy that predates them. No photographs at all, as on a new installation,
is nothing to check.

**The database may not be up yet.** A server can start the API before
PostgreSQL; the check then logs that it could not run and lets the API start,
as it would have without it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .config import settings
from .models import Image
from .storage import StorageBackend, get_storage

__all__ = ["SAMPLE_SIZE", "MediaRootMismatch", "check_at_startup", "check_media_root"]

_log = logging.getLogger(__name__)

#: How many of the newest photographs are looked for.
SAMPLE_SIZE = 5


class MediaRootMismatch(RuntimeError):
    """`MEDIA_ROOT` holds none of the newest photographs the database names."""


def _present(storage: StorageBackend, key: str) -> bool:
    """Whether `key` is stored; a key that escapes the root counts as absent."""
    try:
        return storage.exists(key)
    except ValueError:
        return False


def check_media_root(db: Session, storage: StorageBackend, root: Path) -> None:
    """Raise `MediaRootMismatch` when none of the newest photographs is in `root`."""
    keys = db.scalars(
        select(Image.storage_key).order_by(Image.id.desc()).limit(SAMPLE_SIZE)
    ).all()
    if not keys or any(_present(storage, key) for key in keys):
        return
    raise MediaRootMismatch(
        f"MEDIA_ROOT is {root}, which holds none of the {len(keys)} newest "
        f"photographs (the newest is {keys[0]}). Point MEDIA_ROOT in .env at "
        "the folder holding `originals` and `derivatives`, or remove the line "
        "to use the repository's `media` folder."
    )


@contextmanager
def _session(provide: Callable[[], object]) -> Iterator[Session]:
    """A session from `provide`, which returns either one or a generator of one."""
    produced = provide()
    if isinstance(produced, Session):
        yield produced
        return
    assert isinstance(produced, Generator)
    try:
        yield next(produced)
    finally:
        produced.close()


def check_at_startup(provide: Callable[[], object]) -> None:
    """Run `check_media_root` on a session from `provide`; skip if the database is down.

    `provide` is the application's session dependency -- `get_db`, or whatever
    replaces it -- so the check reads the database the requests will.
    """
    try:
        with _session(provide) as db:
            check_media_root(db, get_storage(), settings.media_root)
    except OperationalError as exc:
        _log.warning(
            "photograph folder not checked: the database is not reachable (%s)",
            exc.orig,
        )
