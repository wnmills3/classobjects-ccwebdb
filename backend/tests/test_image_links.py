"""Linking a photograph to an item, and the one-primary rule.

`app.image_links` is the only writer of `item_image`.
"""

from __future__ import annotations

import pytest
from app import image_links
from app.models import Image, ItemImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item


def _image(db: Session, sha: str) -> Image:
    image = Image(
        sha256=sha,
        storage_key=f"orig/{sha}.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(image)
    db.flush()
    return image


def test_attaching_records_the_role_and_the_order(db: Session) -> None:
    item = build_item(db)
    link = image_links.attach(
        db,
        image=_image(db, "a" * 64),
        item=item,
        role="obverse",
        is_primary=True,
        sort_order=1,
    )
    db.flush()
    assert link.inventory_item_id == item.id
    assert link.is_primary is True
    assert link.sort_order == 1


def test_a_second_primary_replaces_the_first(db: Session) -> None:
    item = build_item(db)
    first = image_links.attach(
        db, image=_image(db, "b" * 64), item=item, role="obverse", is_primary=True
    )
    second = image_links.attach(
        db, image=_image(db, "c" * 64), item=item, role="reverse", is_primary=False
    )
    db.flush()

    image_links.make_primary(db, second)
    db.flush()

    db.refresh(first)
    db.refresh(second)
    assert first.is_primary is False
    assert second.is_primary is True
    # The index allows exactly one; prove only one is set.
    primaries = db.scalars(
        select(ItemImage).where(
            ItemImage.inventory_item_id == item.id, ItemImage.is_primary
        )
    ).all()
    assert len(primaries) == 1


def test_attaching_as_primary_also_replaces(db: Session) -> None:
    item = build_item(db)
    first = image_links.attach(
        db, image=_image(db, "d" * 64), item=item, role="obverse", is_primary=True
    )
    db.flush()
    image_links.attach(
        db, image=_image(db, "e" * 64), item=item, role="detail", is_primary=True
    )
    db.flush()
    db.refresh(first)
    assert first.is_primary is False


def test_detaching_leaves_the_photograph(db: Session) -> None:
    item = build_item(db)
    image = _image(db, "f" * 64)
    link = image_links.attach(db, image=image, item=item, role=None, is_primary=False)
    db.flush()

    image_links.detach(db, link)
    db.flush()

    assert db.get(ItemImage, link.id) is None
    assert db.get(Image, image.id) is not None


def test_the_same_photograph_cannot_be_attached_twice(db: Session) -> None:
    item = build_item(db)
    image = _image(db, "0" * 64)
    image_links.attach(db, image=image, item=item, role=None, is_primary=False)
    db.flush()
    with pytest.raises(image_links.LinkRefused):
        image_links.attach(db, image=image, item=item, role=None, is_primary=False)
