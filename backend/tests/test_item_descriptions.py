"""The description the item editor's Suggest button offers (`app.item_descriptions`).

In the owner's style: grade, designation and attributes first,
then year, face value and serial, then the note type and seal -- no field
labels, no district, signatures or grading service.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.item_descriptions import _without_serial, suggested_description
from app.models import (
    CoinDetail,
    Country,
    Currency,
    CurrencyDetail,
    Denomination,
    DenominationKind,
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
    StrikeType,
)
from app.models.reference import Grade
from app.offer_titles import suggested_title
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import code_id
from tests.conftest import build_item


def _label(db: Session, model: type[ReferenceMixin], code: str) -> str:
    """The label of the vocabulary value with this code."""
    return db.execute(select(model.label).where(model.code == code)).scalar_one()


def _short(grade_label: str) -> str:
    """A grade's label as a description writes it: Uncirculated as Unc."""
    return grade_label.replace("Uncirculated", "Unc")


def _note(db: Session, **overrides: object) -> InventoryItem:
    """A graded $1 note with its currency detail, the overrides applied."""
    fields: dict[str, object] = {
        "kind": "currency",
        "denomination_id": code_id(db, Denomination, "usd_note_1"),
        "grade_id": code_id(db, Grade, "N67"),
        "grade_designation_id": code_id(db, GradeDesignation, "EPQ"),
        "grading_service_id": code_id(db, GradingService, "PMG"),
        "year_start": None,
    }
    item = build_item(db, **{**fields, **overrides})
    signers = db.scalars(select(SignatureCombination)).first()
    assert signers is not None
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id,
            note_type_id=code_id(db, NoteType, "frn"),
            seal_color_id=code_id(db, SealColor, "green"),
            fed_district_id=code_id(db, FedDistrict, "F"),
            signature_combination_id=signers.id,
            series_year=1999,
            serial_number="F06566560R",
        )
    )
    db.flush()
    db.refresh(item)
    return item


def _link(db: Session, item: InventoryItem, code: str) -> str:
    """Give the item the attribute with this code, and return its label."""
    attribute = db.scalars(
        select(ItemAttribute).where(ItemAttribute.code == code)
    ).one()
    db.add(ItemAttributeLink(inventory_item_id=item.id, item_attribute_id=attribute.id))
    db.flush()
    return attribute.label


def test_a_note_reads_as_the_owner_writes_it(db: Session) -> None:
    """The owner's own example of a note described well."""
    item = _note(db)
    radar = _link(db, item, "radar")
    assert radar.endswith(" Serial")
    assert suggested_description(db, item) == (
        f"{_short(_label(db, Grade, 'N67'))} EPQ Radar 1999 $1 S/N F06566560R. "
        f"{_label(db, NoteType, 'frn')} {_label(db, SealColor, 'green')}."
    )


def test_a_note_from_abroad_names_its_country(db: Session) -> None:
    item = _note(db, country_id=code_id(db, Country, "CA"))
    canada = _label(db, Country, "CA")
    assert f"1999 {canada} $1 S/N" in suggested_description(db, item)


@pytest.mark.parametrize(
    ("series_label", "expected"),
    [("Florida Goldback", "½ Florida Goldback"), ("Goldback", "½ Goldback")],
)
def test_a_series_named_for_the_face_values_unit_takes_its_place(
    db: Session, series_label: str, expected: str
) -> None:
    currency = Currency(code="gb_test", label="Goldback")
    db.add(currency)
    db.flush()
    half = Denomination(
        code="gb_test_0_5",
        label="½ Goldback",
        currency_id=currency.id,
        face_value=Decimal("0.5"),
        kind=DenominationKind.note,
    )
    series = Series(code="gb_series_test", label=series_label, applies_to="currency")
    db.add_all([half, series])
    db.flush()
    item = _note(db, denomination_id=half.id, series_id=series.id)
    text = suggested_description(db, item)
    assert f"1999 {expected} S/N" in text
    assert text.count("Goldback") == 1


def test_a_notes_design_series_follows_its_face_value(db: Session) -> None:
    item = _note(db, series_id=code_id(db, Series, "barr_note"))
    barr = _label(db, Series, "barr_note")
    assert f"1999 $1 {barr} S/N F06566560R." in suggested_description(db, item)


def test_a_design_series_an_attribute_has_named_is_not_said_twice(
    db: Session,
) -> None:
    star = _label(db, ItemAttribute, "star")
    series = Series(code="star_series_test", label=star, applies_to="currency")
    db.add(series)
    db.flush()
    item = _note(db, series_id=series.id)
    _link(db, item, "star")
    assert suggested_description(db, item).count(star) == 1


@pytest.mark.parametrize(
    ("code", "label"),
    [
        # Its series year and class are written already.
        ("series_1929_nbn_test", "Series 1999 Federal Reserve Note"),
        # Only the series again.
        ("only_the_year_test", "1999"),
    ],
)
def test_a_design_series_that_only_repeats_the_note_is_left_out(
    db: Session, code: str, label: str
) -> None:
    series = Series(code=code, label=label, applies_to="currency")
    db.add(series)
    db.flush()
    item = _note(db, series_id=series.id)
    assert suggested_description(db, item) == (
        f"{_short(_label(db, Grade, 'N67'))} EPQ 1999 $1 S/N F06566560R. "
        f"{_label(db, NoteType, 'frn')} {_label(db, SealColor, 'green')}."
    )


def test_fancy_serial_is_left_out_beside_the_pattern_it_sums_up(db: Session) -> None:
    item = _note(db)
    radar = _link(db, item, "radar").removesuffix(" Serial")
    _link(db, item, "fancy_serial")
    text = suggested_description(db, item)
    assert f"EPQ {radar} 1999" in text
    assert "Fancy" not in text


def test_fancy_serial_alone_is_still_said(db: Session) -> None:
    item = _note(db)
    fancy = _link(db, item, "fancy_serial").removesuffix(" Serial")
    assert f"EPQ {fancy} 1999" in suggested_description(db, item)


def test_fancy_serial_is_said_beside_an_attribute_that_is_no_digit_pattern(
    db: Session,
) -> None:
    # A star, a low serial or No Motto says nothing of the digits' pattern, so
    # beside one of those "Fancy" is still the only word for it.
    item = _note(db)
    star = _link(db, item, "star")
    fancy = _link(db, item, "fancy_serial").removesuffix(" Serial")
    _link(db, item, "low_serial")
    text = suggested_description(db, item)
    assert star in text
    assert fancy in text


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
        series_id=code_id(db, Series, "morgan_dollar"),
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.773400"),
        grading_service_id=None,
        grade_id=None,
    )
    assert suggested_description(db, item) == (
        f"1947 {_label(db, Series, 'morgan_dollar')}. "
        f"{_label(db, Metal, 'silver')}, 0.7734 ozt fine."
    )


def test_a_bullion_serial_ends_the_first_sentence(db: Session) -> None:
    """As a note's does: what identifies the piece, then its metal.

    A year with nothing to name is not written, as for any coin-side item.
    """
    item = build_item(
        db,
        year_start=2024,
        metal_id=code_id(db, Metal, "gold"),
        fine_weight_ozt=Decimal("1.000000"),
        grading_service_id=None,
        grade_id=None,
    )
    db.add(CoinDetail(inventory_item_id=item.id, serial_number="PAMP-0042"))
    db.flush()
    db.refresh(item)
    assert suggested_description(db, item) == (
        f"S/N PAMP-0042. {_label(db, Metal, 'gold')}, 1 ozt fine."
    )


def _designation(db: Session, code: str, label: str) -> int:
    """A designation added by hand, whose code is not its abbreviation."""
    row = GradeDesignation(code=code, label=label, source="manual")
    db.add(row)
    db.flush()
    return row.id


def _proof(db: Session, designation_id: int, strike: str = "proof") -> InventoryItem:
    """A 68 coin of this strike, with this designation."""
    return build_item(
        db,
        year_start=1959,
        series_id=code_id(db, Series, "washington_quarter"),
        grade_id=code_id(db, Grade, "68"),
        strike_type_id=code_id(db, StrikeType, strike),
        grade_designation_id=designation_id,
        grading_service_id=None,
    )


def test_a_designation_naming_the_strike_stands_by_the_number(db: Session) -> None:
    """PF (Proof) on a proof is the holder's prefix: "PF (Proof) 68", once."""
    item = _proof(db, _designation(db, "pf_test", "PF (Proof)"))
    text = suggested_description(db, item)
    assert text.startswith("PF (Proof) 68 1959")
    assert "pf_test" not in text
    assert "PR68" not in text
    assert "PMG" not in suggested_title(db, item)
    assert "PF (Proof) 68" in suggested_title(db, item)


def test_a_designation_is_written_by_its_abbreviation_not_its_code(
    db: Session,
) -> None:
    """A hand-added designation's code is a database key, not what is written."""
    item = _proof(db, _designation(db, "xd_test", "XD (Extra Deep)"))
    text = suggested_description(db, item)
    assert text.startswith("PR68 XD 1959")
    assert "xd_test" not in text
    assert "PR68 XD" in suggested_title(db, item)


def test_a_coins_variety_follows_its_name(db: Session) -> None:
    """An overmintmark is entered as the variety and read after the name.

    "O/S" goes in Variety, and the description and the listing title both
    carry it.
    """
    item = build_item(
        db,
        year_start=1900,
        series_id=code_id(db, Series, "morgan_dollar"),
        metal_id=code_id(db, Metal, "silver"),
        fine_weight_ozt=Decimal("0.773440"),
        grading_service_id=None,
        grade_id=None,
    )
    db.add(CoinDetail(inventory_item_id=item.id, variety="O/S"))
    db.commit()
    db.refresh(item)

    assert suggested_description(db, item) == (
        f"1900 {_label(db, Series, 'morgan_dollar')} O/S. "
        f"{_label(db, Metal, 'silver')}, 0.77344 ozt fine."
    )
    assert "Morgan Dollar O/S" in suggested_title(db, item)


def test_a_foreign_coin_names_its_country_before_what_it_is(db: Session) -> None:
    item = build_item(
        db,
        country_id=code_id(db, Country, "AT"),
        year_start=1912,
        denomination_id=code_id(db, Denomination, "usd_coin_0_50"),
        grading_service_id=None,
        grade_id=None,
    )
    assert suggested_description(db, item) == (
        f"1912 {_label(db, Country, 'AT')} {_label(db, Denomination, 'usd_coin_0_50')}."
    )


def test_a_foreign_coin_with_no_series_or_face_value_is_its_year_and_country(
    db: Session,
) -> None:
    """Its country is what is known of it: the year is not dropped with the name."""
    item = build_item(
        db,
        country_id=code_id(db, Country, "AT"),
        year_start=1912,
        metal_id=code_id(db, Metal, "gold"),
        fine_weight_ozt=Decimal("0.098020"),
        grading_service_id=None,
        grade_id=None,
    )
    assert suggested_description(db, item) == (
        f"1912 {_label(db, Country, 'AT')}. "
        f"{_label(db, Metal, 'gold')}, 0.09802 ozt fine."
    )


@pytest.mark.parametrize("country", ["US", "XX", None])
def test_no_country_is_named_at_home_or_when_it_is_not_known(
    db: Session, country: str | None
) -> None:
    item = build_item(
        db,
        country_id=None if country is None else code_id(db, Country, country),
        year_start=1912,
        metal_id=code_id(db, Metal, "gold"),
        grading_service_id=None,
        grade_id=None,
    )
    assert suggested_description(db, item) == f"{_label(db, Metal, 'gold')}."


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


@pytest.mark.parametrize(
    ("label", "written"),
    [
        ("Radar Serial", "Radar"),
        ("Super  Radar \t Serial", "Super  Radar"),
        # All of a run of space goes with the noun, wherever the run begins.
        ("  Serial", ""),
        ("Radar" + " " * 200 + "Serial", "Radar"),
        # Only the noun at the very end, and only after space.
        ("Serial", "Serial"),
        ("Serial Number", "Serial Number"),
        ("Radar Serial ", "Radar Serial "),
        ("RadarSerial", "RadarSerial"),
        ("Radar serial", "Radar serial"),
        ("Low Serial Serial", "Low Serial"),
        # A line break counts as space before the noun, and one closing the
        # label is kept.
        ("Radar\nSerial", "Radar"),
        ("Radar \x0b\xa0Serial", "Radar"),
        ("Radar Serial\n", "Radar\n"),
        ("Serial\n", "Serial\n"),
        ("Radar Serial\n\n", "Radar Serial\n\n"),
    ],
)
def test_a_serial_feature_s_label_loses_the_noun_it_ends_in(
    label: str, written: str
) -> None:
    assert _without_serial(label) == written
