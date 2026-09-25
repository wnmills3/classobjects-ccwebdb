"""Storing a photograph: cleanse, put the bytes, record the row.

Lifted out of `routers.images` because a CLI pass importing a router is
backwards. What is left in the router is the HTTP: reading an upload,
translating a refusal into a 422, serving renditions.

`ImageRejected` propagates rather than becoming an `HTTPException` here. The
router turns it into a 422 for a caller holding a request; `app.photo_import`
catches it and names the file it came from, which is the whole point of
separating them.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .imaging import cleanse, derivative_key, make_derivative, original_key
from .models import DerivativeKind, Image, ImageDerivative
from .storage import get_storage

__all__ = ["DERIVATIVE_SIZES", "ingest"]

#: Longest edge per rendition. Both are generated at ingest rather than on
#: demand: a catalog page asks for dozens of thumbnails at once, and
#: resizing on request turns one page view into dozens of decodes.
DERIVATIVE_SIZES: dict[DerivativeKind, int] = {
    DerivativeKind.thumb: settings.thumbnail_max_px,
    DerivativeKind.web: settings.web_max_px,
}


def ingest(db: Session, raw: bytes, source_ref: str | None) -> Image:
    """Cleanse, store and record one file. Idempotent by content.

    Raises `imaging.ImageRejected` if the bytes are not something we are
    willing to store.
    """
    cleansed = cleanse(raw)

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
