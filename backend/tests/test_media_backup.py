"""Photograph bytes copied beside a database backup, and proved there.

`app.media_backup`: no database backup holds a photograph's bytes, so the
export copies them from media storage into a folder, and `check` re-reads
that folder against the rows.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app.media_backup import check_media, copy_media
from app.models import DerivativeKind, Image, ImageDerivative
from app.storage import LocalStorage
from sqlalchemy.orm import Session


class CountingStorage(LocalStorage):
    """Media storage that remembers which keys were read."""

    def __init__(self, root: Path) -> None:
        """Root the storage at `root`, with nothing read yet."""
        super().__init__(root)
        self.read: list[str] = []

    def get(self, key: str) -> bytes:
        """Read `key`, noting that it was read."""
        self.read.append(key)
        return super().get(key)


@pytest.fixture
def storage(tmp_path: Path) -> CountingStorage:
    """Media storage in a temporary folder that counts how it is used."""
    return CountingStorage(tmp_path / "media")


def _photograph(
    db: Session, storage: LocalStorage, data: bytes, *, stored: bool = True
) -> Image:
    """An image row with a thumbnail, and their bytes in storage unless told not."""
    sha = hashlib.sha256(data).hexdigest()
    image = Image(
        sha256=sha,
        storage_key=f"originals/{sha[:2]}/{sha}.jpg",
        media_type="image/jpeg",
        byte_size=len(data),
    )
    db.add(image)
    db.flush()
    thumb = ImageDerivative(
        image_id=image.id,
        kind=DerivativeKind.thumb,
        storage_key=f"derivatives/thumb/{sha[:2]}/{sha}.jpg",
    )
    db.add(thumb)
    db.flush()
    if stored:
        storage.put(image.storage_key, data)
        storage.put(thumb.storage_key, b"small " + data)
    return image


def test_every_original_and_rendition_is_copied_under_its_key(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    one = _photograph(db, storage, b"first photograph")
    two = _photograph(db, storage, b"second photograph")
    folder = tmp_path / "backup" / "media"

    done = copy_media(db, storage, folder)

    assert (done.copied, done.present, done.problems) == (4, 0, [])
    assert (folder / one.storage_key).read_bytes() == b"first photograph"
    assert (folder / two.storage_key).read_bytes() == b"second photograph"
    thumb = one.derivatives[0].storage_key
    assert (folder / thumb).read_bytes() == b"small first photograph"
    assert check_media(db, folder) == []


def test_a_second_copy_reads_only_what_is_new(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    _photograph(db, storage, b"first photograph")
    folder = tmp_path / "backup"
    copy_media(db, storage, folder)
    storage.read.clear()
    new = _photograph(db, storage, b"second photograph")

    done = copy_media(db, storage, folder)

    assert (done.copied, done.present) == (2, 2)
    assert storage.read == [new.storage_key, new.derivatives[0].storage_key]


def test_a_photograph_missing_from_storage_is_named_and_the_rest_copied(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    kept = _photograph(db, storage, b"first photograph")
    lost = _photograph(db, storage, b"second photograph", stored=False)
    folder = tmp_path / "backup"

    done = copy_media(db, storage, folder)

    assert done.copied == 2
    assert done.problems == [
        f"missing from media storage: {lost.storage_key}",
        f"missing from media storage: {lost.derivatives[0].storage_key}",
    ]
    assert (folder / kept.storage_key).exists()
    assert not (folder / lost.storage_key).exists()


def test_an_original_that_no_longer_matches_its_hash_is_not_copied(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    image = _photograph(db, storage, b"first photograph")
    storage.put(image.storage_key, b"first photograpH")  # same length, other bytes
    folder = tmp_path / "backup"

    done = copy_media(db, storage, folder)

    assert done.problems == [f"damaged in media storage: {image.storage_key}"]
    assert not (folder / image.storage_key).exists()


def test_a_short_copy_left_by_an_interrupted_run_is_replaced(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    image = _photograph(db, storage, b"first photograph")
    folder = tmp_path / "backup"
    target = folder / image.storage_key
    target.parent.mkdir(parents=True)
    target.write_bytes(b"first ph")

    done = copy_media(db, storage, folder)

    assert done.problems == []
    assert target.read_bytes() == b"first photograph"


def test_a_key_that_leaves_the_folder_is_refused(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    image = _photograph(db, storage, b"first photograph")
    image.storage_key = "../outside.jpg"
    db.flush()
    (tmp_path / "outside.jpg").write_bytes(b"first photograph")
    folder = tmp_path / "backup"

    done = copy_media(db, storage, folder)

    assert done.problems == ["refused, not a storage key: ../outside.jpg"]
    assert not (tmp_path / "backup" / ".." / "outside.jpg.partial").exists()
    assert (tmp_path / "outside.jpg").read_bytes() == b"first photograph"


def test_the_check_reads_the_copy_and_names_what_is_wrong(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    good = _photograph(db, storage, b"first photograph")
    altered = _photograph(db, storage, b"second photograph")
    gone = _photograph(db, storage, b"third photograph")
    folder = tmp_path / "backup"
    copy_media(db, storage, folder)
    (folder / altered.storage_key).write_bytes(b"second photograpH")
    (folder / gone.storage_key).unlink()
    (folder / gone.derivatives[0].storage_key).unlink()

    assert check_media(db, folder) == [
        f"damaged in the copy: {altered.storage_key}",
        f"missing from the copy: {gone.storage_key}",
        f"missing from the copy: {gone.derivatives[0].storage_key}",
    ]
    assert (folder / good.storage_key).exists()
