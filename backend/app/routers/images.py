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

from ..config import settings
from ..deps import AdminUser, DbSession
from ..imaging import (
    ImageRejected,
    cleanse,
    derivative_key,
    make_derivative,
    original_key,
)
from ..models import (
    DerivativeKind,
    Image,
    ImageDerivative,
    ImageRole,
    InventoryItem,
    ItemImage,
)
from ..references import code_to_id
from ..schemas import ImageOut
from ..storage import get_storage

router = APIRouter(prefix="/images", tags=["images"])

#: Longest edge per rendition. Both are generated at ingest rather than on
#: demand: a catalogue page asks for dozens of thumbnails at once, and
#: resizing on request turns one page view into dozens of decodes.
DERIVATIVE_SIZES: dict[DerivativeKind, int] = {
    DerivativeKind.thumb: settings.thumbnail_max_px,
    DerivativeKind.web: settings.web_max_px,
}

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


def ingest(db: Session, raw: bytes, source_ref: str | None) -> Image:
    """Cleanse, store and record one uploaded file. Idempotent by content."""
    try:
        cleansed = cleanse(raw)
    except ImageRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    existing = db.scalar(select(Image).where(Image.sha256 == cleansed.sha256))
    if existing is not None:
        return existing

    storage = get_storage()
    key = original_key(cleansed.sha256, cleansed.media_type)
    storage.put(key, cleansed.data)

    image = Image(
        sha256=cleansed.sha256,
        storage_key=key,
        media_type=cleansed.media_type,
        byte_size=len(cleansed.data),
        width=cleansed.width,
        height=cleansed.height,
        captured_at=cleansed.captured_at,
        source_ref=source_ref,
    )
    db.add(image)
    db.flush()

    for kind, longest_edge in DERIVATIVE_SIZES.items():
        data, width, height, media_type = make_derivative(cleansed.data, longest_edge)
        derived_key = derivative_key(cleansed.sha256, kind.value, media_type)
        storage.put(derived_key, data)
        db.add(
            ImageDerivative(
                image_id=image.id,
                kind=kind,
                storage_key=derived_key,
                width=width,
                height=height,
            )
        )

    db.flush()
    return image


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_image(
    db: DbSession,
    _admin: AdminUser,
    file: UploadFile = File(...),
    inventory_item_id: int | None = Form(default=None),
    image_role: str | None = Form(default=None),
    is_primary: bool = Form(default=False),
) -> ImageOut:
    """Upload a photograph, optionally attaching it to an inventory item.

    ``inventory_item_id`` is optional because photographs exist before anyone
    has decided what they depict. An unattached image is still stored,
    browsable and searchable -- linking is a separate, human step.
    """
    raw = await file.read()
    image = ingest(db, raw, source_ref=file.filename)

    if inventory_item_id is not None:
        item = db.get(InventoryItem, inventory_item_id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown inventory_item_id: {inventory_item_id}",
            )
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
def delete_image(image_id: int, db: DbSession, _admin: AdminUser) -> None:
    """Remove an image, its renditions and its stored bytes."""
    image = db.get(Image, image_id)
    if image is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )

    storage = get_storage()
    for derivative in image.derivatives:
        storage.delete(derivative.storage_key)
    storage.delete(image.storage_key)

    db.delete(image)
    db.commit()
