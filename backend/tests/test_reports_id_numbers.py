"""`cb_id_numbers`: every held item a serial or a certificate identifies.

Every serial and certificate number here is made up.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import (
    CoinDetail,
    CurrencyDetail,
    Denomination,
    Disposition,
    GradingService,
    InventoryItem,
    ItemCertification,
    ItemKind,
    Metal,
)
from app.reports.base import among
from app.reports.id_numbers import CB_ID_NUMBERS
from app.reports.live_params import NOTHING_MATCHES, LiveParams
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def test_among_binds_one_parameter_however_many_values(db: Session) -> None:
    """More values than a statement may carry parameters, matched all the same.

    `IN (...)` binds one parameter per value, and psycopg refuses a statement
    of more than 65,535; `among` binds the list as one array.
    """
    item = build_bare_item(db)
    db.commit()
    many = [item.id, *range(-70_000, 0)]

    statement = select(InventoryItem.id).where(among(InventoryItem.id, many))
    assert len(statement.compile(dialect=postgresql.dialect()).params) == 1
    assert db.scalars(statement).all() == [item.id]

    codes = [item.item_code, *(f"NONE-{n}" for n in range(70_000))]
    by_code = select(InventoryItem.id).where(among(InventoryItem.item_code, codes))
    assert db.scalars(by_code).all() == [item.id]
    assert (
        db.scalars(select(InventoryItem.id).where(among(InventoryItem.id, []))).all()
        == []
    )


def _bullion(db: Session, serial: str | None, **over: object) -> InventoryItem:
    """A gold bar of 1 ozt fine, holding `serial`."""
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "bullion"),
        metal_id=code_id(db, Metal, "gold"),
        fine_weight_ozt=Decimal("1.000000"),
        description="1 oz gold bar.",
        **over,
    )
    db.add(CoinDetail(inventory_item_id=item.id, serial_number=serial))
    db.commit()
    return item


def _note(db: Session, serial: str) -> InventoryItem:
    """A $1 note holding `serial`."""
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        denomination_id=code_id(db, Denomination, "usd_note_1"),
    )
    db.add(CurrencyDetail(inventory_item_id=item.id, serial_number=serial))
    db.commit()
    return item


def _certify(db: Session, item: InventoryItem, *numbers: str) -> None:
    """Certificates by PCGS on `item`."""
    service = code_id(db, GradingService, "PCGS")
    db.add_all(
        ItemCertification(
            inventory_item_id=item.id, grading_service_id=service, cert_number=number
        )
        for number in numbers
    )
    db.commit()


def test_a_row_for_each_held_item_a_number_identifies(db: Session) -> None:
    bar = _bullion(db, "PAMP-0042")
    note = _note(db, "B12345678A")
    graded = build_bare_item(db)
    _certify(db, graded, "11111111", "22222222")
    build_bare_item(db)  # no number: not listed
    _bullion(db, None)  # a bar without a serial: not listed

    result = CB_ID_NUMBERS.run(db, LiveParams())

    by_item = {row["item"]: row for row in result.rows}
    assert list(by_item) == sorted([bar.item_code, note.item_code, graded.item_code])
    assert by_item[bar.item_code]["serial_number"] == "PAMP-0042"
    assert by_item[bar.item_code]["metal"] == "Gold"
    assert by_item[bar.item_code]["photo"] == "no"
    assert by_item[note.item_code]["serial_number"] == "B12345678A"
    assert by_item[graded.item_code]["cert_numbers"] == "11111111, 22222222"
    assert by_item[graded.item_code]["grading_service"] == "PCGS"
    assert result.totals is not None
    assert result.totals["item"] == "3 items"
    assert result.totals["fine_weight_ozt"] == Decimal("1.000000")
    # Each row opens the item it names.
    assert result.drills[[r["item"] for r in result.rows].index(bar.item_code)] == (
        f"/inventory/coins?item={bar.item_code}"
    )


def test_a_sold_piece_is_left_out_by_default(db: Session) -> None:
    sold = _bullion(db, "JM-77", disposition_id=code_id(db, Disposition, "sold"))

    assert CB_ID_NUMBERS.run(db, LiveParams()).notes == [NOTHING_MATCHES]
    every = CB_ID_NUMBERS.run(db, LiveParams(disposition="all"))
    assert [row["item"] for row in every.rows] == [sold.item_code]
