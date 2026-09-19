"""File the safe-deposit-box photographs against the items they show.

Walks a directory, reads `<item_code>_<nn>.<ext>` out of each filename
(`app.photo_names`), and links the photograph to the item it names
(`app.image_links.attach`). Every file is stored regardless of what its name
says -- an import step must never be the reason a photograph is lost, and
`item_image.inventory_item_id` is nullable for exactly this: a stored,
browsable photograph that nobody has filed yet. Only the *link* is ever
withheld.

    python -m app.photo_import [--root PATH] [--commit]

`--root` defaults to `settings.photo_library_root`, the real library. **Never
point this at a real directory without `--commit` having been asked for on
purpose** -- the owner watches the first real run personally, and every test
in `tests/test_photo_import.py` builds its own library under `tmp_path`
rather than touching it.

Collisions are found before anything is written: every filename in the run
is parsed first, so a slot two files both claim is known before either is
processed, and the rule is that a collision links *neither* -- there is no
way to prefer one claimant over the other from the filename alone.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import image_links, image_store, photo_names, sale_state
from .config import settings
from .database import SessionLocal
from .imaging import ImageRejected
from .models import Image, InventoryItem, ItemImage

__all__ = ["ImportReport", "main", "run"]


@dataclass
class ImportReport:
    """What one run of the pass did, or would do."""

    #: Photographs filed against an item by this run.
    linked: int = 0
    #: Already filed against that item -- the idempotent case, not a problem.
    already: int = 0
    #: (filename, reason) for a file that was stored but not filed.
    unmatched: list[tuple[str, str]] = field(default_factory=list)
    #: Filenames that parsed to a slot another file in this run also claimed.
    collisions: list[str] = field(default_factory=list)
    #: (filename, what already holds the slot).
    occupied: list[tuple[str, str]] = field(default_factory=list)
    #: (filename, why) for bytes `app.imaging` refused.
    rejected: list[tuple[str, str]] = field(default_factory=list)
    #: Item codes among those filed that are for sale. Reported, not refused.
    for_sale: list[str] = field(default_factory=list)


def _walk(root: Path) -> list[Path]:
    """Every file under `root`, sorted by its path relative to `root`.

    Sorted so a report is reproducible run to run, and so the two files in a
    collision test are always visited in the same order.
    """
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _collisions(
    files: list[Path],
) -> tuple[dict[Path, photo_names.ParsedName | None], set[Path]]:
    """Parse every filename first, and the slots two files both claim.

    Must run before anything is ingested: a collision is only knowable once
    every filename in the run has been read, and processing the first
    claimant before seeing the second would link it by accident.
    """
    parsed: dict[Path, photo_names.ParsedName | None] = {}
    claims: dict[tuple[str, int], list[Path]] = {}
    for path in files:
        name = photo_names.parse(path.name)
        parsed[path] = name
        if name is not None:
            claims.setdefault((name.item_code, name.sequence), []).append(path)
    collided = {path for group in claims.values() if len(group) > 1 for path in group}
    return parsed, collided


def run(db: Session, root: Path, *, commit: bool) -> ImportReport:
    """Import every photograph under `root`. Rolls back unless `commit`."""
    report = ImportReport()
    files = _walk(root)
    parsed_by_path, collided = _collisions(files)
    linked_items: dict[int, str] = {}

    try:
        for path in files:
            name = path.relative_to(root).as_posix()
            try:
                image = image_store.ingest(db, path.read_bytes(), name)
            except ImageRejected as exc:
                report.rejected.append((name, str(exc)))
                continue

            parsed = parsed_by_path[path]
            if parsed is None:
                report.unmatched.append(
                    (name, "does not match the CC-NNNNNN_NN naming convention")
                )
                continue
            if path in collided:
                report.collisions.append(name)
                continue

            item = db.scalar(
                select(InventoryItem).where(InventoryItem.item_code == parsed.item_code)
            )
            if item is None:
                report.unmatched.append((name, "no item has this code"))
                continue
            if item.deleted_at is not None:
                report.unmatched.append((name, "item was deleted"))
                continue
            if item.split_at is not None:
                report.unmatched.append((name, "item was split"))
                continue

            already = db.scalar(
                select(ItemImage).where(
                    ItemImage.inventory_item_id == item.id,
                    ItemImage.image_id == image.id,
                )
            )
            if already is not None:
                report.already += 1
                continue

            occupant = db.scalar(
                select(ItemImage).where(
                    ItemImage.inventory_item_id == item.id,
                    ItemImage.sort_order == parsed.sequence,
                )
            )
            if occupant is not None:
                holder = db.get(Image, occupant.image_id)
                holder_name = holder.source_ref if holder and holder.source_ref else "?"
                report.occupied.append((name, holder_name))
                continue

            image_links.attach(
                db,
                image=image,
                item=item,
                role=parsed.role,
                is_primary=parsed.is_primary,
                sort_order=parsed.sequence,
            )
            report.linked += 1
            linked_items[item.id] = item.item_code

        if linked_items:
            uses = sale_state.for_sale(db, linked_items.keys())
            report.for_sale = sorted(linked_items[item_id] for item_id in uses)
    except Exception:
        # Any surprise here -- not just the exceptions this pass already
        # knows to catch -- must not leave a half-applied run sitting in the
        # session, whether or not the caller was going to commit.
        db.rollback()
        raise

    if commit:
        db.commit()
    else:
        db.rollback()
    return report


def main(argv: list[str] | None = None) -> int:
    """Report, or apply, an import of the photograph library."""
    parser = argparse.ArgumentParser(prog="photo_import", description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=settings.photo_library_root,
        help="directory to import (defaults to the configured photo library)",
    )
    parser.add_argument("--commit", action="store_true", help="write the changes")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        report = run(db, args.root, commit=args.commit)

    for name, reason in report.rejected:
        print(f"REJECTED   {name}: {reason}")
    for name, reason in report.unmatched:
        print(f"unmatched  {name}: {reason}")
    for name in report.collisions:
        print(f"collision  {name}")
    for name, holder in report.occupied:
        print(f"occupied   {name}: slot already holds {holder}")

    print()
    print(f"linked     {report.linked}")
    print(f"already    {report.already}")
    print(f"unmatched  {len(report.unmatched)}")
    print(f"collisions {len(report.collisions)}")
    print(f"occupied   {len(report.occupied)}")
    print(f"rejected   {len(report.rejected)}")
    if report.for_sale:
        print(f"for sale   {len(report.for_sale)}: {', '.join(report.for_sale)}")

    if not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
