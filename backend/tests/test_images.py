"""Image ingest, metadata removal and thumbnail delivery.

The tests that matter here are the ones about metadata. A photograph of a
valuable routinely carries the GPS coordinates of where it was taken -- which
is to say, of where the valuable is kept. "We strip EXIF" is a claim; these
are the checks that make it a guarantee.
"""

from __future__ import annotations

import io

import piexif
import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy.orm import Session

from app.imaging import MetadataRemainsError, cleanse, make_derivative
from app.models import DerivativeKind, Image, ItemImage, Listing


def make_jpeg(size=(800, 600), colour=(180, 140, 40)) -> bytes:
    buffer = io.BytesIO()
    PILImage.new("RGB", size, colour).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def make_jpeg_with_gps(size=(800, 600)) -> bytes:
    """A photograph carrying GPS coordinates and a capture time.

    This is what a phone actually produces, and the reason ingest strips
    metadata rather than trusting the uploader to have done so.
    """
    exif = {
        "0th": {piexif.ImageIFD.Make: b"TestCam", piexif.ImageIFD.Model: b"X100"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2024:03:15 10:30:00"},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((42, 1), (21, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"W",
            piexif.GPSIFD.GPSLongitude: ((71, 1), (3, 1), (0, 1)),
        },
        "1st": {},
        "thumbnail": None,
    }
    buffer = io.BytesIO()
    PILImage.new("RGB", size, (200, 150, 50)).save(
        buffer, format="JPEG", quality=90, exif=piexif.dump(exif)
    )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The metadata guarantee
# ---------------------------------------------------------------------------


def test_the_fixture_really_does_carry_gps() -> None:
    """Guard the guard: if this fails, every strip test below proves nothing."""
    raw = make_jpeg_with_gps()
    with PILImage.open(io.BytesIO(raw)) as image:
        exif = image.getexif()
        assert exif, "fixture has no EXIF at all"
        assert exif.get_ifd(0x8825), "fixture has no GPS block"


def test_gps_does_not_survive_ingest() -> None:
    cleansed = cleanse(make_jpeg_with_gps())

    with PILImage.open(io.BytesIO(cleansed.data)) as image:
        exif = image.getexif()
        assert not (exif and len(exif)), f"EXIF survived: {dict(exif)}"
        assert not (exif and exif.get_ifd(0x8825)), "GPS survived"
        assert not image.info.get("exif")


def test_capture_time_is_read_before_it_is_destroyed() -> None:
    """The one piece worth keeping: capture order is the strongest signal for
    linking unidentified photographs to items later."""
    cleansed = cleanse(make_jpeg_with_gps())
    assert cleansed.captured_at is not None
    assert cleansed.captured_at.year == 2024
    assert cleansed.captured_at.month == 3


def test_orientation_is_applied_to_the_pixels_not_just_dropped() -> None:
    """Stripping orientation without first applying it leaves every phone
    photograph sideways. The image must come out physically rotated."""
    exif = {"0th": {piexif.ImageIFD.Orientation: 6}, "Exif": {}, "GPS": {},
            "1st": {}, "thumbnail": None}
    buffer = io.BytesIO()
    # Landscape on disk, tagged "rotate 90" -- so it should end up portrait.
    PILImage.new("RGB", (900, 300), (10, 20, 30)).save(
        buffer, format="JPEG", exif=piexif.dump(exif)
    )

    cleansed = cleanse(buffer.getvalue())
    assert (cleansed.width, cleansed.height) == (300, 900)


def test_derivatives_are_also_clean() -> None:
    cleansed = cleanse(make_jpeg_with_gps())
    data, _, _, _ = make_derivative(cleansed.data, 320)
    with PILImage.open(io.BytesIO(data)) as thumb:
        exif = thumb.getexif()
        assert not (exif and len(exif))


def test_verification_rejects_an_image_it_could_not_clean(monkeypatch) -> None:
    """The verify step is the actual guarantee, so it must be able to fail.

    A strip that quietly did nothing has to be caught here rather than
    trusted -- that is the whole reason step 4 re-reads the written bytes.
    """
    import app.imaging as imaging

    def passthrough(image, quality=90):
        buffer = io.BytesIO()
        exif = {"0th": {piexif.ImageIFD.Make: b"Leaky"}, "Exif": {}, "GPS": {},
                "1st": {}, "thumbnail": None}
        image.convert("RGB").save(buffer, format="JPEG", exif=piexif.dump(exif))
        return buffer.getvalue(), "image/jpeg"

    monkeypatch.setattr(imaging, "_encode", passthrough)
    with pytest.raises(MetadataRemainsError):
        imaging.cleanse(make_jpeg())


# ---------------------------------------------------------------------------
# Ingest behaviour
# ---------------------------------------------------------------------------


def test_identity_is_the_hash_of_the_cleansed_bytes() -> None:
    """Two files differing only in metadata are the same photograph."""
    plain = cleanse(make_jpeg(colour=(1, 2, 3)))
    tagged_buffer = io.BytesIO()
    exif = {"0th": {piexif.ImageIFD.Make: b"Other"}, "Exif": {}, "GPS": {},
            "1st": {}, "thumbnail": None}
    PILImage.new("RGB", (800, 600), (1, 2, 3)).save(
        tagged_buffer, format="JPEG", quality=90, exif=piexif.dump(exif)
    )
    tagged = cleanse(tagged_buffer.getvalue())

    assert plain.sha256 == tagged.sha256


def test_a_thumbnail_is_not_enlarged() -> None:
    """A 200px photograph asked for a 320px thumbnail stays 200px rather than
    being blurrily scaled up."""
    cleansed = cleanse(make_jpeg(size=(200, 150)))
    _, width, height, _ = make_derivative(cleansed.data, 320)
    assert (width, height) == (200, 150)


def test_a_thumbnail_preserves_aspect_ratio() -> None:
    cleansed = cleanse(make_jpeg(size=(1600, 400)))
    _, width, height, _ = make_derivative(cleansed.data, 320)
    assert width == 320
    assert height == 80


def test_a_non_image_is_refused() -> None:
    from app.imaging import ImageRejected

    with pytest.raises(ImageRejected):
        cleanse(b"this is not an image")


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------


def test_upload_stores_the_image_and_both_renditions(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    response = client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg_with_gps(), "image/jpeg")},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()

    image = db.get(Image, body["id"])
    kinds = {d.kind for d in image.derivatives}
    assert kinds == {DerivativeKind.thumb, DerivativeKind.web}
    assert body["thumbnail_url"].endswith("/thumb")
    assert body["image_url"].endswith("/web")


def test_uploading_the_same_photograph_twice_stores_one_copy(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    raw = make_jpeg(colour=(7, 8, 9))
    first = client.post(
        "/api/images", files={"file": ("a.jpg", raw, "image/jpeg")},
        headers=admin_headers,
    ).json()
    second = client.post(
        "/api/images", files={"file": ("b.jpg", raw, "image/jpeg")},
        headers=admin_headers,
    ).json()
    assert first["id"] == second["id"]


def test_upload_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg(), "image/jpeg")},
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_a_thumbnail_is_publicly_servable(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    body = client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg(), "image/jpeg")},
        headers=admin_headers,
    ).json()

    # No auth header: the catalogue is public, so its images must be too.
    served = client.get(body["thumbnail_url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/")
    with PILImage.open(io.BytesIO(served.content)) as thumb:
        assert max(thumb.size) <= 320


def test_the_original_is_not_reachable_over_http(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Only derivatives are served. That is the second half of the metadata
    guarantee: an original is unreachable even if it retained something."""
    body = client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg(), "image/jpeg")},
        headers=admin_headers,
    ).json()

    assert client.get(f"/api/images/{body['id']}/original").status_code == 422
    assert client.get(f"/api/images/{body['id']}/full").status_code == 422


def test_uploading_against_an_item_makes_it_the_catalogue_thumbnail(
    client: TestClient, admin_headers: dict[str, str], listing: Listing, db: Session
) -> None:
    before = client.get(f"/api/catalog/{listing.id}").json()
    assert before["thumbnail_url"] is None

    client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg(), "image/jpeg")},
        data={
            "inventory_item_id": str(listing.inventory_item_id),
            "image_role": "obverse",
            "is_primary": "true",
        },
        headers=admin_headers,
    )

    after = client.get(f"/api/catalog/{listing.id}").json()
    assert after["thumbnail_url"] is not None
    assert client.get(after["thumbnail_url"]).status_code == 200


def test_only_one_photograph_can_be_primary(
    client: TestClient, admin_headers: dict[str, str], listing: Listing, db: Session
) -> None:
    """A partial unique index enforces it; the handler must clear the previous
    one in the same transaction rather than collide with it."""
    for colour in ((10, 10, 10), (20, 20, 20)):
        response = client.post(
            "/api/images",
            files={"file": ("c.jpg", make_jpeg(colour=colour), "image/jpeg")},
            data={
                "inventory_item_id": str(listing.inventory_item_id),
                "is_primary": "true",
            },
            headers=admin_headers,
        )
        assert response.status_code == 201, response.text

    db.expire_all()
    primaries = (
        db.query(ItemImage)
        .filter(
            ItemImage.inventory_item_id == listing.inventory_item_id,
            ItemImage.is_primary.is_(True),
        )
        .count()
    )
    assert primaries == 1


def test_a_photograph_can_exist_before_anyone_knows_what_it_shows(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Camera filenames carry only a timestamp, so linking is a human step.
    An unattached image must still be storable and servable."""
    body = client.post(
        "/api/images",
        files={"file": ("DSC00417.JPG", make_jpeg(colour=(3, 3, 3)), "image/jpeg")},
        headers=admin_headers,
    ).json()

    image = db.get(Image, body["id"])
    assert image.source_ref == "DSC00417.JPG"
    assert client.get(body["thumbnail_url"]).status_code == 200
