"""Moving a filed photograph to another item, and naming it for its new place.

One step moves a photograph filed on the wrong item, and a name that says its
old place (`CC-008078_01.jpg`) is rewritten for the new one.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime

import pytest
from app import image_links, sale_state
from app.models import Image, ImageRole, InventoryItem, ItemImage, Listing
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from tests.conftest import build_item


def _image(db: Session, sha: str, source_ref: str | None = None) -> Image:
    """A stored photograph's row with this hash and file name, flushed."""
    image = Image(
        sha256=sha,
        storage_key=f"orig/{sha}.jpg",
        media_type="image/jpeg",
        byte_size=10,
        source_ref=source_ref,
    )
    db.add(image)
    db.flush()
    return image


def _filed(
    db: Session,
    item: InventoryItem,
    sha: str,
    *,
    role: str | None = "obverse",
    primary: bool = False,
    position: int = 1,
    name: str | None = None,
) -> ItemImage:
    """A new photograph filed on the item, through the writer of such links."""
    return image_links.attach(
        db,
        image=_image(db, sha, name),
        item=item,
        role=role,
        is_primary=primary,
        sort_order=position,
    )


def test_a_moved_photograph_keeps_its_role_and_goes_after_the_targets(
    db: Session,
) -> None:
    source = build_item(db)
    target = build_item(db)
    moving = _filed(
        db, source, "1" * 64, primary=True, name=f"{source.item_code}_01.jpg"
    )
    staying = _filed(db, source, "2" * 64, role="reverse", position=2)
    incumbent = _filed(db, target, "3" * 64, primary=True)

    image_links.move(db, moving, target)
    db.flush()

    assert moving.inventory_item_id == target.id
    assert moving.sort_order == 2
    assert moving.is_primary is False  # the target's own primary stays
    db.refresh(incumbent)
    assert incumbent.is_primary is True
    # The item it left still shows a buyer something.
    db.refresh(staying)
    assert staying.is_primary is True
    image = db.get(Image, moving.image_id)
    assert image is not None
    assert image.source_ref == f"{target.item_code}_02.jpg"
    role = db.get(ImageRole, moving.image_role_id)
    assert role is not None and role.code == "obverse"


def test_a_photograph_moved_to_an_item_without_one_becomes_its_primary(
    db: Session,
) -> None:
    source = build_item(db)
    target = build_item(db)
    moving = _filed(db, source, "4" * 64, primary=True)

    image_links.move(db, moving, target)
    db.flush()

    assert moving.is_primary is True
    assert moving.sort_order == 1


def test_a_name_that_is_not_an_items_place_is_left_alone(db: Session) -> None:
    source = build_item(db)
    target = build_item(db)
    camera = _filed(db, source, "5" * 64, name="DSC00417.JPG")
    lookalike = _filed(db, source, "6" * 64, position=2, name="NOPE-1_02.jpg")

    image_links.move(db, camera, target)
    image_links.move(db, lookalike, target)
    db.flush()

    assert db.get(Image, camera.image_id).source_ref == "DSC00417.JPG"  # type: ignore[union-attr]
    assert db.get(Image, lookalike.image_id).source_ref == "NOPE-1_02.jpg"  # type: ignore[union-attr]


def test_a_name_from_a_folder_of_the_library_is_named_for_its_new_place(
    db: Session,
) -> None:
    """The import keeps the folder a file was in; the name itself is a place."""
    source = build_item(db)
    target = build_item(db)
    moving = _filed(
        db, source, "c" * 64, primary=True, name=f"box3/{source.item_code}_01.jpg"
    )

    image_links.move(db, moving, target)
    db.flush()

    image = db.get(Image, moving.image_id)
    assert image is not None
    assert image.source_ref == f"{target.item_code}_01.jpg"


def test_a_photograph_shared_with_another_item_keeps_its_name(db: Session) -> None:
    """A group photograph filed on three items names none of them alone."""
    source = build_item(db)
    other = build_item(db)
    target = build_item(db)
    shared = _image(db, "7" * 64, f"{source.item_code}_01.jpg")
    moving = image_links.attach(
        db, image=shared, item=source, role=None, is_primary=True
    )
    image_links.attach(db, image=shared, item=other, role=None, is_primary=True)

    image_links.move(db, moving, target)
    db.flush()

    assert shared.source_ref == f"{source.item_code}_01.jpg"


def test_a_photograph_cannot_move_to_its_own_item_or_one_that_has_it(
    db: Session,
) -> None:
    source = build_item(db)
    target = build_item(db)
    image = _image(db, "8" * 64)
    moving = image_links.attach(
        db, image=image, item=source, role=None, is_primary=True
    )
    image_links.attach(db, image=image, item=target, role=None, is_primary=True)

    with pytest.raises(image_links.LinkRefused):
        image_links.move(db, moving, source)
    with pytest.raises(image_links.LinkRefused):
        image_links.move(db, moving, target)


def test_moving_through_the_api(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    source = build_item(db)
    target = build_item(db)
    link = _filed(db, source, "9" * 64, primary=True, name=f"{source.item_code}_01.jpg")
    db.commit()

    moved = client.post(
        f"/api/image-links/{link.id}/move",
        json={"inventory_item_id": target.id},
        headers=admin_headers,
    )

    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["item_code"] == target.item_code
    assert body["is_primary"] is True
    listed = client.get(
        f"/api/images?inventory_item_id={source.id}", headers=admin_headers
    ).json()
    assert listed == []


def test_moving_to_the_same_item_is_refused(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    source = build_item(db)
    link = _filed(db, source, "a1" * 32, primary=True)
    db.commit()

    refused = client.post(
        f"/api/image-links/{link.id}/move",
        json={"inventory_item_id": source.id},
        headers=admin_headers,
    )
    assert refused.status_code == 409


def test_an_unknown_target_is_a_422_and_an_unknown_link_a_404(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """The target is a field of the body; the link is the resource in the path."""
    source = build_item(db)
    link = _filed(db, source, "b2" * 32, primary=True)
    db.commit()

    unknown_target = client.post(
        f"/api/image-links/{link.id}/move",
        json={"inventory_item_id": 999999},
        headers=admin_headers,
    )
    assert unknown_target.status_code == 422, unknown_target.text
    assert "999999" in unknown_target.json()["detail"]

    unknown_link = client.post(
        "/api/image-links/999999/move",
        json={"inventory_item_id": source.id},
        headers=admin_headers,
    )
    assert unknown_link.status_code == 404, unknown_link.text


def test_moving_onto_an_item_for_sale_is_refused_until_acknowledged(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    """Both ends change what the shop shows; the target is checked too."""
    source = build_item(db)
    link = _filed(db, source, "a2" * 32, primary=True)
    db.commit()
    body = {"inventory_item_id": listing.inventory_item_id}

    refused = client.post(
        f"/api/image-links/{link.id}/move", json=body, headers=admin_headers
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    moved = client.post(
        f"/api/image-links/{link.id}/move",
        json={**body, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert moved.status_code == 200, moved.text


def test_moving_off_an_item_for_sale_is_refused_until_acknowledged(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    """The item it leaves is the one a buyer is looking at: checked as well.

    Only the source is for sale here, so the target's own check passes and
    the refusal can come from nowhere else.
    """
    source = db.get(InventoryItem, listing.inventory_item_id)
    assert source is not None
    target = build_item(db)
    link = _filed(db, source, "a5" * 32, primary=True)
    db.commit()
    body = {"inventory_item_id": target.id}

    refused = client.post(
        f"/api/image-links/{link.id}/move", json=body, headers=admin_headers
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]
    db.expire_all()
    assert db.get_one(ItemImage, link.id).inventory_item_id == source.id

    moved = client.post(
        f"/api/image-links/{link.id}/move",
        json={**body, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["item_code"] == target.item_code


def test_filing_a_loose_photograph_names_it_for_its_new_place(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """The Photos page's filing renames as a move does."""
    left = build_item(db)
    target = build_item(db)
    _filed(db, target, "a3" * 32, primary=True)
    loose = _image(db, "a4" * 32, f"{left.item_code}_01.jpg")
    db.commit()

    made = client.post(
        f"/api/images/{loose.id}/links",
        json={"inventory_item_id": target.id, "image_role": "reverse"},
        headers=admin_headers,
    )

    assert made.status_code == 201, made.text
    db.refresh(loose)
    assert loose.source_ref == f"{target.item_code}_02.jpg"


def test_moving_onto_a_deleted_item_is_a_422(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """A deleted item should never have existed: nothing is filed on it."""
    source = build_item(db)
    gone = build_item(db, deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
    link = _filed(db, source, "a6" * 32, primary=True)
    db.commit()

    refused = client.post(
        f"/api/image-links/{link.id}/move",
        json={"inventory_item_id": gone.id},
        headers=admin_headers,
    )

    assert refused.status_code == 422, refused.text
    assert "deleted" in refused.json()["detail"]
    db.expire_all()
    assert db.get_one(ItemImage, link.id).inventory_item_id == source.id


def test_moving_a_photograph_moved_meanwhile_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read on one item and moved to another before it is held: a conflict."""
    source = build_item(db)
    elsewhere = build_item(db)
    target = build_item(db)
    link = _filed(db, source, "a7" * 32, primary=True)
    db.commit()
    real = sale_state.guard
    landed: list[bool] = []

    def guard(
        session: Session,
        items: Collection[InventoryItem],
        *,
        acknowledged: bool,
        kinds: Collection[str] | None = None,
    ) -> None:
        """Ask the real guard, then land another request's move of the link."""
        real(session, items, acknowledged=acknowledged, kinds=kinds)
        if landed:
            return
        landed.append(True)
        session.execute(
            update(ItemImage)
            .where(ItemImage.id == link.id)
            .values(inventory_item_id=elsewhere.id, sort_order=1)
            # Another session's commit: the link loaded here does not see it.
            .execution_options(synchronize_session=False)
        )

    monkeypatch.setattr(sale_state, "guard", guard)

    refused = client.post(
        f"/api/image-links/{link.id}/move",
        json={"inventory_item_id": target.id},
        headers=admin_headers,
    )

    assert refused.status_code == 409, refused.text
    db.expire_all()
    assert db.get_one(ItemImage, link.id).inventory_item_id != target.id
