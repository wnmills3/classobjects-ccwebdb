"""`app.image_enlarge`: a stored photograph replaced by its full-size picture.

No test reaches the network: the pass is handed a fetch that answers from a
table. `main` is always given the test's own `db`: left to itself it opens
the application's `SessionLocal`, which on this machine is the live database.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from app import image_enlarge, image_fetch, image_links, offering_writes
from app.config import settings
from app.image_enlarge import (
    ALREADY_HELD,
    FOR_SALE,
    NOT_FETCHED,
    NOT_LARGER,
    REPLACED,
    candidates,
    main,
    run,
)
from app.image_fetch import ImageFetchRefused, fetch_full_size
from app.image_store import ingest
from app.models import (
    DerivativeKind,
    Image,
    InventoryItem,
    ItemImage,
    ListingFormat,
    SalesVenue,
)
from app.storage import get_storage
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory

G = "https://i.ebayimg.com/images/g"


def _picture(color: str, size: tuple[int, int]) -> bytes:
    """A one-colour JPEG of this size, as a marketplace serves a photograph."""
    buffer = io.BytesIO()
    PILImage.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


SMALL = (50, 30)
LARGE = (400, 240)


def _fetcher(table: dict[str, bytes]) -> Callable[[str], bytes]:
    """A fetch that answers from `table`, refuses the rest, and keeps count."""

    def fetch(url: str) -> bytes:
        """The bytes the table holds for `url`, or a refusal."""
        fetch.asked.append(url)  # type: ignore[attr-defined]
        if url not in table:
            raise ImageFetchRefused(f"{url} answered 404")
        return table[url]

    fetch.asked = []  # type: ignore[attr-defined]
    return fetch


def _stored(db: Session, name: str, color: str, size: tuple[int, int] = SMALL) -> Image:
    """A photograph held at `size`, fetched from eBay's 500-pixel address."""
    image = ingest(db, _picture(color, size), f"{name}.jpg", f"{G}/{name}/s-l500.jpg")
    db.commit()
    return image


def _files(root: Path) -> set[str]:
    """Every file under the media folder, by its path from there."""
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def _keys(image: Image) -> set[str]:
    """The storage keys an image row names: its original and its renditions."""
    return {image.storage_key, *(d.storage_key for d in image.derivatives)}


# --------------------------------------------------------------------------
# Which photographs
# --------------------------------------------------------------------------


def test_a_picture_larger_than_its_address_names_is_not_fetched_again(
    db: Session,
) -> None:
    """Held at 501 under an address naming 500: it never was that copy."""
    at_the_size = _stored(db, "aaa", "navy", (500, 300))
    over_it = _stored(db, "bbb", "olive", (501, 300))
    tall = _stored(db, "ccc", "teal", (300, 501))
    ngc = ingest(
        db,
        _picture("gold", (2000, 2000)),
        "n.jpg",
        "https://ccg-imaging-ngc-coins-production.s3.amazonaws.com/x/TN_N1_OBV.jpg",
    )
    db.commit()

    found = [image.id for image in candidates(db)]

    # An NGC name gives no size to compare with: it is always looked at.
    assert found == [at_the_size.id, ngc.id]
    assert {over_it.id, tall.id}.isdisjoint(found)


def test_only_a_photograph_whose_address_has_a_larger_form_is_a_candidate(
    db: Session,
) -> None:
    scaled = _stored(db, "aaa", "navy")
    full = ingest(db, _picture("olive", SMALL), "b.jpg", f"{G}/bbb/s-l1600.jpg")
    elsewhere = ingest(
        db, _picture("teal", SMALL), "c.jpg", "https://example.com/c.jpg"
    )
    uploaded = ingest(db, _picture("gold", SMALL), "d.jpg")
    db.commit()

    found = [image.id for image in candidates(db)]
    assert found == [scaled.id]
    assert {full.id, elsewhere.id, uploaded.id}.isdisjoint(found)


def test_a_dry_run_counts_them_and_fetches_nothing(db: Session) -> None:
    held = _stored(db, "aaa", "navy")
    _stored(db, "bbb", "olive")
    before = (held.sha256, held.source_url)
    fetch = _fetcher({})

    report = run(db, commit=False, fetch=fetch)

    assert report.candidates == 2
    assert dict(report.hosts) == {"i.ebayimg.com": 2}
    assert fetch.asked == []  # type: ignore[attr-defined]
    assert not report.outcomes
    db.refresh(held)
    assert (held.sha256, held.source_url) == before


# --------------------------------------------------------------------------
# The replacement
# --------------------------------------------------------------------------


def test_the_full_size_picture_takes_the_place_of_the_one_held(
    db: Session, make_item: ItemFactory
) -> None:
    item = make_item()
    other = _stored(db, "zzz", "gold")
    held = _stored(db, "aaa", "navy")
    image_links.attach(
        db, image=other, item=item, role="reverse", is_primary=False, sort_order=1
    )
    link = image_links.attach(
        db, image=held, item=item, role="obverse", is_primary=True, sort_order=2
    )
    db.commit()
    was = (held.id, held.sha256, held.byte_size, link.id)
    role_id = link.image_role_id
    assert role_id is not None
    # Only this photograph's address answers: the other stays as it is.
    fetch = _fetcher({f"{G}/aaa/s-l1600.jpg": _picture("navy", LARGE)})

    report = run(db, commit=True, fetch=fetch, limit=None)

    db.expire_all()
    now = db.get_one(Image, was[0])
    # The same row, holding the larger picture and saying where it came from.
    assert (now.width, now.height) == LARGE
    assert now.sha256 != was[1]
    assert now.byte_size > was[2]
    assert now.source_url == f"{G}/aaa/s-l1600.jpg"
    assert now.source_ref == "aaa.jpg"
    assert now.media_type == "image/jpeg"
    # Still filed where it was: same link, same place, role and primary.
    kept = db.get_one(ItemImage, was[3])
    assert (kept.image_id, kept.inventory_item_id) == (now.id, item.id)
    assert (kept.sort_order, kept.is_primary, kept.image_role_id) == (
        2,
        True,
        role_id,
    )
    assert report.outcomes[REPLACED] == 1
    assert (report.bytes_before, report.bytes_after) == (was[2], now.byte_size)


def test_its_renditions_are_made_again_from_the_larger_picture(db: Session) -> None:
    held = _stored(db, "aaa", "navy")
    fetch = _fetcher({f"{G}/aaa/s-l1600.jpg": _picture("navy", (2000, 1000))})

    run(db, commit=True, fetch=fetch)

    db.expire_all()
    now = db.get_one(Image, held.id)
    sizes = {d.kind: (d.width, d.height) for d in now.derivatives}
    assert sizes == {
        DerivativeKind.thumb: (
            settings.thumbnail_max_px,
            settings.thumbnail_max_px // 2,
        ),
        DerivativeKind.web: (settings.web_max_px, settings.web_max_px // 2),
    }
    # Each rendition is named for the new content, not the old.
    assert all(now.sha256 in d.storage_key for d in now.derivatives)


def test_the_files_on_disk_are_exactly_the_ones_the_rows_name(db: Session) -> None:
    replaced = _stored(db, "aaa", "navy")
    untouched = _stored(db, "bbb", "olive")
    old_keys = _keys(replaced)
    root = Path(settings.media_root)
    assert _files(root) == old_keys | _keys(untouched)

    run(
        db,
        commit=True,
        fetch=_fetcher({f"{G}/aaa/s-l1600.jpg": _picture("navy", LARGE)}),
    )

    db.expire_all()
    now = db.get_one(Image, replaced.id)
    assert _keys(now).isdisjoint(old_keys)
    # The old files are gone, the new ones are there, the other's untouched.
    assert _files(root) == _keys(now) | _keys(db.get_one(Image, untouched.id))
    assert get_storage().get(now.storage_key)


def test_a_second_run_finds_nothing_left_to_do(db: Session) -> None:
    held = _stored(db, "aaa", "navy")
    fetch = _fetcher({f"{G}/aaa/s-l1600.jpg": _picture("navy", LARGE)})
    run(db, commit=True, fetch=fetch)

    again = run(db, commit=True, fetch=fetch)

    assert again.candidates == 0
    assert not again.outcomes
    assert fetch.asked == [f"{G}/aaa/s-l1600.jpg"]  # type: ignore[attr-defined]
    assert db.get_one(Image, held.id).source_url == f"{G}/aaa/s-l1600.jpg"


# --------------------------------------------------------------------------
# Left as they are
# --------------------------------------------------------------------------


def _unchanged(db: Session, image: Image, was: tuple[str, str | None]) -> bool:
    """Whether the row still holds the picture and address it had."""
    db.expire_all()
    now = db.get_one(Image, image.id)
    return (now.sha256, now.source_url) == was and get_storage().exists(now.storage_key)


def test_a_picture_no_larger_than_the_one_held_is_left(db: Session) -> None:
    held = _stored(db, "aaa", "navy", LARGE)
    was = (held.sha256, held.source_url)
    # A different picture of the same size: only its size keeps it out.
    fetch = _fetcher({f"{G}/aaa/s-l1600.jpg": _picture("olive", LARGE)})

    report = run(db, commit=True, fetch=fetch)

    assert _unchanged(db, held, was)
    assert dict(report.outcomes) == {NOT_LARGER: 1}
    assert report.left == [(held.id, NOT_LARGER, "400x240 against 400x240 held")]


def test_an_address_that_is_refused_leaves_the_photograph_and_goes_on(
    db: Session,
) -> None:
    refused = _stored(db, "aaa", "navy")
    was = (refused.sha256, refused.source_url)
    taken = _stored(db, "bbb", "olive")
    fetch = _fetcher({f"{G}/bbb/s-l1600.jpg": _picture("olive", LARGE)})

    report = run(db, commit=True, fetch=fetch)

    assert _unchanged(db, refused, was)
    assert dict(report.outcomes) == {NOT_FETCHED: 1, REPLACED: 1}
    assert report.left[0][:2] == (refused.id, NOT_FETCHED)
    assert "answered 404" in report.left[0][2]
    assert db.get_one(Image, taken.id).width == LARGE[0]


def test_bytes_that_are_not_a_picture_leave_the_photograph(db: Session) -> None:
    held = _stored(db, "aaa", "navy")
    was = (held.sha256, held.source_url)

    report = run(
        db, commit=True, fetch=_fetcher({f"{G}/aaa/s-l1600.jpg": b"<html>gone</html>"})
    )

    assert _unchanged(db, held, was)
    assert dict(report.outcomes) == {NOT_FETCHED: 1}


def test_a_full_size_picture_already_stored_as_another_image_is_left(
    db: Session,
) -> None:
    large = _picture("navy", LARGE)
    already = ingest(db, large, "whole.jpg", f"{G}/aaa/s-l1600.jpg")
    held = _stored(db, "aaa", "navy")
    was = (held.sha256, held.source_url)

    report = run(db, commit=True, fetch=_fetcher({f"{G}/aaa/s-l1600.jpg": large}))

    # Two rows may not hold one picture: this is for a person to settle.
    assert _unchanged(db, held, was)
    assert report.left == [(held.id, ALREADY_HELD, f"as image {already.id}")]
    assert db.get_one(Image, already.id).sha256 != was[0]


def test_a_photograph_on_an_item_for_sale_is_not_changed_under_a_buyer(
    db: Session, make_item: ItemFactory
) -> None:
    offered: InventoryItem = make_item()
    plain: InventoryItem = make_item()
    shown = _stored(db, "aaa", "navy")
    private = _stored(db, "bbb", "olive")
    image_links.attach(db, image=shown, item=offered, role=None, is_primary=True)
    image_links.attach(db, image=private, item=plain, role=None, is_primary=True)
    venue = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).one()
    offering_writes.offer(
        db,
        item=offered,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("50.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()
    was = (shown.sha256, shown.source_url)
    fetch = _fetcher(
        {
            f"{G}/aaa/s-l1600.jpg": _picture("navy", LARGE),
            f"{G}/bbb/s-l1600.jpg": _picture("olive", LARGE),
        }
    )

    report = run(db, commit=True, fetch=fetch)

    assert _unchanged(db, shown, was)
    assert fetch.asked == [f"{G}/bbb/s-l1600.jpg"]  # type: ignore[attr-defined]
    assert report.left == [(shown.id, FOR_SALE, offered.item_code)]
    assert db.get_one(Image, private.id).width == LARGE[0]


def test_a_limit_stops_after_that_many_and_leaves_the_rest_for_later(
    db: Session,
) -> None:
    first = _stored(db, "aaa", "navy")
    second = _stored(db, "bbb", "olive")
    fetch = _fetcher(
        {
            f"{G}/aaa/s-l1600.jpg": _picture("navy", LARGE),
            f"{G}/bbb/s-l1600.jpg": _picture("olive", LARGE),
        }
    )

    report = run(db, commit=True, fetch=fetch, limit=1)

    assert report.candidates == 2
    assert dict(report.outcomes) == {REPLACED: 1}
    assert db.get_one(Image, first.id).width == LARGE[0]
    assert db.get_one(Image, second.id).width == SMALL[0]


def test_more_than_a_batch_is_all_replaced(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(image_enlarge, "BATCH", 2)
    colors = ["navy", "olive", "teal", "gold", "maroon"]
    held = [_stored(db, f"p{n}", color) for n, color in enumerate(colors)]
    table = {
        f"{G}/p{n}/s-l1600.jpg": _picture(color, LARGE)
        for n, color in enumerate(colors)
    }

    commits: list[int] = []
    commit = db.commit

    def counted() -> None:
        """Commit as usual, noting how many photographs were replaced by then."""
        commits.append(
            sum(1 for image in held if db.get_one(Image, image.id).width == LARGE[0])
        )
        commit()

    monkeypatch.setattr(db, "commit", counted)

    report = run(db, commit=True, fetch=_fetcher(table))

    # A commit after every two, and one for the last: a run cut short keeps
    # what it had done.
    assert commits == [2, 4, 5]
    db.expire_all()
    assert report.outcomes[REPLACED] == 5
    assert [db.get_one(Image, image.id).width for image in held] == [LARGE[0]] * 5
    root = Path(settings.media_root)
    assert _files(root) == set().union(*(_keys(db.get_one(Image, i.id)) for i in held))


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


def test_the_command_reports_without_writing_unless_told_to_commit(
    db: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    held = _stored(db, "aaa", "navy")
    was = (held.sha256, held.source_url)
    monkeypatch.setattr(
        image_enlarge,
        "run",
        lambda session, **kw: run(session, **{**kw, "fetch": _fetcher({}), "pause": 0}),
    )

    assert main([], db=db) == 0

    printed = capsys.readouterr().out
    assert "photographs that may have a larger picture: 1" in printed
    assert "dry run: nothing fetched or written" in printed
    assert _unchanged(db, held, was)


def test_the_command_names_what_it_left_when_asked_to_list(
    db: Session, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    held = _stored(db, "aaa", "navy")
    monkeypatch.setattr(
        image_enlarge,
        "run",
        lambda session, **kw: run(session, **{**kw, "fetch": _fetcher({}), "pause": 0}),
    )

    assert main(["--commit", "--list"], db=db) == 0

    printed = capsys.readouterr().out
    assert "not fetched: 1" in printed
    assert f"image {held.id}: not fetched -- {G}/aaa/s-l1600.jpg" in printed


# --------------------------------------------------------------------------
# Adding a photograph by its address fetches the full size
# --------------------------------------------------------------------------


def test_a_pasted_thumbnail_address_brings_back_the_full_picture() -> None:
    large = _picture("navy", LARGE)
    fetch = _fetcher({f"{G}/aaa/s-l1600.webp": large, f"{G}/aaa/s-l140.webp": b"small"})

    got, came_from = fetch_full_size(f"{G}/aaa/s-l140.webp", fetch=fetch)

    assert (got, came_from) == (large, f"{G}/aaa/s-l1600.webp")
    assert fetch.asked == [f"{G}/aaa/s-l1600.webp"]  # type: ignore[attr-defined]


def test_the_address_as_given_is_fetched_when_the_full_size_is_refused() -> None:
    small = _picture("navy", SMALL)
    fetch = _fetcher({f"{G}/aaa/s-l140.webp": small})

    got, came_from = fetch_full_size(f"{G}/aaa/s-l140.webp", fetch=fetch)

    assert (got, came_from) == (small, f"{G}/aaa/s-l140.webp")
    assert fetch.asked == [  # type: ignore[attr-defined]
        f"{G}/aaa/s-l1600.webp",
        f"{G}/aaa/s-l140.webp",
    ]


def test_an_address_with_no_larger_form_is_fetched_once_and_its_refusal_raised() -> (
    None
):
    fetch = _fetcher({})
    with pytest.raises(ImageFetchRefused, match=r"example\.com/a\.jpg answered 404"):
        fetch_full_size("https://example.com/a.jpg", fetch=fetch)
    assert fetch.asked == ["https://example.com/a.jpg"]  # type: ignore[attr-defined]


def test_both_refused_raises_the_refusal_of_the_address_as_given() -> None:
    with pytest.raises(ImageFetchRefused, match=r"s-l140\.webp answered 404"):
        fetch_full_size(f"{G}/aaa/s-l140.webp", fetch=_fetcher({}))


def test_the_console_s_add_by_address_stores_the_full_size_and_says_so(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    table = {
        f"{G}/aaa/s-l1600.webp": _picture("navy", LARGE),
        f"{G}/aaa/s-l140.webp": _picture("navy", SMALL),
    }
    monkeypatch.setattr(image_fetch, "fetch_image", _fetcher(table))
    item = make_item()

    response = client.post(
        "/api/images/from-url",
        json={"url": f"{G}/aaa/s-l140.webp", "inventory_item_id": item.id},
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    assert response.json()["source_url"] == f"{G}/aaa/s-l1600.webp"
    stored = db.scalars(
        select(Image)
        .join(ItemImage, ItemImage.image_id == Image.id)
        .where(ItemImage.inventory_item_id == item.id)
    ).one()
    assert (stored.width, stored.height) == LARGE
