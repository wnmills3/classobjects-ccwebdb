"""The description the item editor's Suggest button offers (`app.item_descriptions`).

In the owner's style (2026-09-24): grade, designation and attributes first,
then year, face value and serial, then the note type and seal -- no field
labels, no district, signatures or grading service.
"""

from __future__ import annotations

from decimal import Decimal

from app.item_descriptions import suggested_description
from app.models import (
    CurrencyDetail,
    Denomination,
    ErrorType,
    FedDistrict,
    GradeDesignation,
    GradingService,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ItemError,
    Metal,
    NoteType,
    ReferenceMixin,
    SealColor,
    Series,
    SignatureCombination,
)
from app.models.reference import Grade
from app.offer_titles import suggested_title
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item


def _id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def _label(db: Session, model: type[ReferenceMixin], code: str) -> str:
    return db.execute(select(model.label).where(model.code == code)).scalar_one()


def _short(grade_label: str) -> str:
    return grade_label.replace("Uncirculated", "Unc")


def _note(db: Session, **overrides: object) -> InventoryItem:
    fields: dict[str, object] = {
        "kind": "currency",
        "denomination_id": _id(db, Denomination, "usd_note_1"),
        "grade_id": _id(db, Grade, "N67"),
        "grade_designation_id": _id(db, GradeDesignation, "EPQ"),
        "grading_service_id": _id(db, GradingService, "PMG"),
        "year_start": None,
    }
    item = build_item(db, **{**fields, **overrides})
    signers = db.scalars(select(SignatureCombination)).first()
    assert signers is not None
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id,
            note_type_id=_id(db, NoteType, "frn"),
            seal_color_id=_id(db, SealColor, "green"),
            fed_district_id=_id(db, FedDistrict, "F"),
            signature_combination_id=signers.id,
            series_year=1999,
            serial_number="F06566560R",
        )
    )
    db.flush()
    db.refresh(item)
    return item


def _link(db: Session, item: InventoryItem, code: str) -> str:
    attribute = db.scalars(
        select(ItemAttribute).where(ItemAttribute.code == code)
    ).one()
    db.add(ItemAttributeLink(inventory_item_id=item.id, item_attribute_id=attribute.id))
    db.flush()
    return attribute.label


def test_a_note_reads_as_the_owner_writes_it(db: Session) -> None:
    """The owner's own example, CC-006140."""
    item = _note(db)
    radar = _link(db, item, "radar")
    assert radar.endswith(" Serial")
    assert suggested_description(db, item) == (
        f"{_short(_label(db, Grade, 'N67'))} EPQ Radar 1999 $1 S/N F06566560R. "
        f"{_label(db, NoteType, 'frn')} {_label(db, SealColor, 'green')}."
    )


def test_no_district_signatures_service_or_labels(db: Session) -> None:
    text = suggested_description(db, _note(db))
    for left_out in ("District", "signatures", "PMG", "Serial number", "Graded"):
        assert left_out not in text


def test_errors_are_promoted_beside_the_attributes(db: Session) -> None:
    item = _note(db)
    star = _link(db, item, "star")
    radar = _link(db, item, "radar").removesuffix(" Serial")
    error = db.scalars(select(ErrorType).order_by(ErrorType.id)).first()
    assert error is not None
    db.add(ItemError(inventory_item_id=item.id, error_type_id=error.id, details="left"))
    db.flush()
    text = suggested_description(db, item)
    grade = _short(_label(db, Grade, "N67"))
    # Right after the grade, before what the note is: the attributes, then
    # the error with its details.
    assert text.startswith(f"{grade} EPQ ")
    features = text.removeprefix(f"{grade} EPQ ").split(" 1999 $1")[0]
    assert features in (
        f"{star}, {radar}, {error.label} (left)",
        f"{radar}, {star}, {error.label} (left)",
    )
    assert text.endswith("Green Seal.")


def test_a_coin_leads_with_its_name_then_its_metal(db: Session) -> None:
    item = build_item(
        db,
        year_start=1947,
        series_id=_id(db, Series, "morgan_dollar"),
        metal_id=_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.773400"),
        grading_service_id=None,
        grade_id=None,
    )
    assert suggested_description(db, item) == (
        f"1947 {_label(db, Series, 'morgan_dollar')}. "
        f"{_label(db, Metal, 'silver')}, 0.7734 ozt fine."
    )


def test_an_item_with_nothing_recorded_gets_an_empty_suggestion(db: Session) -> None:
    item = build_item(
        db,
        year_start=None,
        denomination_id=None,
        series_id=None,
        grade_id=None,
        grading_service_id=None,
    )
    assert suggested_description(db, item) == ""


def test_the_listing_title_carries_the_designation(db: Session) -> None:
    item = _note(db)
    assert suggested_title(db, item).endswith(f"PMG {_label(db, Grade, 'N67')} EPQ")


def test_the_route_writes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _note(db).id
    db.commit()
    item = db.get(InventoryItem, item_id)
    assert item is not None
    before = (item.version, item.description)
    response = client.get(
        f"/api/inventory/{item_id}/suggested-description", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert "1999 $1 S/N F06566560R" in response.json()["description"]
    db.expire_all()
    item = db.get(InventoryItem, item_id)
    assert item is not None
    assert (item.version, item.description) == before
