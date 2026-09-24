"""Banknotes imported as coins, and kind changes that move the detail row.

`app.kind_repair` re-kinds the notes; `app.item_kinds` makes a kind change
through the item edit take the item's detail row with it.

The fixture note is shaped like CC-006033 as it stood on 2026-09-23: a $1
1935-D Silver Certificate recorded as a Peace dollar, with the dollar's
silver, the series letter read as a Denver mint mark, and its serial number
filed as a grading certificate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app import kind_repair
from app.models import (
    CoinDetail,
    Composition,
    Country,
    CurrencyDetail,
    Denomination,
    ErrorType,
    FriedbergNumber,
    InventoryItem,
    ItemCertification,
    ItemError,
    ItemFieldChange,
    ItemKind,
    Metal,
    Mint,
    NoteIssue,
    NoteType,
    SealColor,
    Series,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item

NOTE = "CC-900001"
SET = "CC-900002"


def _misfiled_note(db: Session, code: str = NOTE) -> InventoryItem:
    """A $1 1935-D Silver Certificate, recorded the way the import left it."""
    composition = db.scalars(select(Composition).limit(1)).first()
    item = make_item(
        db,
        item_code=code,
        denomination_id=code_id(db, Denomination, "usd_coin_1_00"),
        series_id=code_id(db, Series, "peace_dollar"),
        metal_id=code_id(db, Metal, "silver"),
        composition_id=composition.id if composition else None,
        fineness=Decimal("0.9000"),
        gross_weight_ozt=Decimal("0.859380"),
        fine_weight_ozt=Decimal("0.773440"),
        year_start=1935,
        year_end=1935,
        year_raw="1935-D",
        denom_raw="1",
        grade_raw="Blue Seal",
    )
    db.add(CoinDetail(inventory_item_id=item.id, mint_id=code_id(db, Mint, "D")))
    db.add(
        ItemCertification(
            inventory_item_id=item.id, cert_number="z13575376f", raw="z13575376f"
        )
    )
    db.commit()
    return item


@pytest.fixture
def one_issue(db: Session) -> None:
    """The one issue fact the refresh needs: $1 1935-D is a Silver Certificate."""
    db.execute(delete(NoteIssue))
    db.add(
        NoteIssue(
            denomination_id=code_id(db, Denomination, "usd_note_1"),
            series_year=1935,
            series_letter="D",
            note_type_id=code_id(db, NoteType, "silver_certificate"),
            seal_color_id=code_id(db, SealColor, "blue"),
        )
    )
    db.commit()


def _run(
    db: Session,
    *,
    commit: bool,
    user: User | None = None,
    named: tuple[str, ...] = (),
) -> list[kind_repair.Repair]:
    return kind_repair.run(
        db,
        commit=commit,
        user_id=user.id if user else None,
        named=named,
        sets=(SET,),
    )


@pytest.mark.parametrize("autoflush", [True, False], ids=["autoflush", "no-autoflush"])
def test_a_note_recorded_as_a_coin_becomes_a_banknote(
    db: Session, admin_user: User, one_issue: None, autoflush: bool
) -> None:
    db.autoflush = autoflush  # production's SessionLocal does not autoflush
    item = _misfiled_note(db)

    [repair] = [
        r for r in _run(db, commit=True, user=admin_user) if r.item_code == NOTE
    ]

    assert repair.skipped is None
    assert repair.fine_removed == Decimal("0.773440")
    db.expire_all()
    stored = db.get_one(InventoryItem, item.id)
    assert stored.item_kind_id == code_id(db, ItemKind, "currency")
    assert stored.denomination_id == code_id(db, Denomination, "usd_note_1")
    # Everything a coin carries and a note cannot is gone -- the silver above all.
    assert (
        stored.series_id,
        stored.metal_id,
        stored.composition_id,
        stored.fineness,
        stored.gross_weight_ozt,
        stored.fine_weight_ozt,
    ) == (None, None, None, None, None, None)
    assert db.get(CoinDetail, item.id) is None
    note = db.get_one(CurrencyDetail, item.id)
    assert (note.series_year, note.series_letter) == (1935, "D")
    assert note.serial_number == "Z13575376F"
    assert note.seal_color_id == code_id(db, SealColor, "blue")
    # Filled by the refresh from the issue fact, as a save in the editor would.
    assert note.note_type_id == code_id(db, NoteType, "silver_certificate")
    # The serial is a serial now, not a grading certificate as well.
    assert not db.scalars(
        select(ItemCertification).where(ItemCertification.inventory_item_id == item.id)
    ).all()
    logged = {
        row.field_name: (row.old_value, row.new_value, row.changed_by_id)
        for row in db.scalars(
            select(ItemFieldChange).where(ItemFieldChange.inventory_item_id == item.id)
        )
    }
    assert logged["item_kind"] == ("coin", "currency", admin_user.id)
    assert logged["denomination"] == ("usd_coin_1_00", "usd_note_1", admin_user.id)
    assert logged["serial_number"] == (None, "Z13575376F", admin_user.id)
    assert logged["metal"] == ("silver", None, admin_user.id)


def test_the_dry_run_writes_nothing(db: Session, one_issue: None) -> None:
    item = _misfiled_note(db)

    [repair] = [r for r in _run(db, commit=False) if r.item_code == NOTE]

    # The report is what a commit would write...
    assert ("item_kind", "coin", "currency") in repair.changes
    # ...and nothing of it was written.
    db.expire_all()
    stored = db.get_one(InventoryItem, item.id)
    assert stored.item_kind_id == code_id(db, ItemKind, "coin")
    assert stored.fine_weight_ozt == Decimal("0.773440")
    assert db.get(CurrencyDetail, item.id) is None
    assert db.get(CoinDetail, item.id) is not None
    assert not db.scalars(select(ItemFieldChange)).all()


def test_a_set_changes_only_its_kind(db: Session, admin_user: User) -> None:
    item = make_item(db, item_code=SET, year_raw="1978-S")
    db.add(CoinDetail(inventory_item_id=item.id, mint_id=code_id(db, Mint, "S")))
    db.commit()

    _run(db, commit=True, user=admin_user)

    db.expire_all()
    stored = db.get_one(InventoryItem, item.id)
    assert stored.item_kind_id == code_id(db, ItemKind, "set")
    # A set is coin-like: it keeps its coin row, mint and all.
    assert db.get_one(CoinDetail, item.id).mint_id == code_id(db, Mint, "S")


def test_running_it_again_changes_nothing(
    db: Session, admin_user: User, one_issue: None
) -> None:
    _misfiled_note(db)
    _run(db, commit=True, user=admin_user)
    logged = len(db.scalars(select(ItemFieldChange)).all())

    again = {
        r.item_code: r for r in _run(db, commit=True, user=admin_user, named=(NOTE,))
    }

    # Its serial is a serial now, so the search no longer finds it; named, it
    # is recognised as done.
    assert again[NOTE].skipped == "already done"
    assert again[SET].skipped == "no such item"
    assert len(db.scalars(select(ItemFieldChange)).all()) == logged


def test_a_coin_with_a_grading_certificate_is_left_alone(db: Session) -> None:
    """PCGS and NGC certificates are digits: not the shape of a note serial."""
    item = make_item(db, item_code="CC-900003")
    for number in ("12345678", "4163214-004", "PK5158"):
        db.add(ItemCertification(inventory_item_id=item.id, cert_number=number))
    db.commit()

    assert "CC-900003" not in {r.item_code for r in _run(db, commit=True)}
    db.expire_all()
    assert db.get_one(InventoryItem, item.id).item_kind_id == code_id(
        db, ItemKind, "coin"
    )


def test_a_named_note_without_a_serial_takes_the_face_value_typed(
    db: Session, admin_user: User
) -> None:
    """No $2 coin was known, so these rows have no denomination at all."""
    item = make_item(db, item_code="CC-900004", year_raw="1953 Bill", denom_raw="2")

    _run(db, commit=True, user=admin_user, named=("CC-900004",))

    db.expire_all()
    stored = db.get_one(InventoryItem, item.id)
    assert stored.denomination_id == code_id(db, Denomination, "usd_note_2")
    note = db.get_one(CurrencyDetail, item.id)
    assert (note.series_year, note.series_letter, note.serial_number) == (
        1953,
        None,
        None,
    )


def test_a_face_value_the_owner_corrected_wins(
    db: Session, admin_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed as $1, and the owner says it is a $2 note."""
    item = make_item(
        db,
        item_code="CC-900006",
        denomination_id=code_id(db, Denomination, "usd_coin_1_00"),
        denom_raw="1",
        year_raw="1976-",
    )
    monkeypatch.setitem(kind_repair.DENOMINATION_FIXES, "CC-900006", "usd_note_2")

    _run(db, commit=True, user=admin_user, named=("CC-900006",))

    db.expire_all()
    assert db.get_one(InventoryItem, item.id).denomination_id == code_id(
        db, Denomination, "usd_note_2"
    )


def test_a_named_set_becomes_a_note_with_its_error(
    db: Session, admin_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One of three consecutive $1 notes with ink smears, recorded as a set."""
    item = make_item(
        db,
        item_code="CC-900007",
        item_kind_id=code_id(db, ItemKind, "set"),
        denom_raw="3-Bill Set",
        year_raw="1995-",
    )
    db.add(CoinDetail(inventory_item_id=item.id))
    db.add(ItemCertification(inventory_item_id=item.id, cert_number="E15997464E"))
    db.commit()
    monkeypatch.setitem(kind_repair.DENOMINATION_FIXES, "CC-900007", "usd_note_1")
    monkeypatch.setitem(kind_repair.ERRORS, "CC-900007", "ink_smear")

    _run(db, commit=True, user=admin_user, named=("CC-900007",))
    # Run again: the error is not recorded twice.
    _run(db, commit=True, user=admin_user, named=("CC-900007",))

    db.expire_all()
    stored = db.get_one(InventoryItem, item.id)
    assert stored.item_kind_id == code_id(db, ItemKind, "currency")
    assert stored.denomination_id == code_id(db, Denomination, "usd_note_1")
    assert db.get(CoinDetail, item.id) is None
    note = db.get_one(CurrencyDetail, item.id)
    assert (note.series_year, note.serial_number) == (1995, "E15997464E")
    [error] = db.scalars(
        select(ItemError).where(ItemError.inventory_item_id == item.id)
    ).all()
    assert error.error_type_id == code_id(db, ErrorType, "ink_smear")
    assert error.noted_by_id == admin_user.id


def test_a_foreign_note_takes_its_country(
    db: Session, admin_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Mexican 5 peso note, recorded as a coin of no country."""
    item = make_item(db, item_code="CC-900008", country_id=None, denom_raw="5 Pesos")
    db.add(ItemCertification(inventory_item_id=item.id, cert_number="ET888973"))
    db.commit()
    monkeypatch.setitem(kind_repair.DENOMINATION_FIXES, "CC-900008", "mxn_note_5")
    monkeypatch.setitem(kind_repair.COUNTRY_FIXES, "CC-900008", "MX")

    _run(db, commit=True, user=admin_user, named=("CC-900008",))

    db.expire_all()
    stored = db.get_one(InventoryItem, item.id)
    assert stored.denomination_id == code_id(db, Denomination, "mxn_note_5")
    assert stored.country_id == code_id(db, Country, "MX")
    assert db.get_one(CurrencyDetail, item.id).serial_number == "ET888973"
    logged = db.scalar(
        select(ItemFieldChange.new_value).where(
            ItemFieldChange.inventory_item_id == item.id,
            ItemFieldChange.field_name == "country",
        )
    )
    assert logged == "MX"


def test_a_face_value_that_is_no_note_is_skipped(db: Session) -> None:
    make_item(db, item_code="CC-900005", denom_raw="Mint Set")

    [repair] = [
        r
        for r in _run(db, commit=True, named=("CC-900005",))
        if r.item_code == "CC-900005"
    ]

    assert repair.skipped == "no note denomination for 'Mint Set'"


@pytest.mark.parametrize(
    ("face", "typed", "expected"),
    [
        (Decimal("1.0000"), "1", "usd_note_1"),
        (Decimal("20.0000"), None, "usd_note_20"),
        (None, "2", "usd_note_2"),
        (None, "$5", "usd_note_5"),
        (Decimal("0.5000"), "0.5", None),
        (None, "3-Bill Set", None),
    ],
)
def test_the_note_denomination_follows_the_face_value(
    face: Decimal | None, typed: str | None, expected: str | None
) -> None:
    assert kind_repair.note_denomination(face, typed) == expected


def test_several_certificates_are_left_for_review(db: Session, one_issue: None) -> None:
    item = _misfiled_note(db)
    db.add(ItemCertification(inventory_item_id=item.id, cert_number="A2"))
    db.commit()

    _run(db, commit=True)

    db.expire_all()
    assert db.get_one(CurrencyDetail, item.id).serial_number is None
    assert (
        len(
            db.scalars(
                select(ItemCertification).where(
                    ItemCertification.inventory_item_id == item.id
                )
            ).all()
        )
        == 2
    )


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("1935-D", (1935, "D")),
        ("2017-A", (2017, "A")),
        ("1957 Silver Certificate", (1957, None)),
        ("1953 Bill", (1953, None)),
        ("1953-", (1953, None)),
        (None, (1999, None)),
    ],
)
def test_series_is_read_from_the_year_as_typed(
    typed: str | None, expected: tuple[int, str | None]
) -> None:
    assert kind_repair.series_of(typed, 1999) == expected


def test_a_seal_is_read_only_when_the_rating_names_one() -> None:
    assert kind_repair.seal_of("Blue Seal Offcenter") == "blue"
    assert kind_repair.seal_of("Red Seal") == "red"
    assert kind_repair.seal_of("21x $2 Bills") is None
    assert kind_repair.seal_of("Red Seal & Green Seal Mixed") is None


# -- the item edit ------------------------------------------------------------


def test_editing_a_coin_into_a_note_gives_it_a_note_row(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    db.add(CoinDetail(inventory_item_id=item.id, mint_id=code_id(db, Mint, "D")))
    db.commit()

    # Kind, a note denomination and a serial number in one request: the note
    # row has to exist before the serial can be written to it.
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={
            "item_kind": "currency",
            "denomination": "usd_note_1",
            "serial_number": "B12345678A",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(CoinDetail, item.id) is None
    assert db.get_one(CurrencyDetail, item.id).serial_number == "B12345678A"


def test_a_note_keeps_its_serial_unless_it_is_cleared_first(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        denomination_id=code_id(db, Denomination, "usd_note_1"),
    )
    db.add(CurrencyDetail(inventory_item_id=item.id, serial_number="B12345678A"))
    db.commit()
    to_coin = {"item_kind": "coin", "denomination": "usd_coin_1_00"}

    refused = client.patch(
        f"/api/inventory/{item.id}", json=to_coin, headers=admin_headers
    )
    assert refused.status_code == 422, refused.text
    assert "serial_number" in refused.json()["detail"]
    db.expire_all()
    assert db.get_one(CurrencyDetail, item.id).serial_number == "B12345678A"
    assert db.get_one(InventoryItem, item.id).item_kind_id == code_id(
        db, ItemKind, "currency"
    )

    # A Friedberg number is the note's too: it goes by its own endpoint first.
    friedberg = FriedbergNumber(fr_number="FR-TEST-1")
    db.add(friedberg)
    db.flush()
    db.get_one(CurrencyDetail, item.id).friedberg_id = friedberg.id
    db.commit()
    with_friedberg = client.patch(
        f"/api/inventory/{item.id}",
        json={**to_coin, "serial_number": None},
        headers=admin_headers,
    )
    assert with_friedberg.status_code == 422, with_friedberg.text
    assert "friedberg" in with_friedberg.json()["detail"]
    db.expire_all()
    db.get_one(CurrencyDetail, item.id).friedberg_id = None
    db.commit()

    # Cleared in the same request, the change goes through.
    cleared = client.patch(
        f"/api/inventory/{item.id}",
        json={**to_coin, "serial_number": None},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    db.expire_all()
    assert db.get(CurrencyDetail, item.id) is None
    assert db.get(CoinDetail, item.id) is not None
