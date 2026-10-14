"""Linking a photograph to an item, and the one-primary rule.

`app.image_links` is the only writer of `item_image`.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime

import pytest
from app import image_links, sale_state
from app.models import Image, ImageRole, InventoryItem, ItemImage, Listing
from fastapi.testclient import TestClient
from sqlalchemy import Executable, delete, event, select, update
from sqlalchemy.orm import Session

from tests.conftest import build_item


def _image(db: Session, sha: str) -> Image:
    """A stored photograph's row with this hash, flushed; no bytes behind it."""
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


def test_the_first_photograph_becomes_primary_without_being_asked(
    db: Session,
) -> None:
    """An item with photographs gets one the shop can show.

    `routers.catalog` serves the primary link and has no fallback to "the
    first one", so a photograph filed without `is_primary` onto an item that
    has none would be a photograph no buyer ever sees. `/management/photos` -- the
    page the import's leftovers go to -- files exactly that way.
    """
    item = build_item(db)
    link = image_links.attach(
        db, image=_image(db, "e1" * 32), item=item, role=None, is_primary=False
    )
    db.flush()
    assert link.is_primary is True


def test_filling_the_vacancy_does_not_displace_an_incumbent(db: Session) -> None:
    """The promotion is for a vacancy only, never a swap nobody asked for."""
    item = build_item(db)
    first = image_links.attach(
        db, image=_image(db, "e2" * 32), item=item, role="obverse", is_primary=True
    )
    second = image_links.attach(
        db, image=_image(db, "e3" * 32), item=item, role="reverse", is_primary=False
    )
    db.flush()
    assert first.is_primary is True
    assert second.is_primary is False


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


def test_detaching_the_primary_promotes_the_next_in_display_order(
    db: Session,
) -> None:
    """An item with photographs must never be left without a primary.

    `routers.catalog` serves the primary link and has no fallback, so three
    photographs and no primary is three photographs no buyer can see.

    The three are attached in an order that is deliberately not their display
    order, and the primary is the one attached first. If the code promoted
    "the next row" or "the lowest id" instead of the display successor, this
    fixture tells the difference; a fixture that attached them in order could
    not.
    """
    item = build_item(db)
    leaving = image_links.attach(
        db,
        image=_image(db, "10" * 32),
        item=item,
        role=None,
        is_primary=True,
        sort_order=5,
    )
    last = image_links.attach(
        db,
        image=_image(db, "11" * 32),
        item=item,
        role=None,
        is_primary=False,
        sort_order=9,
    )
    successor = image_links.attach(
        db,
        image=_image(db, "12" * 32),
        item=item,
        role=None,
        is_primary=False,
        sort_order=1,
    )
    db.flush()
    assert leaving.is_primary is True

    image_links.detach(db, leaving)
    db.flush()

    db.refresh(successor)
    db.refresh(last)
    assert successor.is_primary is True, "the lowest sort_order should take over"
    assert last.is_primary is False


def test_detaching_the_last_photograph_promotes_nothing(db: Session) -> None:
    """An item with no photographs left has no primary to give, and that is fine."""
    item = build_item(db)
    only = image_links.attach(
        db, image=_image(db, "13" * 32), item=item, role=None, is_primary=True
    )
    db.flush()

    image_links.detach(db, only)
    db.flush()

    remaining = db.scalars(
        select(ItemImage).where(ItemImage.inventory_item_id == item.id)
    ).all()
    assert remaining == []


def test_detaching_a_non_primary_leaves_the_primary_alone(db: Session) -> None:
    """Only a vacancy is filled; an item that still has a primary is untouched.

    Three links, and the surviving primary has the *highest* sort_order. So
    if the promotion ran without first checking whether a primary is still
    held, it would promote the sort_order 1 link alongside the incumbent --
    two primaries, which `uq_item_image_primary` refuses. The check is what
    this fixture makes load-bearing.
    """
    item = build_item(db)
    primary = image_links.attach(
        db,
        image=_image(db, "14" * 32),
        item=item,
        role=None,
        is_primary=True,
        sort_order=9,
    )
    spare = image_links.attach(
        db,
        image=_image(db, "15" * 32),
        item=item,
        role=None,
        is_primary=False,
        sort_order=5,
    )
    lowest = image_links.attach(
        db,
        image=_image(db, "16" * 32),
        item=item,
        role=None,
        is_primary=False,
        sort_order=1,
    )
    db.flush()

    image_links.detach(db, spare)
    db.flush()

    db.refresh(primary)
    db.refresh(lowest)
    assert primary.is_primary is True
    assert lowest.is_primary is False


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


def test_attaching_to_an_unknown_item_is_a_422(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """The item is named in the body, so an id that names nothing is a bad field."""
    image = _image(db, "6e" * 32)
    db.commit()

    refused = client.post(
        f"/api/images/{image.id}/links",
        json={"inventory_item_id": 999999},
        headers=admin_headers,
    )
    assert refused.status_code == 422, refused.text
    assert "999999" in refused.json()["detail"]


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
    assert db.get_one(ItemImage, first.id).is_primary is False


def test_an_explicit_null_clears_the_role(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """The console's blank option really does unfile the role.

    `image_role` defaults to None, so "omitted" and "explicitly null" are the
    same value -- `payload.model_fields_set` is the only thing that tells them
    apart, and testing the outgoing request shape alone cannot detect that the
    server ignored it. The assertion that matters is the stored column.
    """
    item = build_item(db)
    link = image_links.attach(
        db, image=_image(db, "e4" * 32), item=item, role="obverse", is_primary=False
    )
    db.commit()
    assert link.image_role_id is not None

    cleared = client.patch(
        f"/api/image-links/{link.id}",
        json={"image_role": None},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["image_role"] is None

    db.expire_all()
    assert db.get_one(ItemImage, link.id).image_role_id is None


def test_omitting_the_role_leaves_it_alone(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """The other half of the convention, and what stops the fix overreaching.

    A PATCH that says nothing about `image_role` must not clear it -- which is
    what a naive "always call set_role" would do to every promote.
    """
    item = build_item(db)
    link = image_links.attach(
        db, image=_image(db, "e5" * 32), item=item, role="obverse", is_primary=False
    )
    db.commit()
    role_id = link.image_role_id

    promoted = client.patch(
        f"/api/image-links/{link.id}",
        json={"is_primary": True},
        headers=admin_headers,
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["image_role"] == "obverse"

    db.expire_all()
    assert db.get_one(ItemImage, link.id).image_role_id == role_id


def test_destroying_the_primary_image_promotes_a_survivor(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """The cascade path leaves a primary behind too.

    `DELETE /api/images/{id}` destroys the image, and the database cascades
    its `item_image` rows away -- the one removal that never runs through
    `app.image_links`. Without a repair afterwards the item keeps two
    photographs and no primary, and the shop shows a buyer nothing.

    Goes through the API rather than calling the module, because calling the
    module is exactly what this path does not do.
    """
    item = build_item(db)
    doomed = _image(db, "c1" * 32)
    image_links.attach(
        db, image=doomed, item=item, role=None, is_primary=True, sort_order=5
    )
    image_links.attach(
        db,
        image=_image(db, "c2" * 32),
        item=item,
        role=None,
        is_primary=False,
        sort_order=9,
    )
    survivor = image_links.attach(
        db,
        image=_image(db, "c3" * 32),
        item=item,
        role=None,
        is_primary=False,
        sort_order=1,
    )
    db.commit()

    removed = client.delete(f"/api/images/{doomed.id}", headers=admin_headers)
    assert removed.status_code == 204, removed.text

    db.expire_all()
    remaining = db.scalars(
        select(ItemImage).where(ItemImage.inventory_item_id == item.id)
    ).all()
    assert len(remaining) == 2
    assert [link.id for link in remaining if link.is_primary] == [survivor.id]


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


def test_filing_on_a_split_item_is_a_422(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """A split item is retired into its parts: nothing is filed on it any more."""
    item = build_item(db, split_at=datetime(2026, 1, 1, tzinfo=UTC))
    image = _image(db, "5a" * 32)
    db.commit()

    refused = client.post(
        f"/api/images/{image.id}/links",
        json={"inventory_item_id": item.id},
        headers=admin_headers,
    )

    assert refused.status_code == 422, refused.text
    assert "split" in refused.json()["detail"]
    db.expire_all()
    assert db.scalar(select(ItemImage).where(ItemImage.image_id == image.id)) is None


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


# --------------------------------------------------------------------------
# Concurrent requests: the rows held, and what lands between read and hold
# --------------------------------------------------------------------------


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


def _holds_items(sent: Collection[str]) -> bool:
    """Whether any of these statements takes item rows FOR UPDATE."""
    return any("FOR UPDATE" in s and "FROM inventory_item" in s for s in sent)


def _after_the_guard(monkeypatch: pytest.MonkeyPatch, *statements: Executable) -> None:
    """Run `statements` once, just after the request's for-sale guard.

    As another request's commit would land: after this one read its rows and
    before it holds them. The instances this request loaded do not see it.
    """
    real = sale_state.guard
    landed: list[bool] = []

    def guard(
        db: Session,
        items: Collection[InventoryItem],
        *,
        acknowledged: bool,
        kinds: Collection[str] | None = None,
    ) -> None:
        """Ask the real guard, then land the other request's writes."""
        real(db, items, acknowledged=acknowledged, kinds=kinds)
        if landed:
            return
        landed.append(True)
        for statement in statements:
            db.execute(statement.execution_options(synchronize_session=False))

    monkeypatch.setattr(sale_state, "guard", guard)


def _moved_meanwhile(
    monkeypatch: pytest.MonkeyPatch, link: ItemImage, target: InventoryItem, heir: int
) -> None:
    """Another request moves `link` to `target`; `heir` takes the primary it left."""
    _after_the_guard(
        monkeypatch,
        update(ItemImage).where(ItemImage.id == link.id).values(is_primary=False),
        update(ItemImage).where(ItemImage.id == heir).values(is_primary=True),
        update(ItemImage)
        .where(ItemImage.id == link.id)
        .values(inventory_item_id=target.id, sort_order=1, is_primary=True),
    )


def _primaries(db: Session, item: InventoryItem) -> list[int]:
    """The item's primary links, as the database now holds them."""
    db.expire_all()
    return list(
        db.scalars(
            select(ItemImage.id).where(
                ItemImage.inventory_item_id == item.id, ItemImage.is_primary
            )
        )
    )


def _filed_pair(db: Session, sha: str) -> tuple[InventoryItem, ItemImage, ItemImage]:
    """An item with a primary photograph and a second one, committed."""
    item = build_item(db)
    primary = image_links.attach(
        db,
        image=_image(db, sha * 32),
        item=item,
        role=None,
        is_primary=True,
        sort_order=1,
    )
    second = image_links.attach(
        db,
        image=_image(db, sha[::-1] * 32),
        item=item,
        role=None,
        is_primary=False,
        sort_order=2,
    )
    db.commit()
    return item, primary, second


def test_destroying_a_photograph_holds_its_items_before_it_deletes(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """A photograph filed on the item meanwhile waits, and is given the primary.

    Unheld, the filing sees the doomed photograph as the incumbent and is not
    made primary, the delete cannot see the filing to promote it, and the
    item is left with a photograph and no primary.
    """
    item = build_item(db)
    doomed = _image(db, "c4" * 32)
    image_links.attach(db, image=doomed, item=item, role=None, is_primary=True)
    db.commit()

    sent = _statements_while(
        db,
        lambda: client.delete(f"/api/images/{doomed.id}", headers=admin_headers),
    )

    held = _first(sent, "FROM inventory_item", "FOR UPDATE")
    deleted = _first(sent, "DELETE FROM")
    assert held != -1, sent
    assert deleted != -1, sent
    assert held < deleted


def test_unfiling_moving_and_promoting_each_hold_the_item(db: Session) -> None:
    """The one primary is read and written by one request at a time."""
    _item, primary, second = _filed_pair(db, "d1")
    other = build_item(db)

    promoting = _statements_while(db, lambda: image_links.make_primary(db, second))
    moving = _statements_while(db, lambda: image_links.move(db, primary, other))
    unfiling = _statements_while(db, lambda: image_links.detach(db, second))

    assert _holds_items(promoting)
    assert _holds_items(moving)
    assert _holds_items(unfiling)


def test_making_primary_a_photograph_moved_meanwhile_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Promoted against the item it left, it would demote that item's new primary."""
    source, moving, staying = _filed_pair(db, "d2")
    target = build_item(db)
    _moved_meanwhile(monkeypatch, moving, target, staying.id)

    refused = client.patch(
        f"/api/image-links/{moving.id}",
        json={"is_primary": True},
        headers=admin_headers,
    )

    assert refused.status_code == 409, refused.text
    assert len(_primaries(db, source)) == 1


def test_unfiling_a_photograph_moved_meanwhile_is_refused(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is on another item now, which this request never asked about."""
    _source, moving, staying = _filed_pair(db, "d3")
    target = build_item(db)
    _moved_meanwhile(monkeypatch, moving, target, staying.id)

    refused = client.delete(f"/api/image-links/{moving.id}", headers=admin_headers)

    assert refused.status_code == 409, refused.text
    db.expire_all()
    assert db.get(ItemImage, moving.id) is not None


def test_re_roling_a_photograph_unfiled_meanwhile_is_a_409(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The link went between this request's read and its write: a conflict."""
    _item, _primary, second = _filed_pair(db, "d4")
    _after_the_guard(monkeypatch, delete(ItemImage).where(ItemImage.id == second.id))

    refused = client.patch(
        f"/api/image-links/{second.id}",
        json={"image_role": "reverse"},
        headers=admin_headers,
    )

    assert refused.status_code == 409, refused.text


def test_filing_a_photograph_destroyed_meanwhile_is_a_409(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The photograph went between this request's read and its write: a conflict."""
    item = build_item(db)
    image = _image(db, "d5" * 32)
    db.commit()
    _after_the_guard(monkeypatch, delete(Image).where(Image.id == image.id))

    refused = client.post(
        f"/api/images/{image.id}/links",
        json={"inventory_item_id": item.id},
        headers=admin_headers,
    )

    assert refused.status_code == 409, refused.text
