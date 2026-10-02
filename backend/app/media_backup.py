"""Photograph bytes copied beside a database backup, and proved there.

A photograph's bytes are in media storage, never in the database
(`app.storage`), so no database backup holds them: a workbook, a `pg_dump`
file or a database copy restores every image *row* and no picture. This
module copies the bytes into a folder, laid out by storage key exactly as
media storage is, so restoring is copying the folder back to `MEDIA_ROOT`.

    python -m app.media_backup copy [FOLDER]     media storage -> FOLDER
    python -m app.media_backup check [FOLDER]    FOLDER against the image rows

`FOLDER` defaults to `ccwebdb-backups/media`, beside the workbooks.
`python -m app.workbook_backup export` runs the copy itself.

**What is copied** is what the rows name: every `image.storage_key` and every
`image_derivative.storage_key`. A file in media storage that no row names is
not a photograph of anything and is left behind.

**One folder serves every backup.** A key is the photograph's content hash,
so a file never changes once written: a copy already in the folder is kept,
and a run copies only what is new. An original already there is trusted when
its size is the row's `byte_size`; a shorter one, left by an interrupted run,
is replaced.

**Each original is hashed as it is copied** and refused when it is not the
bytes its row describes, so damage in media storage is reported rather than
carried into the backup. `check` re-reads the folder itself: every original
hashed against its row, every rendition present. Either command exits 1 and
names each file when anything is missing, damaged or refused.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import REPO_ROOT
from .database import SessionLocal
from .models import Image, ImageDerivative
from .storage import StorageBackend, get_storage

__all__ = ["MediaCopy", "check_media", "copy_media", "default_folder"]


@dataclass
class MediaCopy:
    """What one run did: files copied, files already there, and what went wrong."""

    copied: int = 0
    present: int = 0
    problems: list[str] = field(default_factory=list)


def default_folder() -> Path:
    """Beside the database backups, outside the repository."""
    return REPO_ROOT.parent / "ccwebdb-backups" / "media"


def _files(db: Session) -> list[tuple[str, str | None, int | None]]:
    """Every stored file the rows name: key, and for an original its hash and size.

    Originals first, then renditions, each in id order, so a report reads the
    same from run to run.
    """
    originals = db.execute(
        select(Image.storage_key, Image.sha256, Image.byte_size).order_by(Image.id)
    ).all()
    renditions = db.scalars(
        select(ImageDerivative.storage_key).order_by(ImageDerivative.id)
    ).all()
    return [(key, sha, size) for key, sha, size in originals] + [
        (key, None, None) for key in renditions
    ]


def _target(folder: Path, key: str) -> Path | None:
    """Where `key` lands under `folder`, or None for a key that would leave it.

    A key comes from a database row; resolving and checking containment is
    the same guard `storage.LocalStorage` applies to media storage itself.
    """
    root = folder.resolve()
    candidate = (root / key).resolve()
    return candidate if candidate.is_relative_to(root) and candidate != root else None


def _already_there(target: Path, size: int | None) -> bool:
    """Whether a finished copy is at `target`: the row's size, or any bytes at all."""
    if not target.is_file():
        return False
    held = target.stat().st_size
    return held == size if size is not None else held > 0


def copy_media(db: Session, storage: StorageBackend, folder: Path) -> MediaCopy:
    """Copy every file the image rows name from `storage` into `folder`.

    A file already copied is left alone and not read again. Nothing is
    removed from `folder`, and media storage is only read.
    """
    done = MediaCopy()
    for key, sha, size in _files(db):
        target = _target(folder, key)
        if target is None:
            done.problems.append(f"refused, not a storage key: {key}")
            continue
        if _already_there(target, size):
            done.present += 1
            continue
        try:
            data = storage.get(key)
        except FileNotFoundError:
            done.problems.append(f"missing from media storage: {key}")
            continue
        if sha is not None and hashlib.sha256(data).hexdigest() != sha:
            done.problems.append(f"damaged in media storage: {key}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # A temporary name and a rename, so an interrupted run leaves no file
        # that looks finished.
        temporary = target.with_suffix(target.suffix + ".partial")
        temporary.write_bytes(data)
        temporary.replace(target)
        done.copied += 1
    return done


def check_media(db: Session, folder: Path) -> list[str]:
    """What is wrong with the copy in `folder`; empty when every file is right.

    Reads the folder, not media storage: each original is hashed against its
    row, and each rendition must be there with something in it.
    """
    problems: list[str] = []
    for key, sha, _size in _files(db):
        target = _target(folder, key)
        if target is None:
            problems.append(f"refused, not a storage key: {key}")
        elif not target.is_file() or target.stat().st_size == 0:
            problems.append(f"missing from the copy: {key}")
        elif sha is not None and _sha256(target) != sha:
            problems.append(f"damaged in the copy: {key}")
    return problems


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def report(done: MediaCopy, folder: Path) -> None:
    """Print what a copy did, and each problem on a line of its own."""
    print(
        f"photographs: {done.copied:,} file(s) copied, {done.present:,} already "
        f"there, in {folder}"
    )
    for problem in done.problems:
        print(f"  {problem}", file=sys.stderr)
    if done.problems:
        print(f"  {len(done.problems)} file(s) NOT backed up", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """Copy the photographs to a folder, or check a folder against the rows."""
    parser = argparse.ArgumentParser(prog="media_backup", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("copy", "copy the photographs from media storage"),
        ("check", "re-read a copy against the image rows"),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument(
            "folder", type=Path, nargs="?", help="default ccwebdb-backups/media"
        )
    args = parser.parse_args(argv)
    folder = args.folder or default_folder()

    with SessionLocal() as db:
        if args.command == "copy":
            done = copy_media(db, get_storage(), folder)
            report(done, folder)
            return 1 if done.problems else 0
        problems = check_media(db, folder)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            f"{len(problems)} file(s) wrong in {folder}"
            if problems
            else f"every photograph is in {folder} and matches its row"
        )
        return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
