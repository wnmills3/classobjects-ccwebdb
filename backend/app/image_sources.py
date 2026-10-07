"""The web address of photographs already stored, from the files they came from.

A photograph fetched from a web address keeps that address
(`image.source_url`, written by `image_store.ingest`). For photographs stored
before the address was kept, this pass recovers it from what a download left
behind: a manifest naming each address, and a folder holding each address's
file under the SHA-1 of the address.

    python -m app.image_sources MANIFEST.csv FOLDER              report only
    python -m app.image_sources MANIFEST.csv FOLDER --commit     write

The manifest is a CSV with a `url` column; other columns are ignored. For
each address, `FOLDER/<sha1 of the address>.<ext>` is read and cleansed
exactly as an upload is, and the stored image with that content -- the same
SHA-256 -- takes the address. **The match is by content, not by item or
file name**: a photograph moved to another item, or replaced on its item by
a different one, cannot take an address that is not its own.

Only an image with no address is written. One whose address differs is
reported and kept; one address per image is recorded, the first in the
manifest when several addresses served identical bytes.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .image_store import is_web_address
from .imaging import ImageRejected, cleanse
from .models import Image

__all__ = ["Plan", "apply", "plan", "read_manifest"]


@dataclass
class Plan:
    """What the pass found: addresses to record, and what it could not use."""

    #: Image id to the address it takes.
    to_set: dict[int, str] = field(default_factory=dict)
    addresses: int = 0
    already: int = 0
    #: Addresses whose file is not in the folder.
    no_file: list[str] = field(default_factory=list)
    #: Addresses whose file is not an image the pipeline accepts.
    rejected: list[str] = field(default_factory=list)
    #: Addresses whose content matches no stored image.
    not_stored: list[str] = field(default_factory=list)
    #: (image id, its recorded address, the manifest's) where they differ.
    differs: list[tuple[int, str, str]] = field(default_factory=list)


def read_manifest(path: Path) -> list[str]:
    """Every distinct web address in the manifest's `url` column, in file order.

    Only `http(s)` addresses: the column is a file's content, and what is
    stored is later shown as a link.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if "url" not in (reader.fieldnames or []):
            raise ValueError(f"{path} has no `url` column")
        urls = (row["url"].strip() for row in reader if row["url"])
        return list(dict.fromkeys(url for url in urls if is_web_address(url)))


def _file_for(folder: Path, url: str) -> Path | None:
    """The downloaded file for `url`: named by the address's SHA-1."""
    name = hashlib.sha1(url.encode("utf-8"), usedforsecurity=False).hexdigest()
    return next(iter(sorted(folder.glob(f"{name}.*"))), None)


def plan(db: Session, urls: Sequence[str], folder: Path) -> Plan:
    """Match each address's file to a stored image by content. Writes nothing."""
    todo = Plan(addresses=len(urls))
    stored = {
        sha: (image_id, source_url)
        for image_id, sha, source_url in db.execute(
            select(Image.id, Image.sha256, Image.source_url)
        ).tuples()
    }
    for url in urls:
        path = _file_for(folder, url)
        if path is None:
            todo.no_file.append(url)
            continue
        try:
            sha = cleanse(path.read_bytes()).sha256
        except ImageRejected:
            todo.rejected.append(url)
            continue
        if sha not in stored:
            todo.not_stored.append(url)
            continue
        image_id, recorded = stored[sha]
        if recorded is None:
            # The first address wins when two served identical bytes.
            todo.to_set.setdefault(image_id, url)
        elif recorded == url:
            todo.already += 1
        else:
            todo.differs.append((image_id, recorded, url))
    return todo


def apply(db: Session, todo: Plan) -> int:
    """Record each planned address; the caller commits. Returns how many."""
    for image_id, url in todo.to_set.items():
        image = db.get_one(Image, image_id)
        if image.source_url is None:
            image.source_url = url
    db.flush()
    return len(todo.to_set)


def _print(todo: Plan) -> None:
    """Print the plan's counts, and each image holding a different address."""
    print(f"addresses in the manifest: {todo.addresses}")
    print(f"images to take an address: {len(todo.to_set)}")
    print(f"already recorded: {todo.already}")
    print(f"no file in the folder: {len(todo.no_file)}")
    print(f"file not an image: {len(todo.rejected)}")
    print(f"content matches no stored image: {len(todo.not_stored)}")
    print(f"image holds a different address (kept): {len(todo.differs)}")
    for image_id, recorded, url in todo.differs:
        print(f"  image {image_id}: has {recorded}, manifest says {url}")


def main(argv: Sequence[str] | None = None) -> int:
    """Report, or with --commit record, the addresses."""
    parser = argparse.ArgumentParser(prog="image_sources", description=__doc__)
    parser.add_argument("manifest", type=Path, help="a CSV with a `url` column")
    parser.add_argument("folder", type=Path, help="files named by SHA-1 of address")
    parser.add_argument("--commit", action="store_true", help="write the addresses")
    args = parser.parse_args(argv)

    try:
        urls = read_manifest(args.manifest)
    except (OSError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    with SessionLocal() as db:
        todo = plan(db, urls, args.folder)
        _print(todo)
        if not args.commit:
            print("dry run: nothing written (--commit to apply)")
            return 0
        written = apply(db, todo)
        db.commit()
        print(f"written: {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
