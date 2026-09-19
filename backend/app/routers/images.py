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

from .. import image_links, sale_state
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
from ..schemas import ImageLinkIn, ImageLinkOut, ImageOut
from ..storage import get_storage

router = APIRouter(prefix="/images", tags=["images"])

#: A derivative is immutable -- its key contains the hash of its source -- so
#: it can be cached hard. A year is the usual maximum.
CACHE_CONTROL = "public, max-age=31536000, immutable"


def image_urls(sha256: str) -> dict[str, str]:
    """Where an image's renditions are served from. Never the original.

    Keyed by the content hash, not the row id. These URLs have to work in a
    plain `<img>` tag -- for an anonymous buyer in the shop and for the owner
    in the console -- and an `<img>` cannot carry a bearer token, so the route
    behind them is public. A sequential id therefore made every photograph in
    the collection reachable by counting from 1, and most of this collection
    is not for sale. The hash is unguessable and unique (`uq_image_sha256`),
    so there is nothing left to enumerate.
    """
    return {
        "thumbnail_url": f"{settings.api_prefix}/images/{sha256}/thumb",
        "image_url": f"{settings.api_prefix}/images/{sha256}/web",
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
        **image_urls(image.sha256),
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

        try:
            link = image_links.attach(
                db,
                image=image,
                item=item,
                role=image_role,
                is_primary=is_primary,
            )
        except image_links.LinkRefused as exc:
            # Re-uploading a photograph the item already has updates how it is
            # filed rather than refusing: the upload endpoint has always been
            # an upsert, and the caller is a file picker, not a filing
            # decision. Both writes still go through `image_links`, which is
            # what keeps the primary swap in one place.
            existing = db.scalar(
                select(ItemImage).where(
                    ItemImage.inventory_item_id == item.id,
                    ItemImage.image_id == image.id,
                )
            )
            if existing is None:
                # `LinkRefused` says the link is there, so not finding it
                # means it was removed between the refusal and this read.
                # A real check, not a bare `assert`: an assert vanishes
                # under `python -O` and this branch would then go on to
                # call `set_role` on None.
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(exc)
                ) from exc
            link = existing
            # Only when the form actually carried a role. A file picker
            # re-picking the same file sends no `image_role`, and an
            # unconditional `set_role` would clear whatever the operator had
            # just set -- the same omitted-versus-explicit-null distinction
            # `routers.image_links.update_link` draws.
            if image_role is not None:
                image_links.set_role(db, link, image_role)
            if is_primary:
                image_links.make_primary(db, link)

    db.commit()
    db.refresh(image)
    return to_image_out(image)


@router.post(
    "/{image_id}/links",
    status_code=status.HTTP_201_CREATED,
    response_model=ImageLinkOut,
)
def attach_image(
    image_id: int, payload: ImageLinkIn, db: DbSession, _admin: AdminUser
) -> ImageLinkOut:
    """File a photograph against an item.

    Refused with 409 for an item that is for sale until the caller
    acknowledges it: the shop serves an item's primary photograph, so filing
    one changes what a buyer is looking at.
    """
    image = db.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    item = db.get(InventoryItem, payload.inventory_item_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"No such item: {payload.inventory_item_id}",
        )

    sale_state.guard(db, [item], acknowledged=payload.acknowledge_for_sale)

    try:
        link = image_links.attach(
            db,
            image=image,
            item=item,
            role=payload.image_role,
            is_primary=payload.is_primary,
            sort_order=payload.sort_order,
        )
    except image_links.LinkRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(link)
    return _link_out(db, link)


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
                image_id=row.id, captured_at=row.captured_at, **image_urls(row.sha256)
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
        **image_urls(link.image.sha256),
    )


@router.get("/{sha256}/{kind}")
def get_derivative(sha256: str, kind: DerivativeKind, db: DbSession) -> Response:
    """Serve a rendition. Public, and the only way image bytes leave the app.

    Public is deliberate: a listed coin's photograph must load for a buyer who
    has never signed in. What keeps the unlisted rest of the collection out of
    reach is the path -- see `image_urls`. Addressed by content hash, so a
    caller who has not been given a URL has nothing to walk.
    """
    image = db.scalar(select(Image).where(Image.sha256 == sha256))
    if image is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )

    derivative = db.scalar(
        select(ImageDerivative).where(
            ImageDerivative.image_id == image.id, ImageDerivative.kind == kind
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

    return Response(
        content=data,
        media_type=image.media_type,
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
