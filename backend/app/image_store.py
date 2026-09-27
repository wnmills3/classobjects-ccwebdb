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
from .imaging import (
    cleanse,
    derivative_key,
    file_extension,
    make_derivative,
    original_key,
)
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


#: The extensions a stored file's own format goes by.
_SAME_FORMAT: dict[str, tuple[str, ...]] = {
    "image/jpeg": ("jpg", "jpeg", "jpe"),
    "image/png": ("png",),
}


def named_as_stored(source_ref: str | None, media_type: str) -> str | None:
    """The file's name with the extension of what was stored.

    Every file is re-encoded on the way in -- a WebP or HEIC is stored as
    JPEG -- so a converted file's name says what is actually held:
    `CC-007595_02.webp` becomes `CC-007595_02.jpg`. A name already right
    for the format (`DSC00417.JPG`), or with no extension, is kept as it is.
    """
    if not source_ref:
        return source_ref
    stem, dot, extension = source_ref.rpartition(".")
    if not dot or not stem or "/" in extension or "\\" in extension:
        return source_ref
    # Already named for what it is (a JPEG called .JPG or .jpeg): kept as is.
    if extension.lower() in _SAME_FORMAT.get(media_type, ()):
        return source_ref
    return f"{stem}.{file_extension(media_type)}"


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
        source_ref=named_as_stored(source_ref, cleansed.media_type),
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
