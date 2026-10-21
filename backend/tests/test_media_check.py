"""The start-up check that `MEDIA_ROOT` holds the photographs (`app.media_check`)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from app import media_check
from app.config import settings
from app.database import get_db
from app.main import app
from app.models import Image
from app.storage import LocalStorage
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


def _image(db: Session, n: int) -> Image:
    """A photograph's row, flushed, keyed `originals/<n>.jpg`; no bytes behind it."""
    image = Image(
        sha256=f"{n:064x}",
        storage_key=f"originals/{n}.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(image)
    db.flush()
    return image


def _store(root: Path, image: Image) -> None:
    """Put bytes at the image's key under `root`."""
    LocalStorage(root).put(image.storage_key, b"jpeg")


def test_no_photographs_is_nothing_to_check(db: Session, tmp_path: Path) -> None:
    media_check.check_media_root(db, LocalStorage(tmp_path), tmp_path)


def test_a_folder_holding_the_photographs_passes(db: Session, tmp_path: Path) -> None:
    for n in range(1, 4):
        _store(tmp_path, _image(db, n))
    media_check.check_media_root(db, LocalStorage(tmp_path), tmp_path)


def test_a_folder_holding_none_of_them_is_refused_by_name(
    db: Session, tmp_path: Path
) -> None:
    for n in range(1, 4):
        _store(tmp_path / "media", _image(db, n))
    wrong = tmp_path / "backend"
    wrong.mkdir()
    with pytest.raises(media_check.MediaRootMismatch) as refused:
        media_check.check_media_root(db, LocalStorage(wrong), wrong)
    message = str(refused.value)
    assert str(wrong) in message
    assert "originals/3.jpg" in message  # the newest, by id


def test_one_missing_file_beside_the_rest_is_not_a_wrong_folder(
    db: Session, tmp_path: Path
) -> None:
    images = [_image(db, n) for n in range(1, 4)]
    for image in images[:-1]:
        _store(tmp_path, image)
    media_check.check_media_root(db, LocalStorage(tmp_path), tmp_path)


def test_an_old_copy_without_the_newest_photographs_is_refused(
    db: Session, tmp_path: Path
) -> None:
    """Only the newest are sampled: a copy holding just older ones is wrong too."""
    old = _image(db, 1)
    _store(tmp_path, old)
    for n in range(2, 2 + media_check.SAMPLE_SIZE):
        _image(db, n)
    with pytest.raises(media_check.MediaRootMismatch):
        media_check.check_media_root(db, LocalStorage(tmp_path), tmp_path)


def test_a_key_escaping_the_folder_counts_as_missing(
    db: Session, tmp_path: Path
) -> None:
    image = _image(db, 1)
    image.storage_key = "../outside.jpg"
    db.flush()
    with pytest.raises(media_check.MediaRootMismatch):
        media_check.check_media_root(db, LocalStorage(tmp_path), tmp_path)


def test_an_unreachable_database_logs_and_lets_the_api_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def unreachable() -> Iterator[Session]:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        yield  # pragma: no cover -- makes this a generator, as `get_db` is

    media_check.check_at_startup(unreachable)
    assert "photograph folder not checked" in caplog.text
    assert "connection refused" in caplog.text


def test_the_generator_dependency_is_closed_after_the_check(db: Session) -> None:
    closed: list[bool] = []

    def provide() -> Iterator[Session]:
        try:
            yield db
        finally:
            closed.append(True)

    media_check.check_at_startup(provide)
    assert closed == [True]


def test_the_api_refuses_to_start_against_the_wrong_folder(db: Session) -> None:
    """Wired into the app's start-up, through `get_db` as requests are."""
    _image(db, 1)  # no bytes in the test's media folder
    app.dependency_overrides[get_db] = lambda: db
    try:
        with (
            pytest.raises(media_check.MediaRootMismatch, match="MEDIA_ROOT is"),
            TestClient(app),
        ):
            pass  # pragma: no cover -- start-up refuses first
    finally:
        app.dependency_overrides.clear()


def test_the_api_starts_when_the_folder_holds_them(db: Session) -> None:
    _store(settings.media_root, _image(db, 1))
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            assert client.get("/health").json() == {"status": "ok"}
    finally:
        app.dependency_overrides.clear()
