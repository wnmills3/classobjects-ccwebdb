"""A photograph filed after an item's others, and named as it is stored.

An upload files each photograph after the item's last, so a reverse lists
after its obverse; and a file converted on the way in (a WebP stored as JPEG)
is recorded under the extension it is stored with.
"""

from __future__ import annotations

import io

from app.models import Image, ItemImage
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, make_jpeg


def _webp(color: str = "navy") -> bytes:
    """A small WebP image of one colour."""
    buffer = io.BytesIO()
    PILImage.new("RGB", (64, 40), color).save(buffer, format="WEBP")
    return buffer.getvalue()


def _upload(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    data: bytes,
    media_type: str,
    **form: str,
) -> dict[str, object]:
    """Upload one file with these form fields; the stored photograph's response."""
    res = client.post(
        "/api/images",
        files={"file": (name, data, media_type)},
        data=form,
        headers=headers,
    )
    assert res.status_code == 201, res.text
    body: dict[str, object] = res.json()
    return body


def _positions(db: Session, item_id: int) -> list[int]:
    """The item's photographs' positions, in the order they were filed."""
    return list(
        db.scalars(
            select(ItemImage.sort_order)
            .where(ItemImage.inventory_item_id == item_id)
            .order_by(ItemImage.id)
        )
    )


def test_each_photograph_uploaded_goes_after_the_last(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    item = make_item()
    for n, color in enumerate(("red", "green", "blue")):
        buffer = io.BytesIO()
        PILImage.new("RGB", (40, 40), color).save(buffer, format="JPEG")
        _upload(
            client,
            admin_headers,
            f"p{n}.jpg",
            buffer.getvalue(),
            "image/jpeg",
            inventory_item_id=str(item.id),
        )

    assert _positions(db, item.id) == [1, 2, 3]


def test_an_attached_photograph_goes_after_the_last_unless_placed(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    item = make_item()
    _upload(
        client,
        admin_headers,
        "front.jpg",
        make_jpeg(),
        "image/jpeg",
        inventory_item_id=str(item.id),
    )
    loose = _upload(client, admin_headers, "back.webp", _webp(), "image/webp")
    placed = _upload(client, admin_headers, "edge.webp", _webp("olive"), "image/webp")

    after = client.post(
        f"/api/images/{loose['id']}/links",
        json={"inventory_item_id": item.id, "image_role": "reverse"},
        headers=admin_headers,
    )
    at_seven = client.post(
        f"/api/images/{placed['id']}/links",
        json={"inventory_item_id": item.id, "sort_order": 7},
        headers=admin_headers,
    )

    assert after.status_code == 201, after.text
    assert after.json()["sort_order"] == 2
    assert at_seven.json()["sort_order"] == 7


def test_a_converted_file_is_named_as_it_is_stored(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    item = make_item()
    body = _upload(
        client,
        admin_headers,
        "CC-007595_02.webp",
        _webp(),
        "image/webp",
        inventory_item_id=str(item.id),
    )

    image = db.get_one(Image, body["id"])
    assert image.media_type == "image/jpeg"
    assert image.source_ref == "CC-007595_02.jpg"


def test_a_name_without_an_extension_is_kept_as_it_is(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    body = _upload(client, admin_headers, "scan", _webp("teal"), "image/webp")
    assert db.get_one(Image, body["id"]).source_ref == "scan"
