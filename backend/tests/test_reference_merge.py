"""Merging a vocabulary value into another (app.reference_merge)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from app import aliases
from app.models import (
    GradeDesignation,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ProvenanceSource,
    ReferenceAlias,
    ReferenceMerge,
    ReferenceMixin,
)
from app.seeding import _seed_reference_aliases, _seed_table
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


def _id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def _merge(
    client: TestClient,
    headers: dict[str, str],
    table: str,
    code: str,
    into: str,
    *,
    dry_run: bool = False,
) -> Response:
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
    cam = _id(db, GradeDesignation, "CAM")
    dcam = _id(db, GradeDesignation, "DCAM")
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
    """DCAM's UCAM and Ultra Cameo become CAM's."""
    response = _merge(client, admin_headers, "grade_designation", "DCAM", "CAM")
    assert response.status_code == 200, response.text
    cam = _id(db, GradeDesignation, "CAM")
    assert set(aliases.aliases_by_row(db, GradeDesignation)[cam]) == {
        "DCAM",
        "DCAM (Deep Cameo)",
        "UC",
        "UCAM",
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
    cam = _id(db, GradeDesignation, "CAM")
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
    early = _id(db, ItemAttribute, "early_releases")
    first = _id(db, ItemAttribute, "first_releases")
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

    `dry_run` runs `plan` alone, which never counted duplicates, so every
    preview said `dropped: 0` and the merge then dropped some. It is the one
    number in a confirmation dialog that cannot be checked afterwards --
    whoever approved the merge has already lost the rows it did not mention.

    The same fixture as `test_an_item_with_both_attributes_keeps_one`, so
    the two numbers are directly comparable: whatever the dry run promises
    here, that test proves the merge delivers.
    """
    early = _id(db, ItemAttribute, "early_releases")
    first = _id(db, ItemAttribute, "first_releases")
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
    item = make_item(grade_designation_id=_id(db, GradeDesignation, "CAM"))
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
    ("table", "code", "into", "reason"),
    [
        # A facts table uses it: composition names the denomination.
        ("denomination", "usd_coin_0_10", "usd_coin_0_25", "composition"),
        # A series has year ranges and nicknames of its own.
        ("series", "mercury_dime_x", "morgan_dollar", "has no value"),
        ("series", "winged_liberty_head_dime", "morgan_dollar", "series_year_range"),
        ("item_status", "missing", "received", "looks it up by its code"),
        ("grade_designation", "CAM", "CAM", "into itself"),
    ],
)
def test_a_merge_that_would_break_something_is_refused(
    client: TestClient,
    admin_headers: dict[str, str],
    table: str,
    code: str,
    into: str,
    reason: str,
) -> None:
    response = _merge(client, admin_headers, table, code, into)
    assert response.status_code in (404, 409)
    assert reason in response.json()["detail"]


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
        _id(db, GradeDesignation, "DMPL"), "alias"
    )
