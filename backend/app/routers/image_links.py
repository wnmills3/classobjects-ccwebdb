"""Changing how a photograph is filed, and unfiling it.

A separate prefix from `/api/images` on purpose: that router already serves
`/{sha256}/{kind}`, whose first segment is a string, so a literal `links`
segment in the same position would be told apart from a hash only by the
order the routes are declared in. Correctness by declaration order is a trap.

**Detaching is not deleting.** `DELETE /api/images/{id}` destroys the
photograph and its stored bytes; this removes the link and leaves the
photograph, unattached, to be filed somewhere else. A mis-filed photograph is
a filing error, and a filing error must not be data loss.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.orm import Session

from .. import image_links, sale_state
from ..deps import AdminUser, DbSession
from ..models import InventoryItem, ItemImage
from ..schemas import ImageLinkMove, ImageLinkOut, ImageLinkUpdate
from ._resolve import get_or_404
from .images import link_out

router = APIRouter(prefix="/image-links", tags=["images"])


def _guarded_link(db: Session, link_id: int, *, acknowledged: bool) -> ItemImage:
    """The link, with its item's for-sale warning already answered."""
    link = get_or_404(db, ItemImage, link_id, "Link not found")
    item = (
        db.get(InventoryItem, link.inventory_item_id)
        if link.inventory_item_id is not None
        else None
    )
    sale_state.guard(db, [item] if item else [], acknowledged=acknowledged)
    return link


@router.patch("/{link_id}", response_model=ImageLinkOut)
def update_link(
    link_id: int, payload: ImageLinkUpdate, db: DbSession, _admin: AdminUser
) -> ImageLinkOut:
    """Say what this photograph shows, or make it the one the shop uses."""
    link = _guarded_link(db, link_id, acknowledged=payload.acknowledge_for_sale)
    # model_fields_set, not `is not None`: an omitted field is left alone, an
    # explicit null clears the role -- the same convention `routers.offers`,
    # `routers.sales_venues` and `routers.inventory` follow. Testing for None
    # cannot tell the two apart, which would make the console's blank option
    # a no-op.
    if "image_role" in payload.model_fields_set:
        image_links.set_role(db, link, payload.image_role)
    if payload.is_primary:
        image_links.make_primary(db, link)
    db.commit()
    db.refresh(link)
    return link_out(db, link)


@router.post("/{link_id}/move", response_model=ImageLinkOut)
def move_link(
    link_id: int, payload: ImageLinkMove, db: DbSession, _admin: AdminUser
) -> ImageLinkOut:
    """File this photograph on another item instead (`image_links.move`).

    Both items' for-sale warnings are answered by the one acknowledgement:
    the item it leaves and the item it joins both change what a buyer sees.
    """
    link = _guarded_link(db, link_id, acknowledged=payload.acknowledge_for_sale)
    target = get_or_404(
        db,
        InventoryItem,
        payload.inventory_item_id,
        f"No such item: {payload.inventory_item_id}",
    )
    sale_state.guard(db, [target], acknowledged=payload.acknowledge_for_sale)
    try:
        image_links.move(db, link, target)
    except image_links.LinkRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    db.commit()
    db.refresh(link)
    return link_out(db, link)


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def detach_link(
    link_id: int,
    db: DbSession,
    _admin: AdminUser,
    acknowledge_for_sale: bool = False,
) -> Response:
    """Unfile the photograph. The image itself remains, unattached."""
    link = _guarded_link(db, link_id, acknowledged=acknowledge_for_sale)
    image_links.detach(db, link)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
