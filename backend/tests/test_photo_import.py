"""Importing a directory of photographs.

Every test builds its own library under `tmp_path`. The real library is never
touched by the suite.
"""

from __future__ import annotations

import io
from pathlib import Path

from app import photo_import
from app.models import Image, InventoryItem, ItemImage, Listing
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item


def _jpeg(colour: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (8, 8), colour).save(buffer, format="JPEG")
    return buffer.getvalue()


def _library(root: Path, names: dict[str, bytes]) -> Path:
    for name, data in names.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def test_a_dry_run_writes_nothing(db: Session, tmp_path: Path) -> None:
    item = build_item(db)
    db.commit()
    root = _library(tmp_path, {f"{item.item_code}_01.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=False)

    assert report.linked == 1
    assert db.scalar(select(Image)) is None


def test_commit_files_the_photograph_with_its_role(db: Session, tmp_path: Path) -> None:
    item = build_item(db)
    db.commit()
    root = _library(
        tmp_path,
        {
            f"{item.item_code}_01.jpg": _jpeg((10, 20, 30)),
            f"{item.item_code}_02.jpg": _jpeg((40, 50, 60)),
        },
    )

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 2
    links = db.scalars(
        select(ItemImage)
        .where(ItemImage.inventory_item_id == item.id)
        .order_by(ItemImage.sort_order)
    ).all()
    assert [link.sort_order for link in links] == [1, 2]
    assert links[0].is_primary is True
    assert links[1].is_primary is False


def test_running_twice_changes_nothing(db: Session, tmp_path: Path) -> None:
    item = build_item(db)
    db.commit()
    root = _library(tmp_path, {f"{item.item_code}_01.jpg": _jpeg()})

    photo_import.run(db, root, commit=True)
    second = photo_import.run(db, root, commit=True)

    assert second.linked == 0
    assert second.already == 1
    assert len(db.scalars(select(ItemImage)).all()) == 1


def test_a_name_that_does_not_follow_the_convention_is_kept_unattached(
    db: Session, tmp_path: Path
) -> None:
    root = _library(tmp_path, {"IMG_4021.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 0
    assert [name for name, _ in report.unmatched] == ["IMG_4021.jpg"]
    image = db.scalar(select(Image))
    assert image is not None
    assert db.scalar(select(ItemImage)) is None


def test_an_unknown_item_code_is_kept_unattached(db: Session, tmp_path: Path) -> None:
    root = _library(tmp_path, {"CC-999999_01.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=True)

    assert [name for name, _ in report.unmatched] == ["CC-999999_01.jpg"]
    assert db.scalar(select(Image)) is not None
    assert db.scalar(select(ItemImage)) is None


def test_a_deleted_or_split_item_is_not_linked_to(db: Session, tmp_path: Path) -> None:
    from datetime import UTC, datetime

    deleted = build_item(db)
    deleted.deleted_at = datetime.now(UTC)
    split = build_item(db)
    split.split_at = datetime.now(UTC)
    db.commit()
    root = _library(
        tmp_path,
        {
            f"{deleted.item_code}_01.jpg": _jpeg((7, 7, 7)),
            f"{split.item_code}_01.jpg": _jpeg((8, 8, 8)),
        },
    )

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 0
    reasons = dict(report.unmatched)
    assert sorted(reasons) == sorted(
        [f"{deleted.item_code}_01.jpg", f"{split.item_code}_01.jpg"]
    )
    # The two reasons must not collapse into one string -- a "deleted" item
    # and a "split" item are different conditions with different fixes.
    deleted_reason = reasons[f"{deleted.item_code}_01.jpg"]
    split_reason = reasons[f"{split.item_code}_01.jpg"]
    assert deleted_reason != split_reason
    assert "delet" in deleted_reason.lower()
    assert "split" in split_reason.lower()
    # Both photographs are still stored -- nothing is ever dropped.
    assert len(db.scalars(select(Image)).all()) == 2
    assert db.scalar(select(ItemImage)) is None


def test_two_files_claiming_one_slot_link_neither(db: Session, tmp_path: Path) -> None:
    item = build_item(db)
    db.commit()
    root = _library(
        tmp_path,
        {
            f"a/{item.item_code}_01.jpg": _jpeg((1, 2, 3)),
            f"b/{item.item_code}_01.jpg": _jpeg((4, 5, 6)),
        },
    )

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 0
    assert sorted(report.collisions) == sorted(
        [f"a/{item.item_code}_01.jpg", f"b/{item.item_code}_01.jpg"]
    )
    assert db.scalar(select(ItemImage)) is None


def test_an_occupied_sequence_is_never_replaced(db: Session, tmp_path: Path) -> None:
    item = build_item(db)
    db.commit()
    first = _library(tmp_path / "one", {f"{item.item_code}_01.jpg": _jpeg((1, 1, 1))})
    photo_import.run(db, first, commit=True)

    second = _library(tmp_path / "two", {f"{item.item_code}_01.jpg": _jpeg((9, 9, 9))})
    report = photo_import.run(db, second, commit=True)

    assert report.linked == 0
    assert [name for name, _ in report.occupied] == [f"{item.item_code}_01.jpg"]
    assert len(db.scalars(select(ItemImage)).all()) == 1


def test_a_for_sale_item_is_reported_and_still_linked(
    db: Session, tmp_path: Path, listing: Listing
) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    root = _library(tmp_path, {f"{item.item_code}_01.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=True)

    # Reported, not refused -- the whole point of the design: a CLI pass has
    # nobody to acknowledge a warning, so it links and tells rather than
    # blocking. `linked == 1` is the assertion that would fail if this pass
    # ever grew a refusal for for-sale items.
    assert report.linked == 1
    assert report.for_sale == [item.item_code]


def test_an_item_not_for_sale_reports_no_for_sale_items(
    db: Session, tmp_path: Path
) -> None:
    item = build_item(db)
    db.commit()
    root = _library(tmp_path, {f"{item.item_code}_01.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 1
    assert report.for_sale == []
