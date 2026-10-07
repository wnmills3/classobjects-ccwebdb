"""Replace stored photographs with the full-size picture their address offers.

A photograph added by its address was stored at whatever size that address
named -- a 140-pixel thumbnail of a picture the marketplace also serves at
1600. Each stored photograph that may be such a copy -- its address has a
larger form (`image_urls.full_size`), and the picture held is no bigger
than the address names -- is fetched again at full size and, when what
comes back really is larger, put behind the same image in place
(`image_store.replace_content`): every item keeps the photograph where it
was, in its order and role, primary or not.

Left as they are, and counted:

- **not larger** -- the full-size address returned a picture no bigger than
  the one held;
- **already held** -- the full-size picture is one the collection already
  stores as another image, which a person merges or removes;
- **not fetched** -- the address was refused or returned something that is
  not a picture;
- **for sale** -- the photograph is on an item that is on offer or in an
  order not yet shipped: what a buyer is shown does not change under them.

Usage, from `backend/`:

    python -m app.image_enlarge                    report what would be fetched
    python -m app.image_enlarge --commit           fetch and replace
    python -m app.image_enlarge --commit --limit N the first N only

A dry run fetches nothing. A commit writes in batches, so one that is
interrupted keeps what it had done and a second run takes up the rest.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import sale_state
from .database import SessionLocal
from .image_fetch import ImageFetchRefused, fetch_image
from .image_store import is_web_address, replace_content
from .image_urls import full_size, named_edge
from .imaging import ImageRejected, cleanse
from .models import Image, InventoryItem, ItemImage
from .storage import get_storage

__all__ = ["Report", "candidates", "main", "run"]

#: Photographs replaced between commits.
BATCH = 25

REPLACED = "replaced"
NOT_LARGER = "not larger"
ALREADY_HELD = "already held"
NOT_FETCHED = "not fetched"
FOR_SALE = "for sale"


@dataclass
class Report:
    """What a run found and did."""

    #: Photographs that may be a scaled copy of a larger picture.
    candidates: int = 0
    #: How many candidates each host accounts for.
    hosts: Counter[str] = field(default_factory=Counter)
    #: How each photograph looked at came out.
    outcomes: Counter[str] = field(default_factory=Counter)
    #: Each photograph left as it was: its id, why, and the detail.
    left: list[tuple[int, str, str]] = field(default_factory=list)
    #: Bytes held before and after, over the photographs replaced.
    bytes_before: int = 0
    bytes_after: int = 0


def _may_be_scaled(image: Image) -> bool:
    """Whether the picture held could be the scaled one its address names.

    An address with a larger form is not enough. Most of the collection's
    eBay photographs are held at full size under an address that names 500
    pixels: they were fetched from the larger form of it. A picture bigger
    than its address asks for was never that scaled copy, and is not
    fetched again. Where the address names no size, or the picture's own
    size is not known, it may be.
    """
    address = image.source_url or ""
    if not is_web_address(address) or full_size(address) == address:
        return False
    named = named_edge(address)
    if named is None or image.width is None or image.height is None:
        return True
    return max(image.width, image.height) <= named


def candidates(db: Session) -> list[Image]:
    """Stored photographs that may be a scaled copy of a larger one, oldest first."""
    images = db.scalars(
        select(Image).where(Image.source_url.is_not(None)).order_by(Image.id)
    ).all()
    return [image for image in images if _may_be_scaled(image)]


def _for_sale(db: Session, images: Sequence[Image]) -> dict[int, str]:
    """Each photograph on an item that is for sale, with that item's code."""
    rows = db.execute(
        select(ItemImage.image_id, InventoryItem.id, InventoryItem.item_code)
        .join(InventoryItem, InventoryItem.id == ItemImage.inventory_item_id)
        .where(ItemImage.image_id.in_([image.id for image in images]))
    ).all()
    selling = sale_state.for_sale(db, {item_id for _image, item_id, _code in rows})
    return {
        image_id: item_code
        for image_id, item_id, item_code in rows
        if item_id in selling
    }


def _area(width: int | None, height: int | None) -> int:
    """A picture's size in pixels; nothing known of it counts as none."""
    return (width or 0) * (height or 0)


def _enlarge(
    db: Session, image: Image, fetch: Callable[[str], bytes], report: Report
) -> list[str]:
    """Fetch one photograph at full size and replace it when that is larger.

    Returns the storage keys the replacement left unused, for deleting once
    the change is committed; none when the photograph was left as it was.
    """
    address = full_size(image.source_url or "")
    try:
        cleansed = cleanse(fetch(address))
    except (ImageFetchRefused, ImageRejected) as exc:
        report.outcomes[NOT_FETCHED] += 1
        report.left.append((image.id, NOT_FETCHED, f"{address}: {exc}"))
        return []
    if _area(cleansed.width, cleansed.height) <= _area(image.width, image.height):
        report.outcomes[NOT_LARGER] += 1
        report.left.append(
            (
                image.id,
                NOT_LARGER,
                f"{cleansed.width}x{cleansed.height} against "
                f"{image.width}x{image.height} held",
            )
        )
        return []
    held = db.scalar(select(Image.id).where(Image.sha256 == cleansed.sha256))
    if held is not None:
        report.outcomes[ALREADY_HELD] += 1
        report.left.append((image.id, ALREADY_HELD, f"as image {held}"))
        return []
    report.bytes_before += image.byte_size
    unused = replace_content(db, image, cleansed, address)
    report.bytes_after += image.byte_size
    report.outcomes[REPLACED] += 1
    return unused


def run(
    db: Session,
    *,
    commit: bool,
    fetch: Callable[[str], bytes] = fetch_image,
    limit: int | None = None,
    pause: float = 0.0,
) -> Report:
    """Report the photographs that may be enlarged; with `commit`, do it.

    Without `commit` nothing is fetched and nothing written. With it, each
    photograph is fetched and replaced in turn, committed every `BATCH`, and
    the files a batch left unused are deleted only once it is committed.
    `limit` stops after that many photographs; `pause` waits that many
    seconds between fetches.
    """
    report = Report()
    found = candidates(db)
    report.candidates = len(found)
    for image in found:
        report.hosts[_host(image.source_url or "")] += 1
    if not commit:
        return report

    work = found if limit is None else found[:limit]
    selling = _for_sale(db, work)
    storage = get_storage()
    unused: list[str] = []
    pending = 0

    def settle() -> None:
        """Commit the batch, then delete the files it left unused."""
        db.commit()
        for key in unused:
            storage.delete(key)
        unused.clear()

    for image in work:
        if image.id in selling:
            report.outcomes[FOR_SALE] += 1
            report.left.append((image.id, FOR_SALE, selling[image.id]))
            continue
        unused.extend(_enlarge(db, image, fetch, report))
        pending += 1
        if pending >= BATCH:
            settle()
            pending = 0
        if pause:
            time.sleep(pause)
    settle()
    return report


def _host(url: str) -> str:
    """The host an address names, for counting candidates by where they are."""
    return url.split("//", 1)[-1].split("/", 1)[0].lower()


def _print(report: Report, *, commit: bool, listed: bool) -> None:
    """Print the counts, and with `listed` each photograph left as it was."""
    print(f"photographs that may have a larger picture: {report.candidates}")
    for host, count in report.hosts.most_common():
        print(f"  {count:>6}  {host}")
    if not commit:
        print("dry run: nothing fetched or written (--commit to apply)")
        return
    for outcome, count in report.outcomes.most_common():
        print(f"{outcome}: {count}")
    if report.outcomes[REPLACED]:
        print(
            f"stored bytes of those replaced: {report.bytes_before:,} -> "
            f"{report.bytes_after:,}"
        )
    if listed:
        for image_id, outcome, detail in report.left:
            print(f"  image {image_id}: {outcome} -- {detail}")


def main(argv: Sequence[str] | None = None, *, db: Session | None = None) -> int:
    """Report, or with --commit fetch and replace, the photographs.

    `db` is the session to work in; run as a module, the application's own
    `SessionLocal` is opened. A caller that already has a session -- the
    tests, which must never let this open the live database -- passes it.
    """
    parser = argparse.ArgumentParser(prog="image_enlarge", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="fetch and replace")
    parser.add_argument("--limit", type=int, help="stop after this many photographs")
    parser.add_argument(
        "--pause", type=float, default=0.05, help="seconds between fetches"
    )
    parser.add_argument(
        "--list", action="store_true", help="name each photograph left as it was"
    )
    args = parser.parse_args(argv)

    def report_in(session: Session) -> int:
        """Run in `session` and print what happened."""
        report = run(session, commit=args.commit, limit=args.limit, pause=args.pause)
        _print(report, commit=args.commit, listed=args.list)
        return 0

    if db is None:
        with SessionLocal() as own:
            return report_in(own)
    return report_in(db)


if __name__ == "__main__":
    sys.exit(main())
