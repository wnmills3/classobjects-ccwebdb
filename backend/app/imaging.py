"""Image ingest: read metadata, orient, strip, verify, hash, store.

The order in `docs/database-design.md` section 8 is not arbitrary:

    1. read     capture DateTimeOriginal, dimensions, orientation into columns
    2. rotate   apply the orientation transform to the pixels
    3. strip    drop every metadata segment
    4. verify   reopen and assert none remains -- fail the ingest if any does
    5. hash     sha256 of the cleansed file; that is the identity
    6. store    object storage; database records metadata only

**Why metadata is removed at ingest and not at publish.** Photographs of
valuables routinely carry the GPS coordinates of where they were taken -- which
is to say, of where the valuables are kept. Stripping at publish time means the
coordinates sit in storage until someone remembers; stripping at ingest means
they were never accepted.

**Why step 2 comes before step 3.** Orientation is itself metadata. Removing it
without first applying it to the pixels leaves every phone photograph sideways.

**Why step 4 exists at all.** It re-reads the bytes that were actually written,
rather than trusting the library that wrote them. That is the difference
between believing the metadata is gone and knowing it is.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageOps

from .config import settings

__all__ = [
    "CleansedImage",
    "ImageRejected",
    "MetadataRemainsError",
    "cleanse",
    "derivative_key",
    "make_derivative",
    "original_key",
]

#: EXIF tag numbers. Named rather than magic, because 0x9003 tells no one
#: anything.
_EXIF_DATETIME_ORIGINAL = 0x9003
_EXIF_IFD = 0x8769
_EXIF_GPS_IFD = 0x8825

#: Pillow's own bomb guard. Set from configuration so one limit governs.
Image.MAX_IMAGE_PIXELS = settings.max_image_pixels


class ImageRejected(ValueError):
    """The upload is not something we are willing to store."""


class MetadataRemainsError(ImageRejected):
    """Verification failed: metadata survived the strip.

    Raised rather than warned about. An image whose metadata could not be
    removed must not enter the system, because the only thing worse than not
    having the guarantee is believing you have it.
    """


@dataclass(frozen=True)
class CleansedImage:
    """The result of ingest: bytes that are safe to store, and what we learned."""

    data: bytes
    sha256: str
    media_type: str
    width: int
    height: int
    #: Read out of EXIF before it was destroyed. Kept because capture order is
    #: the most useful signal for linking photographs to items later.
    captured_at: datetime | None


def _open(raw: bytes) -> Image.Image:
    if len(raw) > settings.max_upload_bytes:
        raise ImageRejected(
            f"image is {len(raw)} bytes, over the "
            f"{settings.max_upload_bytes} byte limit"
        )
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Image.DecompressionBombError as exc:
        raise ImageRejected(f"image is implausibly large: {exc}") from exc
    # UnidentifiedImageError derives from OSError, so OSError alone covers
    # both the not-an-image case and an unreadable file.
    except OSError as exc:
        raise ImageRejected(f"not a readable image: {exc}") from exc
    return image


def _captured_at(image: Image.Image) -> datetime | None:
    """DateTimeOriginal, if the camera recorded one and it parses."""
    try:
        exif = image.getexif()
    except Exception:
        return None
    if not exif:
        return None
    # DateTimeOriginal lives in the Exif sub-IFD, not the top-level one, so
    # a plain exif.get() finds nothing on a real camera file.
    raw = exif.get_ifd(_EXIF_IFD).get(_EXIF_DATETIME_ORIGINAL) or exif.get(
        _EXIF_DATETIME_ORIGINAL
    )
    if not raw:
        return None
    try:
        # The EXIF spec's own format, which is not ISO 8601.
        return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def _encode(image: Image.Image, quality: int = 90) -> tuple[bytes, str]:
    """Re-encode from pixel data alone, carrying no metadata across.

    A fresh image is built and the pixels copied into it, rather than saving
    the original with metadata suppressed. Saving relies on knowing every
    channel a format might smuggle information through -- EXIF, ICC profiles,
    XMP, comment blocks; copying the pixels relies on nothing.
    """
    has_alpha = image.mode in ("RGBA", "LA", "P")
    target_mode = "RGBA" if has_alpha else "RGB"
    converted = image.convert(target_mode)

    # Round-trip through raw pixel bytes. The new image is constructed from
    # nothing but the pixels, so there is no `info` dict for a format to write
    # metadata back out of.
    bare = Image.frombytes(target_mode, converted.size, converted.tobytes())

    buffer = io.BytesIO()
    if has_alpha:
        bare.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), "image/png"
    bare.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue(), "image/jpeg"


def _assert_no_metadata(data: bytes) -> None:
    """Step 4. Re-read what was written and prove it is clean."""
    with Image.open(io.BytesIO(data)) as check:
        exif = check.getexif()
        if exif and len(exif):
            raise MetadataRemainsError(
                f"EXIF survived the strip: {sorted(exif.keys())}"
            )
        if exif and exif.get_ifd(_EXIF_GPS_IFD):
            raise MetadataRemainsError("GPS data survived the strip")
        for channel in ("exif", "icc_profile", "XML:com.adobe.xmp", "comment"):
            if check.info.get(channel):
                raise MetadataRemainsError(f"{channel} survived the strip")


def cleanse(raw: bytes) -> CleansedImage:
    """Run the whole pipeline over one uploaded file."""
    image = _open(raw)

    # 1. read -- before anything destroys it
    captured_at = _captured_at(image)

    # 2. rotate -- apply orientation to the pixels while we still know it
    upright = ImageOps.exif_transpose(image) or image

    # 3. strip
    data, media_type = _encode(upright)

    # 4. verify -- from the written bytes, not from what we believe we did
    _assert_no_metadata(data)

    # 5. hash the cleansed file: identity is the identity of what is stored,
    #    so re-uploading the same photograph is a no-op rather than a duplicate
    digest = hashlib.sha256(data).hexdigest()

    return CleansedImage(
        data=data,
        sha256=digest,
        media_type=media_type,
        width=upright.width,
        height=upright.height,
        captured_at=captured_at,
    )


def make_derivative(data: bytes, longest_edge: int) -> tuple[bytes, int, int, str]:
    """A public-safe rendition, scaled to fit a box of `longest_edge`.

    Derived from the already-cleansed bytes, so a derivative cannot reintroduce
    metadata the original no longer has. Never enlarges: a 200px photograph
    asked for a 320px thumbnail stays 200px rather than being blurrily scaled
    up.
    """
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        rendition = image.copy()
    rendition.thumbnail((longest_edge, longest_edge), Image.Resampling.LANCZOS)
    encoded, media_type = _encode(rendition, quality=82)
    _assert_no_metadata(encoded)
    return encoded, rendition.width, rendition.height, media_type


def _extension(media_type: str) -> str:
    return "png" if media_type == "image/png" else "jpg"


def original_key(sha256: str, media_type: str) -> str:
    """Content-addressed, fanned out by the first byte of the hash.

    The fan-out keeps any one directory from accumulating tens of thousands of
    entries, which some filesystems handle poorly and every `ls` handles
    badly.
    """
    return f"originals/{sha256[:2]}/{sha256}.{_extension(media_type)}"


def derivative_key(sha256: str, kind: str, media_type: str) -> str:
    """Storage key for one rendition of an image, fanned out by hash."""
    return f"derivatives/{kind}/{sha256[:2]}/{sha256}.{_extension(media_type)}"
