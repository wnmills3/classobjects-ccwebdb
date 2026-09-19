"""Linking a photograph to an item, and the one-primary rule.

`app.image_links` is the only writer of `item_image`.
"""

from __future__ import annotations

import pytest
from app import image_links
from app.models import Image, ImageRole, InventoryItem, ItemImage, Listing
from fastapi.testclient import TestClient
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
    stored = db.get(ImageRole, link.image_role_id)
    assert stored is not None
    assert stored.code == "obverse"


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
    assert link.image_role_id is None


def test_the_same_photograph_cannot_be_attached_twice(db: Session) -> None:
    item = build_item(db)
    image = _image(db, "0" * 64)
    image_links.attach(db, image=image, item=item, role=None, is_primary=False)
    db.flush()
    with pytest.raises(image_links.LinkRefused):
        image_links.attach(db, image=image, item=item, role=None, is_primary=False)


def test_attaching_through_the_api_files_the_photograph(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    item = build_item(db)
    image = _image(db, "7" * 64)
    db.commit()

    made = client.post(
        f"/api/images/{image.id}/links",
        json={
            "inventory_item_id": item.id,
            "image_role": "obverse",
            "is_primary": True,
        },
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text
    assert made.json()["is_primary"] is True
    assert made.json()["item_code"] == item.item_code


def test_attaching_to_a_listed_item_is_refused_until_acknowledged(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    image = _image(db, "8" * 64)
    db.commit()
    body = {"inventory_item_id": listing.inventory_item_id, "image_role": "obverse"}

    refused = client.post(
        f"/api/images/{image.id}/links", json=body, headers=admin_headers
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    made = client.post(
        f"/api/images/{image.id}/links",
        json={**body, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text


def test_a_photograph_can_be_re_roled_and_promoted(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    item = build_item(db)
    first = image_links.attach(
        db, image=_image(db, "9" * 64), item=item, role="obverse", is_primary=True
    )
    second = image_links.attach(
        db, image=_image(db, "a1" * 32), item=item, role=None, is_primary=False
    )
    db.commit()

    changed = client.patch(
        f"/api/image-links/{second.id}",
        json={"image_role": "reverse", "is_primary": True},
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["image_role"] == "reverse"
    assert changed.json()["is_primary"] is True

    db.expire_all()
    assert db.get(ItemImage, first.id).is_primary is False


def test_detaching_keeps_the_photograph(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    item = build_item(db)
    image = _image(db, "b1" * 32)
    link = image_links.attach(db, image=image, item=item, role=None, is_primary=False)
    db.commit()

    gone = client.delete(f"/api/image-links/{link.id}", headers=admin_headers)
    assert gone.status_code == 204, gone.text

    db.expire_all()
    assert db.get(ItemImage, link.id) is None
    assert db.get(Image, image.id) is not None


def test_detaching_from_a_listed_item_is_refused_until_acknowledged(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    link = image_links.attach(
        db, image=_image(db, "c1" * 32), item=item, role=None, is_primary=True
    )
    db.commit()

    refused = client.delete(f"/api/image-links/{link.id}", headers=admin_headers)
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    gone = client.delete(
        f"/api/image-links/{link.id}?acknowledge_for_sale=true", headers=admin_headers
    )
    assert gone.status_code == 204, gone.text


def test_re_roling_a_listed_items_photograph_is_refused_until_acknowledged(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    link = image_links.attach(
        db, image=_image(db, "d2" * 32), item=item, role=None, is_primary=False
    )
    db.commit()

    refused = client.patch(
        f"/api/image-links/{link.id}",
        json={"image_role": "reverse"},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    changed = client.patch(
        f"/api/image-links/{link.id}",
        json={"image_role": "reverse", "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["image_role"] == "reverse"
