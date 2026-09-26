"""A kind change through the item edit takes the item's detail row with it.

`app.item_kinds`: an item made a banknote loses its coin row and gains a note
row, so its note fields can be sent in the same request; one leaving banknote
is refused while its note row still holds a value.
"""

from __future__ import annotations

from app.models import (
    CoinDetail,
    CurrencyDetail,
    Denomination,
    FriedbergNumber,
    InventoryItem,
    ItemKind,
    Mint,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def test_editing_a_coin_into_a_note_gives_it_a_note_row(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db)
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
    item = build_bare_item(
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
