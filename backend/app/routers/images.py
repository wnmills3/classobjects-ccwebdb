"""Image upload and delivery.

**Originals are never served.** Public requests are answered only from
`image_derivative` rows. That is the second half of the metadata guarantee: even
if an original somehow retained something, it is not reachable over HTTP.

Uploads are content-addressed by the hash of the *cleansed* bytes, so
re-uploading the same photograph links the existing image rather than storing a
second copy. Two people photographing the same coin twice cost one file.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import sale_state
from ..config import settings
from ..deps import AdminUser, DbSession
from ..image_store import ingest
from ..imaging import ImageRejected
from ..models import (
    DerivativeKind,
    Image,
    ImageDerivative,
    ImageRole,
    InventoryItem,
    ItemImage,
)
from ..references import code_to_id
from ..schemas import ImageLinkOut, ImageOut
from ..storage import get_storage

router = APIRouter(prefix="/images", tags=["images"])

#: A derivative is immutable -- its key contains the hash of its source -- so
#: it can be cached hard. A year is the usual maximum.
CACHE_CONTROL = "public, max-age=31536000, immutable"


def image_urls(image_id: int) -> dict[str, str]:
    """Where an image's renditions are served from. Never the original."""
    return {
        "thumbnail_url": f"{settings.api_prefix}/images/{image_id}/thumb",
        "image_url": f"{settings.api_prefix}/images/{image_id}/web",
    }


def to_image_out(image: Image) -> ImageOut:
    """Project a stored image into the API shape."""
    return ImageOut(
        id=image.id,
        sha256=image.sha256,
        media_type=image.media_type,
        byte_size=image.byte_size,
        width=image.width,
        height=image.height,
        captured_at=image.captured_at,
        **image_urls(image.id),
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_image(
    db: DbSession,
    _admin: AdminUser,
    file: UploadFile = File(...),
    inventory_item_id: int | None = Form(default=None),
    image_role: str | None = Form(default=None),
    is_primary: bool = Form(default=False),
    acknowledge_for_sale: bool = Form(default=False),
) -> ImageOut:
    """Upload a photograph, optionally attaching it to an inventory item.

    ``inventory_item_id`` is optional because photographs exist before anyone
    has decided what they depict. An unattached image is still stored,
    browsable and searchable -- linking is a separate, human step.
    """
    raw = await file.read()
    try:
        image = ingest(db, raw, source_ref=file.filename)
    except ImageRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    if inventory_item_id is not None:
        item = db.get(InventoryItem, inventory_item_id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown inventory_item_id: {inventory_item_id}",
            )
        sale_state.guard(db, [item], acknowledged=acknowledge_for_sale)
        link = db.scalar(
            select(ItemImage).where(
                ItemImage.inventory_item_id == inventory_item_id,
                ItemImage.image_id == image.id,
            )
        )
        if link is None:
            link = ItemImage(inventory_item_id=inventory_item_id, image_id=image.id)
            db.add(link)
        link.image_role_id = code_to_id(db, ImageRole, image_role, "image_role")

        if is_primary:
            # At most one primary per item -- enforced by a partial unique
            # index, so the previous one must be cleared in the same
            # transaction rather than left to collide.
            db.query(ItemImage).filter(
                ItemImage.inventory_item_id == inventory_item_id,
                ItemImage.image_id != image.id,
                ItemImage.is_primary.is_(True),
            ).update({"is_primary": False}, synchronize_session=False)
            link.is_primary = True

    db.commit()
    db.refresh(image)
    return to_image_out(image)


@router.get("", response_model=list[ImageLinkOut])
def list_images(
    db: DbSession,
    _admin: AdminUser,
    inventory_item_id: int | None = None,
    unattached: bool = False,
) -> list[ImageLinkOut]:
    """An item's photographs, or the ones nobody has filed yet.

    One filter is required. An unfiltered list of every photograph in the
    collection is a page nobody asked for and a query that grows without
    bound; making the caller say which set it wants costs one parameter.
    """
    if (inventory_item_id is None) == (not unattached):
        raise HTTPException(
            status_code=422,
            detail="Pass exactly one of inventory_item_id or unattached=true.",
        )

    if unattached:
        linked = select(ItemImage.image_id).where(
            ItemImage.inventory_item_id.is_not(None)
        )
        rows = db.scalars(
            select(Image)
            .where(Image.id.not_in(linked))
            .order_by(Image.captured_at.desc().nullslast(), Image.id)
        ).all()
        return [
            ImageLinkOut(
                image_id=row.id, captured_at=row.captured_at, **image_urls(row.id)
            )
            for row in rows
        ]

    links = db.scalars(
        select(ItemImage)
        .where(ItemImage.inventory_item_id == inventory_item_id)
        .order_by(ItemImage.sort_order, ItemImage.id)
    ).all()
    return [_link_out(db, link) for link in links]


def _link_out(db: Session, link: ItemImage) -> ImageLinkOut:
    """Project a filed photograph into the API shape."""
    role = db.get(ImageRole, link.image_role_id) if link.image_role_id else None
    return ImageLinkOut(
        link_id=link.id,
        inventory_item_id=link.inventory_item_id,
        item_code=link.item.item_code if link.item else None,
        image_id=link.image_id,
        image_role=role.code if role else None,
        is_primary=link.is_primary,
        sort_order=link.sort_order,
        captured_at=link.image.captured_at,
        **image_urls(link.image_id),
    )


@router.get("/{image_id}/{kind}")
def get_derivative(image_id: int, kind: DerivativeKind, db: DbSession) -> Response:
    """Serve a rendition. Public, and the only way image bytes leave the app."""
    derivative = db.scalar(
        select(ImageDerivative).where(
            ImageDerivative.image_id == image_id, ImageDerivative.kind == kind
        )
    )
    if derivative is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )

    try:
        data = get_storage().get(derivative.storage_key)
    except (FileNotFoundError, ValueError) as exc:
        # A row pointing at bytes that are gone is a real fault worth
        # surfacing as 404 rather than a 500 -- but it means storage and the
        # database have diverged.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image data missing"
        ) from exc

    image = db.get(Image, image_id)
    return Response(
        content=data,
        media_type=image.media_type if image else "image/jpeg",
        headers={"Cache-Control": CACHE_CONTROL},
    )


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
    image_id: int,
    db: DbSession,
    _admin: AdminUser,
    acknowledge_for_sale: bool = False,
) -> None:
    """Remove an image, its renditions and its stored bytes.

    `acknowledge_for_sale` is a query parameter rather than a body field
    because DELETE has no body here. The shop serves an item's primary image
    (`routers.catalog`), so deleting one changes what a buyer is looking at.
    """
    image = db.get(Image, image_id)
    if image is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )

    # This endpoint knew only an image id. The items it is attached to are
    # what the for-sale rule is about, so they are read before anything is
    # removed.
    attached = db.scalars(
        select(InventoryItem)
        .join(ItemImage, ItemImage.inventory_item_id == InventoryItem.id)
        .where(ItemImage.image_id == image.id)
    ).all()
    sale_state.guard(db, list(attached), acknowledged=acknowledge_for_sale)

    storage = get_storage()
    for derivative in image.derivatives:
        storage.delete(derivative.storage_key)
    storage.delete(image.storage_key)

    db.delete(image)
    db.commit()
