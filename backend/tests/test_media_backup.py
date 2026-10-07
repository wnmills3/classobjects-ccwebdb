"""Photograph bytes copied beside a database backup, and proved there.

`app.media_backup`: no database backup holds a photograph's bytes, so the
export copies them from media storage into a folder, and `check` re-reads
that folder against the rows.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app import media_backup
from app.media_backup import check_media, copy_media, prune_media
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


# --------------------------------------------------------------------------
# Pruning: files no image row names
# --------------------------------------------------------------------------


def _all_files(folder: Path) -> set[str]:
    """Every file under `folder`, by its path from there."""
    return {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}


def _backed_up(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> tuple[Path, set[str]]:
    """A complete copy of two photographs, and the keys their rows name."""
    one = _photograph(db, storage, b"first photograph")
    two = _photograph(db, storage, b"second photograph")
    folder = tmp_path / "backup"
    copy_media(db, storage, folder)
    named = {
        one.storage_key,
        one.derivatives[0].storage_key,
        two.storage_key,
        two.derivatives[0].storage_key,
    }
    return folder, named


def _leave(folder: Path, key: str, data: bytes = b"an older picture") -> None:
    """A file under `folder` that no row names, as a replaced photograph leaves."""
    target = folder / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def test_a_file_no_row_names_is_listed_and_nothing_is_deleted_unasked(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    folder, named = _backed_up(db, storage, tmp_path)
    _leave(folder, "originals/ab/abc.jpg", b"12345")
    _leave(folder, "derivatives/thumb/ab/abc.jpg", b"123")

    done = prune_media(db, folder, delete=False)

    assert done.obsolete == ["derivatives/thumb/ab/abc.jpg", "originals/ab/abc.jpg"]
    assert (done.size, done.deleted, done.refused) == (8, 0, None)
    assert _all_files(folder) == named | set(done.obsolete)


def test_asked_to_delete_it_removes_those_files_and_only_those(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    folder, named = _backed_up(db, storage, tmp_path)
    _leave(folder, "originals/ab/abc.jpg")
    _leave(folder, "derivatives/web/ab/abc.jpg")
    # Left by an interrupted copy: no row names a `.partial` either.
    _leave(folder, "originals/cd/cde.jpg.partial")

    done = prune_media(db, folder, delete=True)

    assert done.deleted == 3
    assert done.refused is None
    assert _all_files(folder) == named
    assert check_media(db, folder) == []


def test_a_complete_copy_with_nothing_extra_has_nothing_to_prune(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    folder, named = _backed_up(db, storage, tmp_path)

    done = prune_media(db, folder, delete=True)

    assert (done.obsolete, done.deleted, done.refused) == ([], 0, None)
    assert _all_files(folder) == named


def test_a_folder_missing_a_photograph_is_not_pruned(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    """Not a complete copy for this database: nothing in it is judged obsolete."""
    folder, named = _backed_up(db, storage, tmp_path)
    _leave(folder, "originals/ab/abc.jpg")
    gone = sorted(named)[0]
    (folder / gone).unlink()

    done = prune_media(db, folder, delete=True)

    assert done.refused is not None
    assert "1 photograph file(s) the rows name are not in" in done.refused
    assert done.deleted == 0
    # The stray file is still there: a refusal deletes nothing.
    assert (folder / "originals/ab/abc.jpg").is_file()


def test_another_database_s_folder_is_not_emptied(
    db: Session, storage: CountingStorage, tmp_path: Path
) -> None:
    """Pointed at a folder that holds none of these photographs, it stops."""
    _photograph(db, storage, b"first photograph")
    elsewhere = tmp_path / "another-collection"
    _leave(elsewhere, "originals/ab/abc.jpg")
    _leave(elsewhere, "originals/cd/cde.jpg")

    done = prune_media(db, elsewhere, delete=True)

    assert done.refused is not None
    assert done.deleted == 0
    assert len(_all_files(elsewhere)) == 2


def test_with_no_photographs_recorded_nothing_is_obsolete(
    db: Session, tmp_path: Path
) -> None:
    """An empty image table would make every file obsolete: that is refused."""
    folder = tmp_path / "backup"
    _leave(folder, "originals/ab/abc.jpg")

    done = prune_media(db, folder, delete=True)

    assert done.refused == "no photographs are recorded: nothing is judged obsolete"
    assert (folder / "originals/ab/abc.jpg").is_file()


def test_a_folder_that_is_not_there_is_refused(db: Session, tmp_path: Path) -> None:
    done = prune_media(db, tmp_path / "nowhere", delete=True)
    assert done.refused is not None
    assert "is not a folder" in done.refused


def test_the_command_lists_by_default_and_deletes_when_told(
    db: Session,
    storage: CountingStorage,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder, named = _backed_up(db, storage, tmp_path)
    _leave(folder, "originals/ab/abc.jpg", b"12345")

    assert media_backup.main(["prune", str(folder)], db=db) == 0
    listed = capsys.readouterr().out
    assert "1 file(s) no image row names (5 bytes)" in listed
    assert "originals/ab/abc.jpg" in listed
    assert "nothing deleted (--delete to remove them)" in listed
    assert (folder / "originals/ab/abc.jpg").is_file()

    assert media_backup.main(["prune", str(folder), "--delete"], db=db) == 0
    assert "deleted 1 file(s)" in capsys.readouterr().out
    assert _all_files(folder) == named


def test_the_command_exits_1_and_says_why_when_it_refuses(
    db: Session,
    storage: CountingStorage,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder, named = _backed_up(db, storage, tmp_path)
    (folder / sorted(named)[0]).unlink()

    assert media_backup.main(["prune", str(folder), "--delete"], db=db) == 1
    assert "refused:" in capsys.readouterr().err
