"""Importing a directory of photographs.

Every test builds its own library under `tmp_path`. The real library is never
touched by the suite.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from app import image_links, photo_import
from app.config import settings
from app.models import Image, InventoryItem, ItemImage, Listing
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item


def _jpeg(color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", (8, 8), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _stored_image(db: Session, sha: str) -> Image:
    """A photograph already in the database, as a console upload would leave it.

    Rows only -- no bytes are put in storage, because nothing here reads them
    back. What matters is that the link exists for the pass to find.
    """
    image = Image(
        sha256=sha,
        storage_key=f"originals/{sha[:2]}/{sha}.jpg",
        media_type="image/jpeg",
        byte_size=10,
        source_ref="hand-attached.jpg",
    )
    db.add(image)
    db.flush()
    return image


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


def test_a_dry_run_leaves_media_storage_untouched(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The database assertion above cannot see this, and that was the bug.

    `image_store.ingest` writes the original and both derivatives through
    `storage.put` *before* any rollback, so a dry run that ingested left three
    files per photograph in media storage with no rows pointing at them --
    while printing "nothing written". The media root is redirected at a
    temporary directory so that what the run does to storage is observable at
    all, and so that the suite cannot write into the repository's own `media/`.
    """
    media = tmp_path / "media"
    monkeypatch.setattr(settings, "media_root", media)
    item = build_item(db)
    db.commit()
    root = _library(tmp_path / "library", {f"{item.item_code}_01.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=False)

    assert report.linked == 1
    assert db.scalar(select(Image)) is None
    written = (
        sorted(p for p in media.rglob("*") if p.is_file()) if media.exists() else []
    )
    assert written == [], f"a dry run wrote {len(written)} file(s) into media storage"


def test_a_dry_run_still_reports_bytes_the_imaging_layer_would_refuse(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping the write must not skip the validation.

    `imaging.cleanse` is what refuses a file; `storage.put` is what stores it.
    A dry run that stopped calling `cleanse` too would report a clean run over
    a library full of files a committing run then chokes on.
    """
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    root = _library(tmp_path / "library", {"CC-000001_01.jpg": b"not an image"})

    report = photo_import.run(db, root, commit=False)

    assert [name for name, _ in report.rejected] == ["CC-000001_01.jpg"]
    assert report.linked == 0


def test_a_dry_run_reports_what_a_committing_run_does(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The counts are the point of a dry run, so they must match the real one.

    The same library is reported and then applied: every bucket has to agree,
    or the report the owner watches is not the run they then authorize.
    """
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    item = build_item(db)
    other = build_item(db)
    image_links.attach(
        db, image=_stored_image(db, "e" * 64), item=other, role=None, is_primary=True
    )
    db.commit()
    root = _library(
        tmp_path / "library",
        {
            f"{item.item_code}_01.jpg": _jpeg((10, 20, 30)),
            f"{item.item_code}_02.jpg": _jpeg((40, 50, 60)),
            f"{other.item_code}_01.jpg": _jpeg((70, 80, 90)),
            "IMG_0001.jpg": _jpeg((11, 12, 13)),
        },
    )

    dry = photo_import.run(db, root, commit=False)
    wet = photo_import.run(db, root, commit=True)

    assert (dry.linked, dry.already) == (wet.linked, wet.already)
    assert dry.unmatched == wet.unmatched
    assert dry.collisions == wet.collisions
    assert dry.occupied == wet.occupied
    assert dry.primary_kept == wet.primary_kept
    assert dry.rejected == wet.rejected


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


def test_an_existing_primary_is_never_demoted_silently(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-attached photograph keeps the primary, and the report says so.

    Every console upload files at `sort_order` 0, so a hand-attached
    photograph is invisible to the occupied check -- the import files its
    `_01` at sequence 1 and, without this rule, `attach(is_primary=True)`
    would take the primary away from it with no line in any bucket.
    """
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    item = build_item(db)
    incumbent = image_links.attach(
        db,
        image=_stored_image(db, "1a" * 32),
        item=item,
        role="obverse",
        is_primary=True,
        sort_order=0,
    )
    db.commit()
    root = _library(tmp_path / "library", {f"{item.item_code}_01.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=True)

    assert report.linked == 1
    assert report.primary_kept == [(f"{item.item_code}_01.jpg", "hand-attached.jpg")]

    db.expire_all()
    assert db.get_one(ItemImage, incumbent.id).is_primary is True
    imported = db.scalar(
        select(ItemImage).where(
            ItemImage.inventory_item_id == item.id, ItemImage.sort_order == 1
        )
    )
    assert imported is not None
    assert imported.is_primary is False


def test_an_item_with_no_primary_still_gets_one_from_the_import(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The decline is only for an item that already has a primary."""
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    item = build_item(db)
    db.commit()
    root = _library(tmp_path / "library", {f"{item.item_code}_01.jpg": _jpeg()})

    report = photo_import.run(db, root, commit=True)

    assert report.primary_kept == []
    link = db.scalar(select(ItemImage).where(ItemImage.inventory_item_id == item.id))
    assert link is not None
    assert link.is_primary is True


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
