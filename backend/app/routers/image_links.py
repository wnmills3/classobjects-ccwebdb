"""Changing how a photograph is filed, and unfiling it.

A separate prefix from `/api/images` on purpose: that router already serves
`/{image_id}/{kind}`, and a literal `links` segment in the same position would
only avoid collision because `"links"` does not parse as an integer.
Correctness by declaration order is a trap.

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
from ..schemas import ImageLinkOut, ImageLinkUpdate
from .images import _link_out

router = APIRouter(prefix="/image-links", tags=["images"])


def _guarded_link(db: Session, link_id: int, *, acknowledged: bool) -> ItemImage:
    """The link, with its item's for-sale warning already answered."""
    link = db.get(ItemImage, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Link not found")
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
    # cannot tell the two apart, which made the console's blank option a
    # no-op.
    if "image_role" in payload.model_fields_set:
        image_links.set_role(db, link, payload.image_role)
    if payload.is_primary:
        image_links.make_primary(db, link)
    db.commit()
    db.refresh(link)
    return _link_out(db, link)


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
