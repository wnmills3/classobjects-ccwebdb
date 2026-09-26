"""Image ingest, metadata removal and thumbnail delivery.

The tests that matter here are the ones about metadata. A photograph of a
valuable routinely carries the GPS coordinates of where it was taken -- which
is to say, of where the valuable is kept. "We strip EXIF" is a claim; these
are the checks that make it a guarantee.
"""

from __future__ import annotations

import io

# piexif ships neither stubs nor a py.typed marker, so there is nothing for the
# checker to read. The alternative is a `[[tool.mypy.overrides]]` entry in
# pyproject.toml; this keeps the statement next to the one import that needs it.
import piexif  # type: ignore[import-untyped]
import pytest
from app.imaging import MetadataRemainsError, cleanse, make_derivative
from app.models import DerivativeKind, Image, ImageRole, ItemImage, Listing
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy.orm import Session

from tests.builders import make_jpeg


def make_jpeg_with_gps(size: tuple[int, int] = (800, 600)) -> bytes:
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
    """Capture time is read before the metadata is destroyed.

    The one piece worth keeping: capture order is the strongest signal for
    linking unidentified photographs to items later.
    """
    cleansed = cleanse(make_jpeg_with_gps())
    assert cleansed.captured_at is not None
    assert cleansed.captured_at.year == 2024
    assert cleansed.captured_at.month == 3


def test_orientation_is_applied_to_the_pixels_not_just_dropped() -> None:
    """Orientation reaches the pixels, not just the metadata.

    Stripping orientation without first applying it leaves every phone
    photograph sideways. The image must come out physically rotated.
    """
    exif = {
        "0th": {piexif.ImageIFD.Orientation: 6},
        "Exif": {},
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
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


def test_verification_rejects_an_image_it_could_not_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verify step is the actual guarantee, so it must be able to fail.

    A strip that quietly did nothing has to be caught here rather than
    trusted -- that is the whole reason step 4 re-reads the written bytes.
    """
    import app.imaging as imaging

    def passthrough(image: PILImage.Image, quality: int = 90) -> tuple[bytes, str]:
        buffer = io.BytesIO()
        exif = {
            "0th": {piexif.ImageIFD.Make: b"Leaky"},
            "Exif": {},
            "GPS": {},
            "1st": {},
            "thumbnail": None,
        }
        image.convert("RGB").save(buffer, format="JPEG", exif=piexif.dump(exif))
        return buffer.getvalue(), "image/jpeg"

    monkeypatch.setattr(imaging, "_encode", passthrough)
    jpeg = make_jpeg()
    with pytest.raises(MetadataRemainsError):
        imaging.cleanse(jpeg)


def test_surviving_gps_is_reported_as_gps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal must name GPS when GPS is what survived.

    GPS is the reason this pipeline exists -- a photograph of a valuable
    carries the coordinates of where it is kept -- so "GPS data survived the
    strip" is a different emergency from "some EXIF survived", and the person
    reading the error needs to be told which.

    This could not happen before: GPS is reached through the 0x8825 pointer,
    which is itself a top-level EXIF entry, so the general check always fired
    first and the GPS branch was unreachable. Asserting on the message, not
    just the exception type, is what makes that difference visible -- the old
    code raised `MetadataRemainsError` here too, and a test that checked only
    the type would have passed against the dead branch.
    """
    import app.imaging as imaging

    def leaks_gps(image: PILImage.Image, quality: int = 90) -> tuple[bytes, str]:
        buffer = io.BytesIO()
        exif = {
            "0th": {},
            "Exif": {},
            "GPS": {
                piexif.GPSIFD.GPSLatitudeRef: b"N",
                piexif.GPSIFD.GPSLatitude: ((41, 1), (52, 1), (0, 1)),
            },
            "1st": {},
            "thumbnail": None,
        }
        image.convert("RGB").save(buffer, format="JPEG", exif=piexif.dump(exif))
        return buffer.getvalue(), "image/jpeg"

    monkeypatch.setattr(imaging, "_encode", leaks_gps)
    with pytest.raises(MetadataRemainsError, match="GPS"):
        imaging.cleanse(make_jpeg())


# ---------------------------------------------------------------------------
# Ingest behavior
# ---------------------------------------------------------------------------


def test_identity_is_the_hash_of_the_cleansed_bytes() -> None:
    """Two files differing only in metadata are the same photograph."""
    plain = cleanse(make_jpeg(color=(1, 2, 3)))
    tagged_buffer = io.BytesIO()
    exif = {
        "0th": {piexif.ImageIFD.Make: b"Other"},
        "Exif": {},
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
    PILImage.new("RGB", (800, 600), (1, 2, 3)).save(
        tagged_buffer, format="JPEG", quality=90, exif=piexif.dump(exif)
    )
    tagged = cleanse(tagged_buffer.getvalue())

    assert plain.sha256 == tagged.sha256


def test_a_thumbnail_is_not_enlarged() -> None:
    """A rendition is never enlarged beyond its source.

    A 200px photograph asked for a 320px thumbnail stays 200px rather than
    being blurrily scaled up.
    """
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

    image = db.get_one(Image, body["id"])
    kinds = {d.kind for d in image.derivatives}
    assert kinds == {DerivativeKind.thumb, DerivativeKind.web}
    assert body["thumbnail_url"].endswith("/thumb")
    assert body["image_url"].endswith("/web")


def test_uploading_the_same_photograph_twice_stores_one_copy(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    raw = make_jpeg(color=(7, 8, 9))
    first = client.post(
        "/api/images",
        files={"file": ("a.jpg", raw, "image/jpeg")},
        headers=admin_headers,
    ).json()
    second = client.post(
        "/api/images",
        files={"file": ("b.jpg", raw, "image/jpeg")},
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


def test_a_bad_upload_is_a_422_naming_the_refusal(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """`ImageRejected` reaches an HTTP caller as a 422, not a 500.

    Same bytes `test_a_non_image_is_refused` feeds `cleanse()` directly --
    but this goes through the actual endpoint, so a wrong exception type
    caught, a wrong status code, or a `try` that stopped wrapping the call
    would show up here even though the suite stays green everywhere else.
    """
    response = client.post(
        "/api/images",
        files={"file": ("not-a-photo.jpg", b"this is not an image", "image/jpeg")},
        headers=admin_headers,
    )
    assert response.status_code == 422, response.text
    assert "not a readable image" in response.json()["detail"]


def test_a_thumbnail_is_publicly_servable(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    body = client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg(), "image/jpeg")},
        headers=admin_headers,
    ).json()

    # No auth header: the catalog is public, so its images must be too.
    served = client.get(body["thumbnail_url"])
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/")
    with PILImage.open(io.BytesIO(served.content)) as thumb:
        assert max(thumb.size) <= 320


def test_photographs_cannot_be_found_by_counting(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The serving path is the content hash, so there is no sequence to walk.

    The route is public and has to be: an `<img>` tag carries no bearer
    token, so a listed coin's photograph must load for a signed-out buyer.
    That makes the URL itself the only thing standing between a stranger and
    a photograph of every valuable the owner keeps in a safe-deposit box.
    Keyed by row id, `/api/images/1/web`, `/2/web`, `/3/web` walked the whole
    collection.

    Asserted against a real uploaded image, so the test fails if the route
    ever accepts an id again -- not against a made-up number, which would
    404 whatever the route did and prove nothing.
    """
    body = client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg(), "image/jpeg")},
        headers=admin_headers,
    ).json()

    # The hash serves. This half is what makes the other half meaningful.
    assert client.get(body["thumbnail_url"]).status_code == 200
    assert body["sha256"] in body["thumbnail_url"]

    # The id of that very same image serves nothing.
    assert client.get(f"/api/images/{body['id']}/thumb").status_code == 404
    assert client.get(f"/api/images/{body['id']}/web").status_code == 404


def test_the_original_is_not_reachable_over_http(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Only derivatives are served.

    That is the second half of the metadata guarantee: an original is
    unreachable even if it retained something.
    """
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

    # The `listing` fixture makes the item for sale, so this attach needs
    # the same acknowledgement `test_for_sale_guards.py` covers -- this test
    # is about the catalog thumbnail, not the guard, so it just clears it.
    client.post(
        "/api/images",
        files={"file": ("coin.jpg", make_jpeg(), "image/jpeg")},
        data={
            "inventory_item_id": str(listing.inventory_item_id),
            "image_role": "obverse",
            "is_primary": "true",
            "acknowledge_for_sale": "true",
        },
        headers=admin_headers,
    )

    after = client.get(f"/api/catalog/{listing.id}").json()
    assert after["thumbnail_url"] is not None
    assert client.get(after["thumbnail_url"]).status_code == 200


def test_only_one_photograph_can_be_primary(
    client: TestClient, admin_headers: dict[str, str], listing: Listing, db: Session
) -> None:
    """Only one photograph per item may be the primary one.

    A partial unique index enforces it; the handler must clear the previous
    one in the same transaction rather than collide with it.
    """
    for color in ((10, 10, 10), (20, 20, 20)):
        # The `listing` fixture makes the item for sale, so each attach
        # needs the acknowledgement `test_for_sale_guards.py` covers.
        response = client.post(
            "/api/images",
            files={"file": ("c.jpg", make_jpeg(color=color), "image/jpeg")},
            data={
                "inventory_item_id": str(listing.inventory_item_id),
                "is_primary": "true",
                "acknowledge_for_sale": "true",
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


def test_re_uploading_the_same_photograph_keeps_its_role_and_primacy(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The upsert branch, which nothing else in the suite reaches.

    `test_uploading_the_same_photograph_twice_stores_one_copy` posts without
    an item, so `attach` is never called; `test_only_one_photograph_can_be_
    primary` posts two *different* images. Only the same bytes against the
    same item take the `LinkRefused` path -- the one behavior the mid-branch
    migration onto `image_links` could have changed.

    The second post is what a file picker sends: no `image_role`, no
    `is_primary`. Neither may undo the filing decision the operator already
    made.
    """
    from tests.conftest import build_item

    item = build_item(db)
    db.commit()
    raw = make_jpeg(color=(21, 22, 23))

    first = client.post(
        "/api/images",
        files={"file": ("coin.jpg", raw, "image/jpeg")},
        data={
            "inventory_item_id": str(item.id),
            "image_role": "obverse",
            "is_primary": "true",
        },
        headers=admin_headers,
    )
    assert first.status_code == 201, first.text

    again = client.post(
        "/api/images",
        files={"file": ("coin.jpg", raw, "image/jpeg")},
        data={"inventory_item_id": str(item.id)},
        headers=admin_headers,
    )
    assert again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"]

    db.expire_all()
    links = db.query(ItemImage).filter(ItemImage.inventory_item_id == item.id).all()
    assert len(links) == 1
    assert links[0].is_primary is True
    role = db.get(ImageRole, links[0].image_role_id)
    assert role is not None
    assert role.code == "obverse"


def test_a_photograph_can_exist_before_anyone_knows_what_it_shows(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Camera filenames carry only a timestamp, so linking is a human step.

    An unattached image must still be storable and servable.
    """
    body = client.post(
        "/api/images",
        files={"file": ("DSC00417.JPG", make_jpeg(color=(3, 3, 3)), "image/jpeg")},
        headers=admin_headers,
    ).json()

    image = db.get_one(Image, body["id"])
    assert image.source_ref == "DSC00417.JPG"
    assert client.get(body["thumbnail_url"]).status_code == 200


# ---------------------------------------------------------------------------
# Listing photographs
# ---------------------------------------------------------------------------


def test_an_items_photographs_come_back_in_order(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    from app import image_links

    from tests.conftest import build_item

    item = build_item(db)
    # Attached in the opposite order from the desired sort_order, so a result
    # that merely came back in insertion (id) order -- rather than one that
    # actually obeyed ORDER BY sort_order -- would read [2, 1], not [1, 2].
    for sha, order in (("2" * 64, 2), ("1" * 64, 1)):
        image = Image(
            sha256=sha,
            storage_key=f"orig/{sha}.jpg",
            media_type="image/jpeg",
            byte_size=10,
        )
        db.add(image)
        db.flush()
        image_links.attach(
            db,
            image=image,
            item=item,
            role=None,
            is_primary=order == 1,
            sort_order=order,
        )
    db.commit()

    listed = client.get(
        f"/api/images?inventory_item_id={item.id}", headers=admin_headers
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [row["sort_order"] for row in body] == [1, 2]
    assert body[0]["is_primary"] is True
    assert body[0]["thumbnail_url"].endswith("/thumb")


def test_unattached_photographs_can_be_listed(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    from app import image_links

    from tests.conftest import build_item

    image = Image(
        sha256="3" * 64,
        storage_key="orig/3.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(image)
    db.flush()

    # A filed photograph in the same table -- present so a listing that
    # forgot to exclude linked images would return it too.
    filed = Image(
        sha256="4" * 64,
        storage_key="orig/4.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(filed)
    db.flush()
    item = build_item(db)
    image_links.attach(
        db, image=filed, item=item, role=None, is_primary=True, sort_order=0
    )
    db.commit()

    listed = client.get("/api/images?unattached=true", headers=admin_headers)
    assert listed.status_code == 200, listed.text
    assert [row["image_id"] for row in listed.json()] == [image.id]


def test_listing_every_photograph_at_once_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert client.get("/api/images", headers=admin_headers).status_code == 422


def test_listing_with_both_filters_at_once_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    from tests.conftest import build_item

    item = build_item(db)
    response = client.get(
        f"/api/images?inventory_item_id={item.id}&unattached=true",
        headers=admin_headers,
    )
    assert response.status_code == 422
