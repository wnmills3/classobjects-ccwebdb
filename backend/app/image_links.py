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

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Image, ImageRole, InventoryItem, ItemImage
from .references import code_to_id

__all__ = ["LinkRefused", "attach", "detach", "make_primary", "set_role"]


class LinkRefused(Exception):
    """This photograph cannot be linked to this item."""


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


def attach(
    db: Session,
    *,
    image: Image,
    item: InventoryItem,
    role: str | None,
    is_primary: bool,
    sort_order: int = 0,
) -> ItemImage:
    """Link `image` to `item`. Raises `LinkRefused` if it is already linked."""
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


def set_role(db: Session, link: ItemImage, role: str | None) -> None:
    """Say what this photograph shows -- obverse, reverse, slab, and so on."""
    link.image_role_id = code_to_id(db, ImageRole, role, "image_role")
    db.flush()


def make_primary(db: Session, link: ItemImage) -> None:
    """Make this the photograph the shop shows, demoting the incumbent."""
    if link.inventory_item_id is None:
        raise LinkRefused("An unattached photograph cannot be an item's primary.")
    _clear_primary(db, link.inventory_item_id, keep=link.id)
    link.is_primary = True
    db.flush()


def detach(db: Session, link: ItemImage) -> None:
    """Unfile the photograph. The image itself is untouched and remains."""
    db.delete(link)
    db.flush()
