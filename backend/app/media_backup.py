"""Photograph bytes copied beside a database backup, and proved there.

A photograph's bytes are in media storage, never in the database
(`app.storage`), so no database backup holds them: a workbook, a `pg_dump`
file or a database copy restores every image *row* and no picture. This
module copies the bytes into a folder, laid out by storage key exactly as
media storage is, so restoring is copying the folder back to `MEDIA_ROOT`.

    python -m app.media_backup copy [FOLDER]     media storage -> FOLDER
    python -m app.media_backup check [FOLDER]    FOLDER against the image rows
    python -m app.media_backup prune [FOLDER]    files in FOLDER no row names
        [--delete]                               ... removed, not only listed

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

**A copy only grows.** A photograph replaced by a better picture, or removed,
leaves its old files in the folder: nothing names them any more and nothing
removes them. `prune` lists the files in a folder that no image row names,
and with `--delete` removes them. It judges nothing obsolete in a folder
that is not a complete copy for this database -- one missing a file the rows
name, or a database that records no photographs at all -- so pointing it at
the wrong folder, or at the wrong database, deletes nothing. It works on
`MEDIA_ROOT` itself as on a copy of it, with the server stopped: a
photograph's files are written before its rows are committed, so one being
stored while the prune runs is a file no row names yet.
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

__all__ = [
    "MediaCopy",
    "MediaPrune",
    "check_media",
    "copy_media",
    "default_folder",
    "prune_media",
]


@dataclass
class MediaCopy:
    """What one run did: files copied, files already there, and what went wrong."""

    copied: int = 0
    present: int = 0
    problems: list[str] = field(default_factory=list)


@dataclass
class MediaPrune:
    """What a prune found: the files no row names, and what became of them."""

    #: Each file no image row names, by its path under the folder, in order.
    obsolete: list[str] = field(default_factory=list)
    #: Their size together, in bytes.
    size: int = 0
    #: How many were removed: none unless deletion was asked for.
    deleted: int = 0
    #: Why nothing was judged obsolete, when the folder was not pruned.
    refused: str | None = None


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


def prune_media(db: Session, folder: Path, *, delete: bool) -> MediaPrune:
    """Find the files under `folder` that no image row names; remove them if asked.

    A file is obsolete only by comparison with what the rows name, so the
    comparison has to be sound before anything is judged. It is refused, and
    nothing listed or removed, when `folder` is not a folder, when the
    database records no photographs, or when a file the rows name is not in
    the folder: a folder that is not a complete copy for this database may
    be another collection's, and its files are not this one's to call
    obsolete. Run `copy` first to complete a copy that has fallen behind.
    """
    done = MediaPrune()
    if not folder.is_dir():
        done.refused = f"{folder} is not a folder"
        return done
    named = {key for key, _sha, _size in _files(db)}
    if not named:
        done.refused = "no photographs are recorded: nothing is judged obsolete"
        return done
    held = {
        path.relative_to(folder).as_posix(): path
        for path in folder.rglob("*")
        if path.is_file()
    }
    absent = named - set(held)
    if absent:
        done.refused = (
            f"{len(absent)} photograph file(s) the rows name are not in {folder}: "
            "it is not a complete copy for this database, so nothing in it is "
            "judged obsolete (run copy first)"
        )
        return done
    done.obsolete = sorted(set(held) - named)
    done.size = sum(held[key].stat().st_size for key in done.obsolete)
    if delete:
        for key in done.obsolete:
            held[key].unlink()
            done.deleted += 1
    return done


def _sha256(path: Path) -> str:
    """The SHA-256 of a file's bytes in hex, read a megabyte at a time."""
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


def _report_prune(done: MediaPrune, folder: Path, *, delete: bool) -> int:
    """Print what a prune found and did; the exit status, 1 for a refusal."""
    if done.refused is not None:
        print(f"refused: {done.refused}", file=sys.stderr)
        return 1
    print(
        f"{len(done.obsolete):,} file(s) no image row names ({done.size:,} bytes) "
        f"in {folder}"
    )
    for key in done.obsolete:
        print(f"  {key}")
    if delete:
        print(f"deleted {done.deleted:,} file(s)")
    elif done.obsolete:
        print("nothing deleted (--delete to remove them)")
    return 0


def main(argv: Sequence[str] | None = None, *, db: Session | None = None) -> int:
    """Copy the photographs to a folder, check a folder, or prune one.

    `db` is the session to read the image rows from; run as a module, the
    application's own `SessionLocal` is opened. A caller that already has a
    session -- the tests, which must never let this open the live database
    -- passes it.
    """
    parser = argparse.ArgumentParser(prog="media_backup", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("copy", "copy the photographs from media storage"),
        ("check", "re-read a copy against the image rows"),
        ("prune", "list the files in a folder that no image row names"),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument(
            "folder", type=Path, nargs="?", help="default ccwebdb-backups/media"
        )
        if name == "prune":
            command.add_argument(
                "--delete", action="store_true", help="remove them, not only list"
            )
    args = parser.parse_args(argv)
    folder = args.folder or default_folder()

    if db is None:
        with SessionLocal() as own:
            return _run(own, args, folder)
    return _run(db, args, folder)


def _run(db: Session, args: argparse.Namespace, folder: Path) -> int:
    """Carry out the command the arguments name, in one session."""
    if args.command == "copy":
        done = copy_media(db, get_storage(), folder)
        report(done, folder)
        return 1 if done.problems else 0
    if args.command == "prune":
        pruned = prune_media(db, folder, delete=args.delete)
        return _report_prune(pruned, folder, delete=args.delete)
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
