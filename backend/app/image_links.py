"""Linking a photograph to an item -- the only writer of `item_image`.

The same rule `app.offering_writes` holds for listing status and
`app.lifecycle_writes` for item status: one module writes this table, so that
what a link means cannot drift between the console and the import pass.

**The primary swap is why this is a module and not four lines in a router.**
`uq_item_image_primary` is a partial unique index -- at most one primary per
item -- so promoting a photograph means demoting the incumbent first, inside
the same transaction. Two callers doing that independently is two chances to
get the order wrong, and the failure is a rejected write at commit time, far
from whoever caused it.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import PurePosixPath

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import Image, ImageRole, InventoryItem, ItemImage
from .references import code_to_id

__all__ = [
    "CHANGED_MEANWHILE",
    "LinkRefused",
    "attach",
    "detach",
    "fill_primary_vacancy",
    "hold_items",
    "make_primary",
    "move",
    "name_for_place",
    "next_position",
    "replace_role",
    "set_role",
]

#: A name given for a place on an item: its item code, its position, the
#: extension -- `CC-008078_01.jpg`, as the item editor names what it files.
_PLACE_NAME = re.compile(r"^(?P<code>.+)_(?P<position>\d{2,})(?P<ext>\.[A-Za-z0-9]+)$")


#: Why a link moved or unfiled between a request's read and its write is
#: refused.
CHANGED_MEANWHILE = (
    "This photograph was moved or unfiled while saving; "
    "read the photographs again and retry."
)


class LinkRefused(Exception):
    """This photograph cannot be linked to this item."""


def hold_items(db: Session, item_ids: Collection[int | None]) -> None:
    """Take these items' rows FOR UPDATE, in id order, before their photographs.

    A photograph's place is the item's last place plus one, and the shop's
    one primary is given when none is found: each is read and then written.
    Two requests filing photographs on one item at once would otherwise
    both take the same place -- and the same `CC-######_NN` name -- or both
    find no primary and both take it, which `uq_item_image_primary`
    refuses with an error rather than an answer. Holding the item's row
    makes the second wait and read what the first wrote.

    Taken before a stored picture's hash (`image_store.hold_content`) by
    anything that holds both, so two such requests cannot each hold what
    the other waits for.
    """
    ids = sorted({item_id for item_id in item_ids if item_id is not None})
    if ids:
        db.execute(
            select(InventoryItem.id)
            .where(InventoryItem.id.in_(ids))
            .order_by(InventoryItem.id)
            .with_for_update()
        )


def _still_on(db: Session, link: ItemImage, item_id: int | None) -> None:
    """Refuse unless `link`, read again under its item's hold, is still on it.

    The link is read before its item is held, and another request may move
    or unfile it in between: acted on as it was read, a promotion would
    demote the primary of the item it has left, and a removal would take it
    off an item this request never asked about.
    """
    current = db.scalar(
        select(ItemImage)
        .where(ItemImage.id == link.id)
        .execution_options(populate_existing=True)
    )
    if current is None or current.inventory_item_id != item_id:
        raise LinkRefused(CHANGED_MEANWHILE)


def _clear_primary(db: Session, item_id: int, keep: int | None = None) -> None:
    """Demote whatever is primary for this item, except `keep`.

    Runs before a promotion, never after: the partial unique index refuses a
    second primary, so the order is the whole trick.
    """
    stmt = update(ItemImage).where(
        ItemImage.inventory_item_id == item_id, ItemImage.is_primary
    )
    if keep is not None:
        stmt = stmt.where(ItemImage.id != keep)
    db.execute(stmt.values(is_primary=False))
    db.flush()


def next_position(db: Session, item_id: int) -> int:
    """The place after the item's last photograph: 1 for its first.

    The item's row is held from here to the caller's commit, so the place
    is still free when the caller files a photograph in it.
    """
    hold_items(db, [item_id])
    last = db.scalar(
        select(func.max(ItemImage.sort_order)).where(
            ItemImage.inventory_item_id == item_id
        )
    )
    return (last or 0) + 1


def attach(
    db: Session,
    *,
    image: Image,
    item: InventoryItem,
    role: str | None,
    is_primary: bool,
    sort_order: int | None = None,
) -> ItemImage:
    """Link `image` to `item`. Raises `LinkRefused` if it is already linked.

    An item that has photographs gets one the shop can show. `routers.catalog`
    serves an item's *primary* link and has no fallback to "the first one", so
    a photograph filed without `is_primary` onto an item that has no primary
    would otherwise be a photograph no buyer ever sees. Filling that vacancy
    here rather than in each caller is why `/management/photos`, the item editor's
    upload and the receiving screen cannot disagree about it.

    Filling a vacancy is never a demotion: an incumbent is displaced only when
    the caller asked for `is_primary`.

    With no `sort_order` it goes after the item's last photograph, so a
    reverse added to an item lists after its obverse.

    So `is_primary` is an input *and* an output: passing False may still
    produce a primary link, and the caller learns that only from
    `link.is_primary` on the row returned.
    """
    hold_items(db, [item.id])
    existing = db.scalar(
        select(ItemImage).where(
            ItemImage.inventory_item_id == item.id,
            ItemImage.image_id == image.id,
        )
    )
    if existing is not None:
        raise LinkRefused(
            f"{item.item_code} already has this photograph (link #{existing.id})."
        )

    if is_primary:
        _clear_primary(db, item.id)
    else:
        incumbent = db.scalar(
            select(ItemImage.id).where(
                ItemImage.inventory_item_id == item.id, ItemImage.is_primary
            )
        )
        is_primary = incumbent is None

    if sort_order is None:
        sort_order = next_position(db, item.id)

    link = ItemImage(
        inventory_item_id=item.id,
        image_id=image.id,
        image_role_id=code_to_id(db, ImageRole, role, "image_role"),
        is_primary=is_primary,
        sort_order=sort_order,
    )
    db.add(link)
    db.flush()
    return link


def name_for_place(db: Session, link: ItemImage) -> None:
    """Rename the photograph for the place `link` gives it, where it had one.

    Only a name that is an item's place -- another real item's code and a
    position -- is replaced: a camera's `DSC00417.JPG` says where the file
    came from and stays. And only a photograph filed nowhere else: a group
    photograph shared by three items is named for none of them alone. The
    name is read without the folder an imported file was in
    (`box3/CC-######_01.jpg`), and the new one carries none.
    """
    image = db.get(Image, link.image_id)
    item = db.get(InventoryItem, link.inventory_item_id)
    if image is None or item is None:
        return
    match = _PLACE_NAME.match(PurePosixPath(image.source_ref or "").name)
    if match is None:
        return
    shared = db.scalar(
        select(func.count())
        .select_from(ItemImage)
        .where(ItemImage.image_id == image.id, ItemImage.id != link.id)
    )
    named_for_an_item = db.scalar(
        select(InventoryItem.id).where(InventoryItem.item_code == match["code"])
    )
    if shared or named_for_an_item is None:
        return
    image.source_ref = f"{item.item_code}_{link.sort_order:02d}{match['ext']}"
    db.flush()


def move(db: Session, link: ItemImage, target: InventoryItem) -> ItemImage:
    """File this photograph on `target` instead, as what it already shows.

    It goes after the target's photographs, is the target's primary only if
    the target had none, and the item it leaves gets its next photograph as
    primary (`fill_primary_vacancy`). Renamed for its new place where its
    name was its old one (`name_for_place`).
    """
    source_id = link.inventory_item_id
    hold_items(db, [source_id, target.id])
    _still_on(db, link, source_id)
    if source_id == target.id:
        raise LinkRefused(f"This photograph is already filed on {target.item_code}.")
    held = db.scalar(
        select(ItemImage.id).where(
            ItemImage.inventory_item_id == target.id,
            ItemImage.image_id == link.image_id,
        )
    )
    if held is not None:
        raise LinkRefused(
            f"{target.item_code} already has this photograph (link #{held})."
        )

    # Demoted before it leaves: the target may have a primary of its own,
    # and the index allows one per item.
    link.is_primary = False
    db.flush()
    link.sort_order = next_position(db, target.id)
    link.inventory_item_id = target.id
    db.flush()
    incumbent = db.scalar(
        select(ItemImage.id).where(
            ItemImage.inventory_item_id == target.id,
            ItemImage.is_primary,
            ItemImage.id != link.id,
        )
    )
    if incumbent is None:
        link.is_primary = True
        db.flush()
    if source_id is not None:
        fill_primary_vacancy(db, [source_id])
    name_for_place(db, link)
    return link


def set_role(db: Session, link: ItemImage, role: str | None) -> None:
    """Say what this photograph shows -- obverse, reverse, slab, and so on."""
    link.image_role_id = code_to_id(db, ImageRole, role, "image_role")
    db.flush()


def replace_role(db: Session, old_role_id: int, new_role_id: int) -> None:
    """File every photograph shown as one role under another: a merged role.

    `app.reference_merge` calls it for an `image_role` merged away, having
    locked the items it moves. A role says only what a photograph shows --
    no primary or position depends on it -- so nothing else moves with it.
    """
    db.execute(
        update(ItemImage)
        .where(ItemImage.image_role_id == old_role_id)
        .values(image_role_id=new_role_id)
    )


def make_primary(db: Session, link: ItemImage) -> None:
    """Make this the photograph the shop shows, demoting the incumbent."""
    if link.inventory_item_id is None:
        raise LinkRefused("An unattached photograph cannot be an item's primary.")
    item_id = link.inventory_item_id
    hold_items(db, [item_id])
    _still_on(db, link, item_id)
    _clear_primary(db, item_id, keep=link.id)
    link.is_primary = True
    db.flush()


def fill_primary_vacancy(db: Session, item_ids: Collection[int]) -> list[ItemImage]:
    """Give a primary back to any of these items left with photographs but none.

    `attach` stops the vacancy ever appearing; this closes one that has just
    been opened. Two things open it, and neither goes through the rest of
    this module: `detach` removing the primary link, and the database
    cascading `item_image` away when `routers.images.delete_image` destroys
    the image itself (`item_image_image_id_fkey` is ON DELETE CASCADE).

    An item with photographs and no primary shows a buyer nothing at all --
    `routers.catalog` serves the primary link and has no fallback to "the
    first one". The successor is the lowest `sort_order`, then the lowest id:
    the order the item editor and `/management/photos` already list them in, so
    the photograph that takes over is the one the owner sees at the top.

    Returns the links promoted, so a caller can say what it did.
    """
    promoted = []
    for item_id in item_ids:
        held = db.scalar(
            select(ItemImage.id).where(
                ItemImage.inventory_item_id == item_id, ItemImage.is_primary
            )
        )
        if held is not None:
            continue
        successor = db.scalar(
            select(ItemImage)
            .where(ItemImage.inventory_item_id == item_id)
            .order_by(ItemImage.sort_order, ItemImage.id)
            .limit(1)
        )
        if successor is None:
            continue
        successor.is_primary = True
        promoted.append(successor)
    if promoted:
        db.flush()
    return promoted


def detach(db: Session, link: ItemImage) -> None:
    """Unfile the photograph. The image itself is untouched and remains.

    If it was the item's primary, the next photograph in display order takes
    over -- see `fill_primary_vacancy`. Without that, unfiling the primary
    from an item holding three photographs would leave all three unreachable
    to a buyer.
    """
    item_id = link.inventory_item_id
    hold_items(db, [item_id])
    _still_on(db, link, item_id)
    db.delete(link)
    db.flush()
    if item_id is not None:
        fill_primary_vacancy(db, [item_id])
