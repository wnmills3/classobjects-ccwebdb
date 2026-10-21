"""Merging a vocabulary value into another (app.reference_merge)."""

from __future__ import annotations

import pytest
from app import aliases, image_links
from app.models import (
    CoinDetail,
    CurrencyDetail,
    ErrorType,
    GradeDesignation,
    GradingService,
    Image,
    ImageRole,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ItemCertification,
    ItemError,
    ItemFieldChange,
    ItemImage,
    ItemKind,
    Mint,
    ProvenanceSource,
    ReferenceAlias,
    ReferenceMerge,
    SealColor,
    Series,
    SeriesAlias,
    SeriesYearRange,
)
from app.models.base import utcnow
from app.seeding import (
    _seed_reference_aliases,
    _seed_series_year_ranges,
    _seed_table,
)
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, build_bare_item, code_id


def _merge(
    client: TestClient,
    headers: dict[str, str],
    table: str,
    code: str,
    into: str,
    *,
    dry_run: bool = False,
) -> Response:
    """The response to merging one vocabulary value into another."""
    return client.post(
        f"/api/reference/{table}/{code}/merge",
        json={"into": into, "dry_run": dry_run},
        headers=headers,
    )


def test_a_merge_moves_items_keeps_the_names_and_removes_the_value(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    cam = code_id(db, GradeDesignation, "CAM")
    dcam = code_id(db, GradeDesignation, "DCAM")
    moved = [make_item(grade_designation_id=cam) for _ in range(2)]
    kept = make_item(grade_designation_id=dcam)
    versions = {item.id: item.version for item in moved}

    response = _merge(client, admin_headers, "grade_designation", "CAM", "DCAM")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["moved"] == {"inventory_item.grade_designation_id": 2}
    assert body["items"] == 2
    assert body["aliases"] == ["CAM (Cameo)", "CAM"]

    db.expire_all()
    for item in moved:
        refreshed = db.get_one(InventoryItem, item.id)
        assert refreshed.grade_designation_id == dcam
        assert refreshed.version == versions[item.id] + 1
    assert db.get_one(InventoryItem, kept.id).grade_designation_id == dcam
    assert db.get(GradeDesignation, cam) is None
    record = db.scalar(select(ReferenceMerge))
    assert record is not None
    assert (record.table_name, record.code, record.merged_into) == (
        "grade_designation",
        "CAM",
        "DCAM",
    )
    # The old word still names the kept value.
    assert aliases.resolve(db, GradeDesignation, "cam") == aliases.Resolved(
        dcam, "alias"
    )


def test_the_old_values_aliases_move_with_it(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """UCAM's Ultra Cameo and UC become CAM's, with its code and label."""
    response = _merge(client, admin_headers, "grade_designation", "UCAM", "CAM")
    assert response.status_code == 200, response.text
    cam = code_id(db, GradeDesignation, "CAM")
    assert set(aliases.aliases_by_row(db, GradeDesignation)[cam]) == {
        "UCAM",
        "UCAM (Ultra Cameo)",
        "UC",
        "Ultra Cameo",
    }
    # None left pointing at a row that is gone.
    assert (
        db.scalar(
            select(ReferenceAlias).where(
                ReferenceAlias.table_name == "grade_designation",
                ReferenceAlias.row_id.not_in(select(GradeDesignation.id)),
            )
        )
        is None
    )


def test_a_dry_run_says_what_would_move_and_changes_nothing(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    cam = code_id(db, GradeDesignation, "CAM")
    item = make_item(grade_designation_id=cam)

    response = _merge(
        client, admin_headers, "grade_designation", "CAM", "DCAM", dry_run=True
    )

    assert response.json()["dry_run"] is True
    assert response.json()["items"] == 1
    db.expire_all()
    assert db.get_one(InventoryItem, item.id).grade_designation_id == cam
    assert db.scalar(select(ReferenceMerge)) is None


def test_an_item_with_both_attributes_keeps_one(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    early = code_id(db, ItemAttribute, "early_releases")
    first = code_id(db, ItemAttribute, "first_releases")
    both = make_item()
    only_early = make_item()
    for item, attribute in ((both, early), (both, first), (only_early, early)):
        db.add(
            ItemAttributeLink(
                inventory_item_id=item.id,
                item_attribute_id=attribute,
                source=ProvenanceSource.manual,
            )
        )
    db.commit()

    response = _merge(
        client, admin_headers, "item_attribute", "early_releases", "first_releases"
    )

    assert response.status_code == 200, response.text
    assert (response.json()["items"], response.json()["dropped"]) == (2, 1)
    links = db.execute(
        select(ItemAttributeLink.inventory_item_id, ItemAttributeLink.item_attribute_id)
    ).all()
    assert sorted(links) == sorted([(both.id, first), (only_early.id, first)])


def test_a_dry_run_reports_the_rows_it_would_drop(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """The preview's `dropped` must mean what the merge will do.

    `dry_run` runs `plan` alone, so `plan` has to count the duplicates the
    merge will drop. It is the one
    number in a confirmation dialog that cannot be checked afterwards --
    whoever approved the merge has already lost the rows it did not mention.

    The same fixture as `test_an_item_with_both_attributes_keeps_one`, so
    the two numbers are directly comparable: whatever the dry run promises
    here, that test proves the merge delivers.
    """
    early = code_id(db, ItemAttribute, "early_releases")
    first = code_id(db, ItemAttribute, "first_releases")
    both = make_item()
    only_early = make_item()
    for item, attribute in ((both, early), (both, first), (only_early, early)):
        db.add(
            ItemAttributeLink(
                inventory_item_id=item.id,
                item_attribute_id=attribute,
                source=ProvenanceSource.manual,
            )
        )
    db.commit()

    response = _merge(
        client,
        admin_headers,
        "item_attribute",
        "early_releases",
        "first_releases",
        dry_run=True,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["items"], body["dropped"]) == (2, 1)

    # And it really was a preview: nothing moved.
    db.expire_all()
    links = db.execute(
        select(ItemAttributeLink.inventory_item_id, ItemAttributeLink.item_attribute_id)
    ).all()
    assert sorted(links) == sorted(
        [(both.id, early), (both.id, first), (only_early.id, early)]
    )


def test_a_form_opened_before_the_merge_is_refused(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    item = make_item(grade_designation_id=code_id(db, GradeDesignation, "CAM"))
    version = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()[
        "version"
    ]
    _merge(client, admin_headers, "grade_designation", "CAM", "DCAM")
    stale = client.patch(
        f"/api/inventory/{item.id}",
        json={"description": "edited", "version": version},
        headers=admin_headers,
    )
    assert stale.status_code == 409


@pytest.mark.parametrize(
    ("table", "code", "into", "status", "reason"),
    [
        # A facts table uses it: composition names the denomination.
        ("denomination", "usd_coin_0_10", "usd_coin_0_25", 409, "composition"),
        # A code that names no value is a 404.
        ("series", "mercury_dime_x", "morgan_dollar", 404, "has no value"),
        # A series has year ranges of its own, which are facts.
        (
            "series",
            "winged_liberty_head_dime",
            "morgan_dollar",
            409,
            "series_year_range",
        ),
        ("item_status", "missing", "received", 409, "looks it up by its code"),
        ("grade_designation", "CAM", "CAM", 409, "into itself"),
    ],
)
def test_a_merge_that_would_break_something_is_refused(
    client: TestClient,
    admin_headers: dict[str, str],
    table: str,
    code: str,
    into: str,
    status: int,
    reason: str,
) -> None:
    response = _merge(client, admin_headers, table, code, into)
    assert response.status_code == status
    assert reason in response.json()["detail"]


def test_a_series_refused_for_its_year_ranges_is_not_refused_for_its_nicknames(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A nickname belongs to its series and goes with it; a year range is a fact."""
    response = _merge(
        client, admin_headers, "series", "winged_liberty_head_dime", "morgan_dollar"
    )
    assert response.status_code == 409
    assert "series_alias" not in response.json()["detail"]


# ---------------------------------------------------------------------------
# Every table that describes an item moves
# ---------------------------------------------------------------------------


def _added(
    client: TestClient,
    headers: dict[str, str],
    table: str,
    label: str,
    **body: object,
) -> str:
    """Add a value to a vocabulary and return its code."""
    made = client.post(
        f"/api/reference/{table}", json={"label": label, **body}, headers=headers
    )
    assert made.status_code == 201, made.text
    return str(made.json()["code"])


def test_a_coins_mint_moves_and_its_version_with_it(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    carson = _added(client, admin_headers, "mint", "Carson Test", extra={"mark": "CT"})
    item = build_bare_item(db)
    db.add(CoinDetail(inventory_item_id=item.id, mint_id=code_id(db, Mint, carson)))
    db.commit()
    version = item.version

    response = _merge(client, admin_headers, "mint", carson, "CC")

    assert response.status_code == 200, response.text
    assert response.json()["moved"] == {"coin_detail.mint_id": 1}
    assert response.json()["items"] == 1
    db.expire_all()
    assert db.get_one(CoinDetail, item.id).mint_id == code_id(db, Mint, "CC")
    assert db.get_one(InventoryItem, item.id).version == version + 1


def test_a_merge_locks_its_items_before_it_writes_their_records(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """An item edit writes the item, then its records: a merge must not invert that."""
    carson = _added(client, admin_headers, "mint", "Carson Lock", extra={"mark": "CL"})
    item = build_bare_item(db)
    db.add(CoinDetail(inventory_item_id=item.id, mint_id=code_id(db, Mint, carson)))
    db.commit()
    sent: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_: object) -> None:
        """Keep each statement, in order."""
        sent.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", record)
    try:
        response = _merge(client, admin_headers, "mint", carson, "CC")
    finally:
        event.remove(connection, "before_cursor_execute", record)

    assert response.status_code == 200, response.text
    locks = [
        i
        for i, s in enumerate(sent)
        if "FOR UPDATE" in s and "FROM inventory_item" in s
    ]
    writes = [i for i, s in enumerate(sent) if s.startswith("UPDATE coin_detail")]
    assert locks, sent
    assert writes, sent
    assert locks[0] < writes[0]


def test_a_notes_seal_moves(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    teal = _added(client, admin_headers, "seal_color", "Teal Test")
    note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))
    db.add(
        CurrencyDetail(
            inventory_item_id=note.id, seal_color_id=code_id(db, SealColor, teal)
        )
    )
    db.commit()

    response = _merge(client, admin_headers, "seal_color", teal, "blue")

    assert response.status_code == 200, response.text
    assert response.json()["moved"] == {"currency_detail.seal_color_id": 1}
    db.expire_all()
    assert db.get_one(CurrencyDetail, note.id).seal_color_id == code_id(
        db, SealColor, "blue"
    )


def test_a_grading_service_moves_on_the_item_and_on_its_certificates(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    house = _added(client, admin_headers, "grading_service", "House Test")
    house_id = code_id(db, GradingService, house)
    item = build_bare_item(db, grading_service_id=house_id)
    db.add(
        ItemCertification(
            inventory_item_id=item.id, grading_service_id=house_id, cert_number="T-1"
        )
    )
    db.commit()

    response = _merge(client, admin_headers, "grading_service", house, "PCGS")

    assert response.status_code == 200, response.text
    assert response.json()["moved"] == {
        "inventory_item.grading_service_id": 1,
        "item_certification.grading_service_id": 1,
    }
    # One item, though two of its rows moved.
    assert response.json()["items"] == 1
    pcgs = code_id(db, GradingService, "PCGS")
    db.expire_all()
    assert db.get_one(InventoryItem, item.id).grading_service_id == pcgs
    certificate = db.scalars(
        select(ItemCertification).where(ItemCertification.inventory_item_id == item.id)
    ).one()
    assert certificate.grading_service_id == pcgs


def test_a_photographs_role_moves(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    role = _added(client, admin_headers, "image_role", "Toning Test")
    item = build_bare_item(db)
    image = Image(
        sha256="7" * 64,
        storage_key="orig/merge-test.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(image)
    db.flush()
    db.add(
        ItemImage(
            inventory_item_id=item.id,
            image_id=image.id,
            image_role_id=code_id(db, ImageRole, role),
        )
    )
    db.commit()

    response = _merge(client, admin_headers, "image_role", role, "detail")

    assert response.status_code == 200, response.text
    assert response.json()["moved"] == {"item_image.image_role_id": 1}
    assert response.json()["items"] == 1
    db.expire_all()
    link = db.scalars(select(ItemImage).where(ItemImage.image_id == image.id)).one()
    assert link.image_role_id == code_id(db, ImageRole, "detail")


def test_a_photographs_role_is_changed_by_the_one_writer_of_item_image(
    db: Session,
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`app.image_links` is the only writer of `item_image`, a merge included."""
    role = _added(client, admin_headers, "image_role", "Writer Test")
    item = build_bare_item(db)
    image = Image(
        sha256="8" * 64,
        storage_key="orig/merge-writer-test.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(image)
    db.flush()
    role_id = code_id(db, ImageRole, role)
    detail_id = code_id(db, ImageRole, "detail")
    db.add(
        ItemImage(inventory_item_id=item.id, image_id=image.id, image_role_id=role_id)
    )
    db.commit()
    calls: list[tuple[int, int]] = []
    real = image_links.replace_role

    def spy(session: Session, old_role_id: int, new_role_id: int) -> None:
        """Note the call, then make it."""
        calls.append((old_role_id, new_role_id))
        real(session, old_role_id, new_role_id)

    monkeypatch.setattr(image_links, "replace_role", spy)

    response = _merge(client, admin_headers, "image_role", role, "detail")

    assert response.status_code == 200, response.text
    assert calls == [(role_id, detail_id)]
    db.expire_all()
    link = db.scalars(select(ItemImage).where(ItemImage.image_id == image.id)).one()
    assert link.image_role_id == detail_id


def test_an_item_with_both_errors_keeps_one(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """One error type per item: the row that would repeat is dropped, not moved.

    Its details are not lost with it: they follow the kept error's.
    """
    slip = _added(
        client,
        admin_headers,
        "error_type",
        "Off Centre Test",
        extra={"applies_to": "coin"},
    )
    slip_id = code_id(db, ErrorType, slip)
    off_center = code_id(db, ErrorType, "off_center")
    both = build_bare_item(db)
    only_slip = build_bare_item(db)
    db.add_all(
        [
            ItemError(inventory_item_id=both.id, error_type_id=slip_id, details="a"),
            ItemError(inventory_item_id=both.id, error_type_id=off_center, details="b"),
            ItemError(inventory_item_id=only_slip.id, error_type_id=slip_id),
        ]
    )
    db.commit()

    response = _merge(client, admin_headers, "error_type", slip, "off_center")

    assert response.status_code == 200, response.text
    assert (response.json()["items"], response.json()["dropped"]) == (2, 1)
    rows = db.execute(
        select(ItemError.inventory_item_id, ItemError.error_type_id, ItemError.details)
    ).all()
    assert sorted(rows, key=lambda row: row[0]) == sorted(
        [(both.id, off_center, "b; a"), (only_slip.id, off_center, None)],
        key=lambda row: row[0],
    )


def test_a_dropped_errors_details_are_added_only_when_they_say_something_new(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The same words, or none, leave the kept error's details as they were."""
    slip = _added(
        client,
        admin_headers,
        "error_type",
        "Off Centre Same Test",
        extra={"applies_to": "coin"},
    )
    slip_id = code_id(db, ErrorType, slip)
    off_center = code_id(db, ErrorType, "off_center")
    same = build_bare_item(db)
    blank = build_bare_item(db)
    unnoted = build_bare_item(db)
    db.add_all(
        [
            ItemError(inventory_item_id=same.id, error_type_id=slip_id, details="10%"),
            ItemError(
                inventory_item_id=same.id, error_type_id=off_center, details="10%"
            ),
            ItemError(inventory_item_id=blank.id, error_type_id=slip_id, details=" "),
            ItemError(
                inventory_item_id=blank.id, error_type_id=off_center, details="kept"
            ),
            ItemError(inventory_item_id=unnoted.id, error_type_id=slip_id, details="x"),
            ItemError(inventory_item_id=unnoted.id, error_type_id=off_center),
        ]
    )
    db.commit()

    response = _merge(client, admin_headers, "error_type", slip, "off_center")

    assert response.status_code == 200, response.text
    assert response.json()["dropped"] == 3
    db.expire_all()
    rows = dict(
        db.execute(
            select(ItemError.inventory_item_id, ItemError.details).where(
                ItemError.inventory_item_id.in_([same.id, blank.id, unnoted.id])
            )
        )
        .tuples()
        .all()
    )
    assert rows == {same.id: "10%", blank.id: "kept", unnoted.id: "x"}


def _link(
    db: Session, item: InventoryItem, attribute: int, *, removed: bool = False
) -> None:
    """Give the item this attribute, in force or removed by a person."""
    db.add(
        ItemAttributeLink(
            inventory_item_id=item.id,
            item_attribute_id=attribute,
            source=ProvenanceSource.manual,
            removed_at=utcnow() if removed else None,
        )
    )


def test_a_removed_kept_attribute_gives_way_to_the_merged_one_in_force(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """An item had the kept attribute removed and holds the merged one.

    It holds the attribute, so it keeps holding it: the removed link is not
    "already has the kept value", and dropping the link in force for it
    would take the attribute away.
    """
    early = code_id(db, ItemAttribute, "early_releases")
    first = code_id(db, ItemAttribute, "first_releases")
    item = make_item()
    _link(db, item, early)
    _link(db, item, first, removed=True)
    db.commit()

    preview = _merge(
        client,
        admin_headers,
        "item_attribute",
        "early_releases",
        "first_releases",
        dry_run=True,
    )
    response = _merge(
        client, admin_headers, "item_attribute", "early_releases", "first_releases"
    )

    assert preview.status_code == 200, preview.text
    assert response.status_code == 200, response.text
    assert preview.json()["dropped"] == response.json()["dropped"] == 0
    db.expire_all()
    links = db.execute(
        select(ItemAttributeLink.item_attribute_id, ItemAttributeLink.removed_at).where(
            ItemAttributeLink.inventory_item_id == item.id
        )
    ).all()
    assert [tuple(link) for link in links] == [(first, None)]


def test_a_removed_merged_attribute_is_dropped_for_a_kept_one_in_force(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """The other way round: the kept link in force stays, and is what is counted."""
    early = code_id(db, ItemAttribute, "early_releases")
    first = code_id(db, ItemAttribute, "first_releases")
    item = make_item()
    _link(db, item, early, removed=True)
    _link(db, item, first)
    db.commit()

    response = _merge(
        client, admin_headers, "item_attribute", "early_releases", "first_releases"
    )

    assert response.status_code == 200, response.text
    assert response.json()["dropped"] == 1
    db.expire_all()
    links = db.execute(
        select(ItemAttributeLink.item_attribute_id, ItemAttributeLink.removed_at).where(
            ItemAttributeLink.inventory_item_id == item.id
        )
    ).all()
    assert [tuple(link) for link in links] == [(first, None)]


def test_both_attributes_removed_merge_without_a_conflict(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """One row per item and attribute: two removed links leave one, still removed."""
    early = code_id(db, ItemAttribute, "early_releases")
    first = code_id(db, ItemAttribute, "first_releases")
    item = make_item()
    _link(db, item, early, removed=True)
    _link(db, item, first, removed=True)
    db.commit()

    response = _merge(
        client, admin_headers, "item_attribute", "early_releases", "first_releases"
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    links = db.execute(
        select(ItemAttributeLink.item_attribute_id, ItemAttributeLink.removed_at).where(
            ItemAttributeLink.inventory_item_id == item.id
        )
    ).all()
    assert len(links) == 1
    assert links[0][0] == first
    assert links[0][1] is not None


def test_a_series_with_nicknames_and_no_year_ranges_merges(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """Its nicknames go with it and come back as the kept design's."""
    design = _added(client, admin_headers, "series", "Cartwheel Test Design")
    named = client.post(
        f"/api/reference/series/{design}/aliases",
        json={"alias": "Wagon Wheel Test"},
        headers=admin_headers,
    )
    assert named.status_code == 201, named.text
    design_id = code_id(db, Series, design)
    item = make_item(series_id=design_id)

    response = _merge(client, admin_headers, "series", design, "morgan_dollar")

    assert response.status_code == 200, response.text
    assert response.json()["moved"] == {"inventory_item.series_id": 1}
    morgan = code_id(db, Series, "morgan_dollar")
    db.expire_all()
    assert db.get_one(InventoryItem, item.id).series_id == morgan
    assert db.get(Series, design_id) is None
    assert {"Cartwheel Test Design", "Wagon Wheel Test"} <= set(
        aliases.aliases_by_row(db, Series)[morgan]
    )
    assert (
        db.scalars(select(SeriesAlias).where(SeriesAlias.series_id == design_id)).all()
        == []
    )


# ---------------------------------------------------------------------------
# What the preview says is what the merge does
# ---------------------------------------------------------------------------


def test_a_name_too_long_to_be_an_alias_is_said_not_silently_lost(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The preview must not promise a name the merge cannot keep.

    An alias holds 64 characters and a label 255, so a long label cannot
    become the kept value's alias. The preview and the merge name the same
    names, and the one that is lost is listed with them.
    """
    long_label = "Deep Cameo With An Unusually Long Descriptive Label " + "x" * 20
    assert len(long_label) > 64
    code = _added(
        client, admin_headers, "grade_designation", long_label, code="LONGTEST"
    )

    preview = _merge(
        client, admin_headers, "grade_designation", code, "DCAM", dry_run=True
    )
    made = _merge(client, admin_headers, "grade_designation", code, "DCAM")

    assert preview.status_code == 200, preview.text
    assert made.status_code == 200, made.text
    assert preview.json()["aliases"] == ["LONGTEST"]
    assert made.json()["aliases"] == preview.json()["aliases"]
    for body in (preview.json(), made.json()):
        assert len(body["names_not_kept"]) == 1
        assert long_label in body["names_not_kept"][0]


def test_a_name_that_differs_only_in_case_is_promised_once(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A label and a code that read the same are one name, kept once.

    The merge keeps the first and finds the second already held; the
    preview must say the same.
    """
    code = _added(client, admin_headers, "grade_designation", "Toned", code="toned")

    preview = _merge(
        client, admin_headers, "grade_designation", code, "DCAM", dry_run=True
    )
    made = _merge(client, admin_headers, "grade_designation", code, "DCAM")

    assert preview.status_code == 200, preview.text
    assert made.status_code == 200, made.text
    assert preview.json()["aliases"] == ["Toned"]
    assert made.json()["aliases"] == preview.json()["aliases"]


def test_a_name_that_is_another_values_own_is_not_promised_either(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """`CAM` is a value's own code: it can never be DCAM's alias."""
    code = _added(client, admin_headers, "grade_designation", "cam", code="CAMDUPTEST")

    preview = _merge(
        client, admin_headers, "grade_designation", code, "DCAM", dry_run=True
    )

    assert preview.status_code == 200, preview.text
    assert preview.json()["aliases"] == ["CAMDUPTEST"]
    assert len(preview.json()["names_not_kept"]) == 1
    assert "cam" in preview.json()["names_not_kept"][0]


# ---------------------------------------------------------------------------
# A merged code stays merged
# ---------------------------------------------------------------------------


def test_a_merged_code_cannot_be_added_again(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """`reference_merge` holds a code once, and a seed load skips it for good.

    Added again, the value could not be merged a second time and no seed
    load would ever update it, so the add is refused, naming what the code
    became.
    """
    assert (
        _merge(client, admin_headers, "grade_designation", "PL", "DMPL").status_code
        == 200
    )

    again = client.post(
        "/api/reference/grade_designation",
        json={"code": "PL", "label": "Prooflike again"},
        headers=admin_headers,
    )

    assert again.status_code == 409, again.text
    assert "merged into" in again.json()["detail"]
    assert "DMPL" in again.json()["detail"]
    assert (
        db.scalar(select(GradeDesignation).where(GradeDesignation.code == "PL")) is None
    )


def test_a_seed_alias_follows_a_code_merged_twice_over(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """PL became DMPL and DMPL became DCAM: a seed row naming PL names DCAM."""
    for code, into in (("PL", "DMPL"), ("DMPL", "DCAM")):
        assert (
            _merge(client, admin_headers, "grade_designation", code, into).status_code
            == 200
        )

    alias = [{"table": "grade_designation", "code": "PL", "alias": "Chain Test"}]
    assert _seed_reference_aliases(db, {"reference_alias": alias}) == {"created": 1}
    assert aliases.resolve(db, GradeDesignation, "chain test") == aliases.Resolved(
        code_id(db, GradeDesignation, "DCAM"), "alias"
    )


def test_a_seed_range_naming_a_merged_design_lands_on_the_one_it_became(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A design's year ranges follow a merge, as its other seed rows do."""
    assert (
        _merge(
            client, admin_headers, "series", "gobrecht_dollar", "flowing_hair_dollar"
        ).status_code
        == 200
    )

    shipped = [{"series": "gobrecht_dollar", "year_start": 1836, "year_end": 1839}]
    assert _seed_series_year_ranges(db, {"series_year_range": shipped}) == {
        "created": 1
    }
    kept = code_id(db, Series, "flowing_hair_dollar")
    ranges = db.scalars(
        select(SeriesYearRange).where(SeriesYearRange.series_id == kept)
    ).all()
    assert [(r.year_start, r.year_end) for r in ranges] == [(1836, 1839)]


def test_a_second_merge_of_the_same_value_is_a_409_not_a_500(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Two merges at once: the second meets the first's record and is refused.

    Inserted directly, as the first request's commit would leave it, while
    the value is still there for the second request to find.
    """
    db.add(
        ReferenceMerge(
            table_name="grade_designation",
            code="CAM",
            label="CAM (Cameo)",
            merged_into="DCAM",
        )
    )
    db.commit()

    response = _merge(client, admin_headers, "grade_designation", "CAM", "DCAM")

    assert response.status_code == 409, response.text
    assert code_id(db, GradeDesignation, "CAM") is not None


def test_a_merge_is_in_each_moved_items_history(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    """Who moved a field, and from what to what, as an edit would log it."""
    item = make_item(grade_designation_id=code_id(db, GradeDesignation, "CAM"))
    bystander = make_item(grade_designation_id=code_id(db, GradeDesignation, "DCAM"))

    response = _merge(client, admin_headers, "grade_designation", "CAM", "DCAM")

    assert response.status_code == 200, response.text
    changes = db.execute(
        select(
            ItemFieldChange.inventory_item_id,
            ItemFieldChange.field_name,
            ItemFieldChange.old_value,
            ItemFieldChange.new_value,
            ItemFieldChange.changed_by_id,
        ).where(
            ItemFieldChange.inventory_item_id.in_([item.id, bystander.id]),
            ItemFieldChange.field_name == "grade_designation",
        )
    ).all()
    assert len(changes) == 1
    item_id, field, old, new, by = changes[0]
    assert (item_id, field, old, new) == (item.id, "grade_designation", "CAM", "DCAM")
    assert by is not None


def test_a_coins_mint_merge_is_in_its_history(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    carson = _added(client, admin_headers, "mint", "Carson Log", extra={"mark": "CG"})
    item = build_bare_item(db)
    db.add(CoinDetail(inventory_item_id=item.id, mint_id=code_id(db, Mint, carson)))
    db.commit()

    response = _merge(client, admin_headers, "mint", carson, "CC")

    assert response.status_code == 200, response.text
    logged = db.execute(
        select(ItemFieldChange.old_value, ItemFieldChange.new_value).where(
            ItemFieldChange.inventory_item_id == item.id,
            ItemFieldChange.field_name == "mint",
        )
    ).all()
    assert [tuple(row) for row in logged] == [(carson, "CC")]


def test_a_retired_value_is_not_a_merge_target(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.patch(
        "/api/reference/grade_designation/DCAM",
        json={"label": "DCAM (Deep Cameo)", "is_active": False},
        headers=admin_headers,
    )
    response = _merge(client, admin_headers, "grade_designation", "CAM", "DCAM")
    assert response.status_code == 409
    assert "restore it first" in response.json()["detail"]


def test_an_unknown_value_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert (
        _merge(client, admin_headers, "grade_designation", "NOPE", "DCAM").status_code
        == 404
    )
    assert _merge(client, admin_headers, "no_such_table", "a", "b").status_code == 404


def test_an_unknown_target_is_a_422(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The value merged into is named in the body: a bad field, not a missing row."""
    response = _merge(client, admin_headers, "grade_designation", "CAM", "NOPE")
    assert response.status_code == 422, response.text
    assert "NOPE" in response.json()["detail"]


def test_only_staff_merge(client: TestClient, customer_headers: dict[str, str]) -> None:
    url = "/api/reference/grade_designation/CAM/merge"
    assert client.post(url, json={"into": "DCAM"}).status_code == 401
    assert (
        client.post(url, json={"into": "DCAM"}, headers=customer_headers).status_code
        == 403
    )


def test_a_merged_value_stays_merged_through_a_seed_load(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert (
        _merge(client, admin_headers, "grade_designation", "PL", "DMPL").status_code
        == 200
    )
    shipped = [{"code": "PL", "label": "PL (Prooflike)", "sort_order": 100}]

    assert _seed_table(db, GradeDesignation, shipped) == {"merged": 1}
    assert (
        db.scalar(select(GradeDesignation).where(GradeDesignation.code == "PL")) is None
    )

    # A seed alias naming the merged code lands on the value it became.
    alias = [{"table": "grade_designation", "code": "PL", "alias": "Proof-like"}]
    assert _seed_reference_aliases(db, {"reference_alias": alias}) == {"created": 1}
    assert aliases.resolve(db, GradeDesignation, "proof-like") == aliases.Resolved(
        code_id(db, GradeDesignation, "DMPL"), "alias"
    )
