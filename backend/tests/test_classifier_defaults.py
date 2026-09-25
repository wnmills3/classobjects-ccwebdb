"""Classifier defaults from known facts (docs/specs/classifier-defaults-design.md).

The issue facts are built by each test rather than taken from the seed file,
so the rules are tested on their own and not on the state of the data.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from app.classifier_defaults import classify, district_letter, run
from app.field_sources import derived_fields, record_derived
from app.models import (
    Composition,
    CurrencyDetail,
    Denomination,
    FedDistrict,
    InventoryItem,
    NoteIssue,
    NoteType,
    PurchaseOrder,
    ReferenceAlias,
    ReferenceMixin,
    SealColor,
    SignatureCombination,
    Vendor,
)
from app.seeding import SeedError, seed_all
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


@pytest.fixture(autouse=True)
def _no_seeded_issues(db: Session) -> None:
    """Start from no issue facts; each test adds the ones it is about.

    Inside the test's transaction, so the seeded rows return afterwards.
    """
    db.execute(delete(NoteIssue))
    db.flush()


def _id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def _code(db: Session, model: type[ReferenceMixin], row_id: int | None) -> str | None:
    if row_id is None:
        return None
    return db.get_one(model, row_id).code


def _issue(
    db: Session,
    denomination: str,
    year: int,
    note_type: str,
    seal: str,
    *,
    letter: str | None = None,
    signatures: str | None = None,
    prefix: str | None = None,
) -> None:
    db.add(
        NoteIssue(
            denomination_id=_id(db, Denomination, denomination),
            series_year=year,
            series_letter=letter,
            note_type_id=_id(db, NoteType, note_type),
            seal_color_id=_id(db, SealColor, seal),
            signature_combination_id=(
                _id(db, SignatureCombination, signatures) if signatures else None
            ),
            serial_prefix=prefix,
        )
    )
    db.flush()


def _note(
    db: Session,
    make_item: ItemFactory,
    denomination: str,
    year: int,
    *,
    letter: str | None = None,
    serial: str | None = None,
    **detail: object,
) -> InventoryItem:
    item = make_item(
        kind="currency",
        title="Plain note",
        denomination_id=_id(db, Denomination, denomination),
        year_start=year,
    )
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id,
            series_year=year,
            series_letter=letter,
            serial_number=serial,
            **detail,
        )
    )
    db.commit()
    return item


def _detail(db: Session, item: InventoryItem) -> CurrencyDetail:
    db.refresh(item)
    detail = item.currency_detail
    assert detail is not None
    db.refresh(detail)
    return detail


def _cases(db: Session, item: InventoryItem) -> list[tuple[str, str]]:
    return [
        (c.reason, c.detail)
        for c in classify(db).review
        if c.item_code == item.item_code
    ]


# -- notes: class, seal, signatures ------------------------------------------


def test_a_single_issue_fills_class_seal_and_signatures(
    db: Session, make_item: ItemFactory
) -> None:
    _issue(
        db,
        "usd_note_1",
        1957,
        "silver_certificate",
        "blue",
        signatures="priest_anderson",
    )
    note = _note(db, make_item, "usd_note_1", 1957)

    run(db, commit=True)

    detail = _detail(db, note)
    assert _code(db, NoteType, detail.note_type_id) == "silver_certificate"
    assert _code(db, SealColor, detail.seal_color_id) == "blue"
    assert _code(db, SignatureCombination, detail.signature_combination_id) == (
        "priest_anderson"
    )
    assert derived_fields(db, note.id) == {
        "note_type_id": "note_issue",
        "seal_color_id": "note_issue",
        "signature_combination_id": "note_issue",
    }


def test_a_dry_run_writes_nothing_and_a_rerun_changes_nothing(
    db: Session, make_item: ItemFactory
) -> None:
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue")
    note = _note(db, make_item, "usd_note_1", 1957)

    report = run(db, commit=False)
    assert report.changes
    assert _detail(db, note).note_type_id is None

    run(db, commit=True)
    assert [c for c in classify(db).changes if c.item_id == note.id] == []


def test_the_letter_is_part_of_the_series(db: Session, make_item: ItemFactory) -> None:
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue", letter="B")
    plain = _note(db, make_item, "usd_note_1", 1957)

    run(db, commit=True)

    assert _detail(db, plain).note_type_id is None
    assert ("unknown issue", "1957") in _cases(db, plain)


def test_two_classes_in_one_series_are_left_for_a_person(
    db: Session, make_item: ItemFactory
) -> None:
    _issue(db, "usd_note_1", 1928, "us_note", "red")
    _issue(db, "usd_note_1", 1928, "silver_certificate", "blue")
    unknown = _note(db, make_item, "usd_note_1", 1928)

    run(db, commit=True)

    assert _detail(db, unknown).note_type_id is None
    reason, detail = _cases(db, unknown)[0]
    assert reason == "ambiguous"
    assert "Silver Certificate" in detail and "United States Note" in detail


def test_a_recorded_seal_decides_the_class_and_is_kept(
    db: Session, make_item: ItemFactory
) -> None:
    _issue(db, "usd_note_1", 1928, "us_note", "red")
    _issue(db, "usd_note_1", 1928, "silver_certificate", "blue")
    red = _note(
        db, make_item, "usd_note_1", 1928, seal_color_id=_id(db, SealColor, "red")
    )

    run(db, commit=True)

    detail = _detail(db, red)
    assert _code(db, NoteType, detail.note_type_id) == "us_note"
    assert _code(db, SealColor, detail.seal_color_id) == "red"
    # The seal was the data's, so it is not recorded as derived.
    assert set(derived_fields(db, red.id)) == {"note_type_id"}


def test_a_persons_value_is_never_replaced_and_a_contradiction_is_reported(
    db: Session, make_item: ItemFactory
) -> None:
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue")
    wrong = _note(
        db, make_item, "usd_note_1", 1957, note_type_id=_id(db, NoteType, "frn")
    )

    run(db, commit=True)

    detail = _detail(db, wrong)
    assert _code(db, NoteType, detail.note_type_id) == "frn"
    assert detail.seal_color_id is None
    assert ("disagrees", "1957: note_type") in _cases(db, wrong)


def test_a_derived_value_is_refreshed_when_the_facts_change(
    db: Session, make_item: ItemFactory
) -> None:
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue")
    note = _note(
        db, make_item, "usd_note_1", 1957, note_type_id=_id(db, NoteType, "us_note")
    )
    record_derived(db, note.id, ["note_type_id"], "note_issue")
    db.commit()

    run(db, commit=True)

    assert _code(db, NoteType, _detail(db, note).note_type_id) == "silver_certificate"


# -- notes: the Federal Reserve Bank from the serial -------------------------


@pytest.mark.parametrize(
    ("serial", "face", "year", "expected"),
    [
        ("B12345678A", "1", 1969, ("B", "")),
        ("L12345678*", "1", 2009, ("L", "")),
        ("EB12345678A", "20", 2004, ("B", "E")),
        ("B12345678A", "20", 2004, ("", "")),  # a 2004 $20 has two letters
        ("*12345678A", "1", 1969, ("", "")),  # a star hides the Bank
        ("not a serial", "1", 1969, ("", "")),
    ],
)
def test_the_serial_names_the_bank(
    serial: str, face: str, year: int, expected: tuple[str, str]
) -> None:
    assert district_letter(serial, Decimal(face), year) == expected


def test_a_federal_reserve_note_gets_its_district(
    db: Session, make_item: ItemFactory
) -> None:
    _issue(db, "usd_note_1", 1969, "frn", "green")
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue")
    frn = _note(db, make_item, "usd_note_1", 1969, serial="B12345678A")
    certificate = _note(db, make_item, "usd_note_1", 1957, serial="B12345678A")

    run(db, commit=True)

    new_york = db.execute(
        select(FedDistrict.id).where(FedDistrict.letter == "B")
    ).scalar_one()
    assert _detail(db, frn).fed_district_id == new_york
    assert derived_fields(db, frn.id)["fed_district_id"] == "serial_district"
    # A Silver Certificate's prefix letter says nothing about a district.
    assert _detail(db, certificate).fed_district_id is None


def test_a_serial_from_another_series_is_reported(
    db: Session, make_item: ItemFactory
) -> None:
    _issue(db, "usd_note_20", 2004, "frn", "green", prefix="E")
    right = _note(db, make_item, "usd_note_20", 2004, serial="EB12345678A")
    wrong = _note(db, make_item, "usd_note_20", 2004, serial="CB12345678A")

    assert not [c for c in _cases(db, right) if c[0] == "serial prefix"]
    assert any(reason == "serial prefix" for reason, _ in _cases(db, wrong))


# -- coins: composition ------------------------------------------------------


def _dime(
    db: Session, make_item: ItemFactory, year: int, **extra: object
) -> InventoryItem:
    return make_item(
        title="Plain dime",
        denomination_id=_id(db, Denomination, "usd_coin_0_10"),
        year_start=year,
        **extra,
    )


def test_a_coin_gets_its_composition(db: Session, make_item: ItemFactory) -> None:
    dime = _dime(db, make_item, 1964)

    run(db, commit=True)

    db.refresh(dime)
    composition = db.get(Composition, dime.composition_id)
    assert composition is not None
    assert composition.fineness == Decimal("0.9000")
    assert dime.fineness == Decimal("0.9000")
    assert derived_fields(db, dime.id)["fineness"] == "composition"


def test_a_stated_value_is_kept(db: Session, make_item: ItemFactory) -> None:
    dime = _dime(db, make_item, 1964, fineness=Decimal("0.5000"))

    run(db, commit=True)

    db.refresh(dime)
    assert dime.fineness == Decimal("0.5000")
    assert "fineness" not in derived_fields(db, dime.id)
    assert dime.composition_id is not None


# -- the API -----------------------------------------------------------------


def test_editing_a_derived_field_makes_it_the_persons(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue")
    note = _note(db, make_item, "usd_note_1", 1957)
    run(db, commit=True)

    body = client.get(f"/api/inventory/{note.id}", headers=admin_headers).json()
    assert body["note_type"] == "silver_certificate"
    assert body["derived"]["note_type_id"] == "note_issue"

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"note_type": "us_note", "serial_number": "A00000001A"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    body = client.get(f"/api/inventory/{note.id}", headers=admin_headers).json()
    assert body["note_type"] == "us_note"
    assert body["serial_number"] == "A00000001A"
    # The person's class stands. The facts know no such note, so the seal
    # derived from the class they replaced is withdrawn, not left behind.
    assert body["seal_color"] is None
    assert body["derived"] == {}
    # And the pass leaves it alone from now on.
    run(db, commit=True)
    assert _code(db, NoteType, _detail(db, note).note_type_id) == "us_note"


def test_a_note_field_on_a_coin_is_refused_by_name(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    coin = _dime(db, make_item, 1964)

    response = client.patch(
        f"/api/inventory/{coin.id}", json={"seal_color": "blue"}, headers=admin_headers
    )

    assert response.status_code == 422
    assert "seal_color" in response.text


def test_a_bulk_edit_makes_the_fields_the_persons(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    dime = _dime(db, make_item, 1964)
    run(db, commit=True)
    assert "metal_id" in derived_fields(db, dime.id)

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [dime.id], "changes": {"metal": "silver"}},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert "metal_id" not in derived_fields(db, dime.id)
    assert "fineness" in derived_fields(db, dime.id)


def test_a_new_items_suggestions_are_recorded_as_derived(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue")
    vendor = Vendor(name="Suggestion seller")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, order_number="SUG-1")
    db.add(order)
    db.commit()

    response = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "currency",
            "source_title": "1957 $1",
            "denomination": "usd_note_1",
            "series_year": 1957,
            "note_type": "silver_certificate",
            "seal_color": "blue",
            "suggested": ["note_type", "seal_color", "signature_combination"],
        },
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    # Only suggestions that were actually filled are recorded.
    assert body["derived"] == {
        "note_type_id": "suggestion",
        "seal_color_id": "suggestion",
    }


def test_an_unknown_suggested_field_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = Vendor(name="Suggestion seller 2")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, order_number="SUG-2")
    db.add(order)
    db.commit()

    response = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "coin",
            "source_title": "x",
            "suggested": ["grade"],
        },
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "grade" in response.text


# -- seeding -----------------------------------------------------------------


def _seed_file(tmp_path: Path, payload: dict) -> Path:
    (tmp_path / "facts.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def _issue_row(**overrides: object) -> dict:
    row = {
        "denomination": "usd_note_1",
        "series_year": 1957,
        "series_letter": None,
        "note_type": "silver_certificate",
        "seal_color": "blue",
        "signatures": "priest_anderson",
    }
    row.update(overrides)
    return row


def test_note_issues_follow_the_file(db: Session, tmp_path: Path) -> None:
    first = _seed_file(
        tmp_path, {"note_issue": [_issue_row(), _issue_row(series_letter="A")]}
    )
    seed_all(db, first, only=["note_issue"])
    assert db.query(NoteIssue).count() == 2

    second = _seed_file(
        tmp_path, {"note_issue": [_issue_row(signatures="smith_dillon")]}
    )
    stats = seed_all(db, second, only=["note_issue"])

    assert stats["note_issue"] == {"removed": 1, "updated": 1}
    (only,) = db.execute(select(NoteIssue)).scalars()
    assert (
        _code(db, SignatureCombination, only.signature_combination_id) == "smith_dillon"
    )


def test_an_issue_naming_an_unknown_code_is_refused(
    db: Session, tmp_path: Path
) -> None:
    bad = _seed_file(tmp_path, {"note_issue": [_issue_row(note_type="legal_tender")]})

    with pytest.raises(SeedError, match="legal_tender"):
        seed_all(db, bad, only=["note_issue"])
    db.rollback()


def test_aliases_name_rows_of_any_classifier(db: Session, tmp_path: Path) -> None:
    folder = _seed_file(
        tmp_path,
        {
            "reference_alias": [
                {"table": "note_type", "code": "us_note", "alias": "Greenback"}
            ]
        },
    )

    seed_all(db, folder, only=["reference_alias"])
    again = seed_all(db, folder, only=["reference_alias"])

    assert again["reference_alias"] == {"unchanged": 1}
    alias = db.execute(
        select(ReferenceAlias).where(ReferenceAlias.alias == "Greenback")
    ).scalar_one()
    assert (alias.table_name, alias.row_id) == (
        "note_type",
        _id(db, NoteType, "us_note"),
    )


def test_the_seeded_note_types_use_bep_names(db: Session) -> None:
    codes = set(db.execute(select(NoteType.code)).scalars())
    assert {"national_bank_note", "demand_note", "treasury_note", "us_note"} <= codes
    assert not {"legal_tender", "national_currency"} & codes
    legal = db.execute(
        select(ReferenceAlias.row_id).where(
            ReferenceAlias.table_name == "note_type",
            ReferenceAlias.alias == "Legal Tender Note",
        )
    ).scalar_one()
    assert legal == _id(db, NoteType, "us_note")


# -- the rating as evidence, and defaults applied as items change ------------


def test_the_rating_can_name_the_class(db: Session, make_item: ItemFactory) -> None:
    _issue(db, "usd_note_1", 1928, "us_note", "red")
    _issue(db, "usd_note_1", 1928, "silver_certificate", "blue")
    note = _note(db, make_item, "usd_note_1", 1928)
    note.rating = "VF Legal Tender funnyback"
    db.commit()

    run(db, commit=True)

    detail = _detail(db, note)
    assert _code(db, NoteType, detail.note_type_id) == "us_note"
    assert _code(db, SealColor, detail.seal_color_id) == "red"


def test_editing_the_series_refreshes_what_followed_from_it(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue")
    _issue(db, "usd_note_1", 1969, "frn", "green")
    note = _note(db, make_item, "usd_note_1", 1957, serial="B12345678A")
    run(db, commit=True)

    response = client.patch(
        f"/api/inventory/{note.id}", json={"series_year": 1969}, headers=admin_headers
    )

    assert response.status_code == 200, response.text
    body = client.get(f"/api/inventory/{note.id}", headers=admin_headers).json()
    assert (body["note_type"], body["seal_color"], body["fed_district"]) == (
        "frn",
        "green",
        "B",
    )


def test_a_new_coin_gets_its_composition_at_once(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = Vendor(name="Coin seller")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, order_number="COIN-1")
    db.add(order)
    db.commit()

    response = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "coin",
            "source_title": "1964 dime",
            "denomination": "usd_coin_0_10",
            "country": "US",
            "year_start": 1964,
        },
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["metal"] == "silver"
    assert body["derived"]["metal_id"] == "composition"


def test_the_form_is_told_what_a_note_would_be(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _issue(db, "usd_note_1", 1928, "us_note", "red", signatures="woods_woodin")
    _issue(
        db, "usd_note_1", 1928, "silver_certificate", "blue", signatures="tate_mellon"
    )
    _issue(db, "usd_note_1", 1969, "frn", "green")

    def ask(**params: object) -> dict:
        response = client.get(
            "/api/defaults/note", params=params, headers=admin_headers
        )
        assert response.status_code == 200, response.text
        return response.json()

    open_series = ask(denomination="usd_note_1", series_year=1928)
    assert open_series["note_type"] is None
    chosen_seal = ask(denomination="usd_note_1", series_year=1928, seal_color="red")
    assert chosen_seal == {
        "note_type": "us_note",
        "seal_color": None,  # the person's own choice is not suggested back
        "signature_combination": "woods_woodin",
        "fed_district": None,
        "warning": None,  # Series 1928 is on record for $1
    }
    frn = ask(denomination="usd_note_1", series_year=1969, serial_number="L12345678A")
    assert (frn["note_type"], frn["fed_district"]) == ("frn", "L")


def test_the_form_is_told_a_coins_metal(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.get(
        "/api/defaults/coin",
        params={"denomination": "usd_coin_0_10", "country": "US", "year": 1964},
        headers=admin_headers,
    )

    assert response.json() == {"metal": "silver"}


def test_suggestions_are_staff_only(client: TestClient) -> None:
    response = client.get("/api/defaults/coin", params={"year": 1964})

    assert response.status_code == 401


def test_search_finds_a_note_by_its_class_nickname(
    db: Session, make_item: ItemFactory
) -> None:
    # The note's own text says nothing about its class; the recorded class,
    # reached through the "Legal Tender" alias, is what matches.
    from app.inventory_search import CURRENCY_VIEW, search

    note = _note(
        db, make_item, "usd_note_1", 1928, note_type_id=_id(db, NoteType, "us_note")
    )

    rows, _ = search(db, CURRENCY_VIEW, params={}, query="legal tender")

    assert note.item_code in {row["item_code"] for row in rows}


# -- retracting what the facts no longer support ------------------------------


def test_a_series_the_facts_do_not_cover_takes_back_the_defaults(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    _issue(
        db,
        "usd_note_1",
        1957,
        "silver_certificate",
        "blue",
        signatures="priest_anderson",
    )
    note = _note(db, make_item, "usd_note_1", 1957)
    run(db, commit=True)

    # Corrected to a large-size year: nothing in the facts says what it is.
    response = client.patch(
        f"/api/inventory/{note.id}", json={"series_year": 1917}, headers=admin_headers
    )

    assert response.status_code == 200, response.text
    body = client.get(f"/api/inventory/{note.id}", headers=admin_headers).json()
    assert (body["note_type"], body["seal_color"], body["signature_combination"]) == (
        None,
        None,
        None,
    )
    assert body["derived"] == {}


def test_a_derived_district_goes_when_the_class_changes(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    _issue(db, "usd_note_1", 1969, "frn", "green")
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue")
    note = _note(db, make_item, "usd_note_1", 1969, serial="B12345678A")
    run(db, commit=True)
    assert _detail(db, note).fed_district_id is not None

    client.patch(
        f"/api/inventory/{note.id}", json={"series_year": 1957}, headers=admin_headers
    )

    detail = _detail(db, note)
    assert _code(db, NoteType, detail.note_type_id) == "silver_certificate"
    assert detail.fed_district_id is None
    assert "fed_district_id" not in derived_fields(db, note.id)


def test_a_signature_the_issue_does_not_have_is_taken_back(
    db: Session, make_item: ItemFactory
) -> None:
    # Series 1929 bank notes were signed by bank officers: the facts say no pair.
    _issue(db, "usd_note_10", 1929, "frbn", "brown")
    note = _note(
        db,
        make_item,
        "usd_note_10",
        1929,
        signature_combination_id=_id(db, SignatureCombination, "julian_morgenthau"),
    )
    record_derived(db, note.id, ["signature_combination_id"], "note_issue")
    db.commit()

    run(db, commit=True)

    assert _detail(db, note).signature_combination_id is None


def test_a_year_range_takes_back_a_derived_composition(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    dime = _dime(db, make_item, 1964)
    run(db, commit=True)
    assert dime.composition_id is not None

    client.patch(
        f"/api/inventory/{dime.id}",
        json={"year_start": 1960, "year_end": 1970},
        headers=admin_headers,
    )

    db.refresh(dime)
    assert dime.composition_id is None
    assert dime.fineness is None
    assert derived_fields(db, dime.id) == {}


def test_a_range_one_composition_covers_keeps_it(
    db: Session, make_item: ItemFactory
) -> None:
    # A 1999-2008 quarter set is clad throughout.
    quarters = make_item(
        title="State quarter set",
        denomination_id=_id(db, Denomination, "usd_coin_0_25"),
        year_start=1999,
        year_end=2008,
    )

    run(db, commit=True)

    db.refresh(quarters)
    composition = db.get(Composition, quarters.composition_id)
    assert composition is not None
    assert composition.year_from <= 1999
    assert composition.year_to is None or composition.year_to >= 2008


def test_a_stated_value_the_composition_contradicts_is_reported(
    db: Session, make_item: ItemFactory
) -> None:
    dime = _dime(db, make_item, 1964, fineness=Decimal("0.5000"))

    assert ("disagrees", "composition says otherwise: fineness") in _cases(db, dime)


# -- a person emptying a field -----------------------------------------------


def test_an_emptied_field_stays_empty(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    _issue(db, "usd_note_1", 1957, "silver_certificate", "blue")
    note = _note(db, make_item, "usd_note_1", 1957)
    run(db, commit=True)

    response = client.patch(
        f"/api/inventory/{note.id}", json={"note_type": None}, headers=admin_headers
    )

    assert response.status_code == 200, response.text
    body = client.get(f"/api/inventory/{note.id}", headers=admin_headers).json()
    assert body["note_type"] is None
    assert "note_type_id" not in body["derived"]
    # Not refilled by a batch run either...
    run(db, commit=True)
    assert _detail(db, note).note_type_id is None

    # ...until the person sets it again, which makes it theirs.
    client.patch(
        f"/api/inventory/{note.id}", json={"note_type": "frn"}, headers=admin_headers
    )
    assert _code(db, NoteType, _detail(db, note).note_type_id) == "frn"
    assert "note_type_id" not in derived_fields(db, note.id)


def test_emptying_a_field_no_pass_fills_holds_nothing(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    from app.field_sources import sources_by_item

    dime = _dime(db, make_item, 1964)

    response = client.patch(
        f"/api/inventory/{dime.id}", json={"grade": None}, headers=admin_headers
    )

    assert response.status_code == 200, response.text
    assert "grade_id" not in sources_by_item(db, [dime.id]).get(dime.id, {})
