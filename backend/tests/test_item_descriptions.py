"""The description the item editor's Suggest button offers (`app.item_descriptions`).

Composed from the saved record for the owner to edit: the item's name as
the listing title names it, the note's or coin's own details, the grade
with its service, designation and certificate, then attributes and errors.
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
    ItemCertification,
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


def _note(db: Session, **overrides: object) -> int:
    fields: dict[str, object] = {
        "kind": "currency",
        "denomination_id": _id(db, Denomination, "usd_note_1"),
        "grade_id": _id(db, Grade, "N64"),
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
    db.add(ItemCertification(inventory_item_id=item.id, cert_number="8061234-005"))
    db.flush()
    db.refresh(item)
    return item.id


def test_a_graded_note_is_described_in_full(db: Session) -> None:
    item = db.get(InventoryItem, _note(db))
    assert item is not None
    signers = db.scalars(select(SignatureCombination)).first()
    assert signers is not None
    grade = _label(db, Grade, "N64")
    assert suggested_description(db, item) == (
        "Series 1999 $1 Federal Reserve Note. "
        f"District {_label(db, FedDistrict, 'F')}, "
        f"{_label(db, SealColor, 'green')}, signatures {signers.label}. "
        "Serial number F06566560R. "
        f"Graded PMG {grade} EPQ, certificate 8061234-005."
    )


def test_attributes_and_errors_follow_the_grade(db: Session) -> None:
    item = db.get(InventoryItem, _note(db))
    assert item is not None
    star = db.scalars(select(ItemAttribute).where(ItemAttribute.code == "star")).one()
    db.add(ItemAttributeLink(inventory_item_id=item.id, item_attribute_id=star.id))
    error = db.scalars(select(ErrorType).order_by(ErrorType.id)).first()
    assert error is not None
    db.add(ItemError(inventory_item_id=item.id, error_type_id=error.id, details="left"))
    db.flush()

    text = suggested_description(db, item)
    assert text.endswith(f"{star.label}. Error: {error.label} (left).")


def test_a_grade_with_no_service_recorded_is_not_called_raw(db: Session) -> None:
    item = db.get(InventoryItem, _note(db, grading_service_id=None))
    assert item is not None
    text = suggested_description(db, item)
    assert f"Grade {_label(db, Grade, 'N64')} EPQ, certificate" in text
    assert "Ungraded" not in text and "raw" not in text


def test_a_coin_names_its_metal_and_fine_weight(db: Session) -> None:
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
    item = db.get(InventoryItem, _note(db))
    assert item is not None
    assert suggested_title(db, item).endswith(f"PMG {_label(db, Grade, 'N64')} EPQ")


def test_the_route_writes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item_id = _note(db)
    db.commit()
    item = db.get(InventoryItem, item_id)
    assert item is not None
    before = (item.version, item.description)
    response = client.get(
        f"/api/inventory/{item_id}/suggested-description", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["description"].startswith("Series 1999 $1")
    db.expire_all()
    item = db.get(InventoryItem, item_id)
    assert item is not None
    assert (item.version, item.description) == before
