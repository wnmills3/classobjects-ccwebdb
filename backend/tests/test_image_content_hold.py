"""Stored files are named by their content: one hash, one set of files.

So a delete must not remove files an upload of the same picture has just
come to hold, and two uploads of one picture must be one row. Both turn on
`image_store.hold_content`, held per hash until the transaction commits.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest
from app import image_store
from app.config import settings
from app.models import Image
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from tests.builders import make_jpeg
from tests.conftest import build_item


@pytest.fixture
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty media directory of the test's own, where storage writes."""
    monkeypatch.setattr(settings, "media_root", tmp_path)
    return tmp_path


def _stored(root: Path) -> set[str]:
    """Every file under the media root, as a path relative to it."""
    return {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }


def _keys(image: Image) -> set[str]:
    """The storage keys an image row names: its original and its renditions."""
    return {image.storage_key} | {d.storage_key for d in image.derivatives}


def test_a_delete_leaves_the_files_of_a_picture_uploaded_again_meanwhile(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    media_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same picture uploaded between the delete's commit and its file removal."""
    picture = make_jpeg(color=(10, 90, 160))
    first = image_store.ingest(db, picture, "first.jpg")
    db.commit()
    real = image_store.remove_unheld

    def uploaded_meanwhile(
        db: Session, keys_by_hash: Mapping[str, Sequence[str]]
    ) -> None:
        """Another upload of the same bytes commits first, then the removal runs."""
        image_store.ingest(db, picture, "again.jpg")
        db.commit()
        real(db, keys_by_hash)

    monkeypatch.setattr(image_store, "remove_unheld", uploaded_meanwhile)

    deleted = client.delete(f"/api/images/{first.id}", headers=admin_headers)

    assert deleted.status_code == 204, deleted.text
    again = db.scalars(select(Image)).one()
    assert _keys(again) <= _stored(media_root)


def test_a_delete_removes_the_files_no_row_holds(
    client: TestClient, admin_headers: dict[str, str], db: Session, media_root: Path
) -> None:
    image = image_store.ingest(db, make_jpeg(color=(200, 30, 30)), "gone.jpg")
    db.commit()
    assert _keys(image) <= _stored(media_root)

    deleted = client.delete(f"/api/images/{image.id}", headers=admin_headers)

    assert deleted.status_code == 204, deleted.text
    assert _stored(media_root) == set()


def test_an_upload_holds_its_picture_before_it_looks_for_it(
    db: Session, media_root: Path
) -> None:
    """Two uploads of one picture: the second waits, then finds the first's row."""
    sent: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_: object) -> None:
        """Keep each statement, in order."""
        sent.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", record)
    try:
        image_store.ingest(db, make_jpeg(color=(5, 120, 5)), "held.jpg")
    finally:
        event.remove(connection, "before_cursor_execute", record)

    holds = [i for i, s in enumerate(sent) if "pg_advisory_xact_lock" in s]
    looks = [i for i, s in enumerate(sent) if "FROM image" in s and "sha256" in s]
    assert holds, sent
    assert looks, sent
    assert holds[0] < looks[0]


def _statements_while(db: Session, act: Callable[[], object]) -> list[str]:
    """Every statement `act` sends, in order."""
    sent: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_: object) -> None:
        """Keep each statement, in order."""
        sent.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", record)
    try:
        act()
    finally:
        event.remove(connection, "before_cursor_execute", record)
    return sent


def _first(sent: list[str], *fragments: str) -> int:
    """The place of the first statement holding every fragment; -1 for none."""
    for place, statement in enumerate(sent):
        if all(fragment in statement for fragment in fragments):
            return place
    return -1


def test_a_removal_holds_the_picture_before_it_asks_whether_a_row_names_it(
    db: Session, media_root: Path
) -> None:
    """An upload of the same picture committing meanwhile is seen, not missed."""
    image = image_store.ingest(db, make_jpeg(color=(60, 5, 60)), "kept.jpg")
    db.commit()
    # Read before listening: after the commit, reading them loads the row.
    held_keys = {image.sha256: [image.storage_key]}

    sent = _statements_while(db, lambda: image_store.remove_unheld(db, held_keys))

    held = _first(sent, "pg_advisory_xact_lock")
    looked = _first(sent, "FROM image", "sha256")
    assert held != -1, sent
    assert looked != -1, sent
    assert held < looked
    # A row still names it: nothing was removed.
    assert _keys(image) <= _stored(media_root)


def test_a_delete_holds_the_picture_before_it_deletes_the_row(
    client: TestClient, admin_headers: dict[str, str], db: Session, media_root: Path
) -> None:
    """An upload of the same picture meanwhile waits for the delete to commit.

    Unheld, it would find the row still there, file itself under it and
    answer with it -- and the delete would then remove the row and the files.
    """
    image = image_store.ingest(db, make_jpeg(color=(90, 90, 5)), "doomed.jpg")
    db.commit()

    sent = _statements_while(
        db, lambda: client.delete(f"/api/images/{image.id}", headers=admin_headers)
    )

    held = _first(sent, "pg_advisory_xact_lock")
    deleted = _first(sent, "DELETE FROM image")
    assert held != -1, sent
    assert deleted != -1, sent
    assert held < deleted


def test_an_upload_onto_an_item_holds_the_item_before_the_picture(
    client: TestClient, admin_headers: dict[str, str], db: Session, media_root: Path
) -> None:
    """One order for both locks, as adding by web address takes them.

    Taken in the other order, an upload and an address of the same picture
    onto the same item would each hold what the other waits for.
    """
    item = build_item(db)

    sent = _statements_while(
        db,
        lambda: client.post(
            "/api/images",
            files={"file": ("c.jpg", make_jpeg(color=(5, 60, 90)), "image/jpeg")},
            data={"inventory_item_id": str(item.id)},
            headers=admin_headers,
        ),
    )

    held_item = _first(sent, "FROM inventory_item", "FOR UPDATE")
    held_picture = _first(sent, "pg_advisory_xact_lock")
    assert held_item != -1, sent
    assert held_picture != -1, sent
    assert held_item < held_picture
