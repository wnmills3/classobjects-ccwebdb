"""The web address of photographs already stored (`app.image_sources`).

An address is matched to a stored image by the content of the file that was
downloaded from it, never by an item or a file name.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app.config import settings
from app.image_sources import apply, plan, read_manifest
from app.image_store import ingest
from app.models import Image
from sqlalchemy.orm import Session

from tests.builders import make_jpeg

A = "https://i.ebayimg.com/images/g/aaa/s-l500.jpg"
B = "https://i.ebayimg.com/images/g/bbb/s-l500.jpg"
C = "https://i.ebayimg.com/images/g/ccc/s-l500.jpg"


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stored bytes go to a temporary folder, never the repository's media."""
    root = tmp_path / "media"
    monkeypatch.setattr(settings, "media_root", root)
    return root


def _download(folder: Path, url: str, data: bytes) -> None:
    """Leave `data` where a download of `url` leaves it: named by its SHA-1."""
    folder.mkdir(exist_ok=True)
    name = hashlib.sha1(url.encode(), usedforsecurity=False).hexdigest()
    (folder / f"{name}.jpg").write_bytes(data)


def test_the_manifest_gives_each_address_once_in_order(tmp_path: Path) -> None:
    manifest = tmp_path / "images_manifest.csv"
    manifest.write_text(
        f"item_code,url,status\nCC-000001,{B},ok\nCC-000002,{A},ok\n"
        f"CC-000003,{B},ok\nCC-000004,,missing\n",
        encoding="utf-8",
    )

    assert read_manifest(manifest) == [B, A]


def test_an_address_goes_to_the_image_holding_its_files_content(
    db: Session, tmp_path: Path
) -> None:
    red, blue = make_jpeg(color=(200, 10, 10)), make_jpeg(color=(10, 10, 200))
    # Named as an import names them: nothing in the name says which address.
    first = ingest(db, red, "CC-000002_01.jpg")
    second = ingest(db, blue, "CC-000001_01.jpg")
    db.commit()
    folder = tmp_path / "by_url"
    _download(folder, A, blue)
    _download(folder, B, red)

    todo = plan(db, [A, B], folder)

    assert todo.to_set == {second.id: A, first.id: B}
    assert apply(db, todo) == 2
    db.commit()
    db.refresh(first)
    db.refresh(second)
    assert (first.source_url, second.source_url) == (B, A)
    # Run again: both are recorded, nothing is left to write.
    again = plan(db, [A, B], folder)
    assert (again.to_set, again.already) == ({}, 2)


def test_what_cannot_be_matched_is_counted_and_left(
    db: Session, tmp_path: Path
) -> None:
    kept = ingest(db, make_jpeg(color=(1, 2, 3)), "one.jpg", source_url=C)
    db.commit()
    folder = tmp_path / "by_url"
    _download(folder, A, make_jpeg(color=(1, 2, 3)))  # stored, under another address
    _download(folder, B, make_jpeg(color=(9, 9, 9)))  # never stored
    (folder / "junk.txt").write_text("not an image", encoding="utf-8")
    _download(folder, "https://example.test/not-an-image", b"not an image")

    todo = plan(
        db,
        [A, B, "https://example.test/gone", "https://example.test/not-an-image"],
        folder,
    )

    assert todo.to_set == {}
    assert todo.differs == [(kept.id, C, A)]
    assert todo.not_stored == [B]
    assert todo.no_file == ["https://example.test/gone"]
    assert todo.rejected == ["https://example.test/not-an-image"]
    apply(db, todo)
    db.commit()
    assert db.get_one(Image, kept.id).source_url == C


def test_two_addresses_serving_one_picture_record_the_first(
    db: Session, tmp_path: Path
) -> None:
    picture = make_jpeg(color=(40, 40, 40))
    image = ingest(db, picture, "CC-000001_01.jpg")
    db.commit()
    folder = tmp_path / "by_url"
    _download(folder, A, picture)
    _download(folder, B, picture)

    assert plan(db, [A, B], folder).to_set == {image.id: A}


def test_fetching_a_stored_picture_again_learns_its_address_once(
    db: Session,
) -> None:
    picture = make_jpeg(color=(70, 70, 70))
    image = ingest(db, picture, "upload.jpg")
    assert image.source_url is None

    assert ingest(db, picture, "CC-000001_02.jpg", source_url=A).id == image.id
    assert image.source_url == A
    # Known now, so a second address for the same bytes does not replace it.
    ingest(db, picture, "CC-000001_03.jpg", source_url=B)
    assert image.source_url == A
