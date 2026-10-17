"""A bullion item's serial number, on its coin detail row.

A bar, round or Goldback may carry a serial unique to the piece. It is the
same API field as a note's `serial_number`, routed by the item's resulting
kind: to `coin_detail` for bullion, to `currency_detail` for a note. Another
kind refuses it, and no kind change drops one silently.
"""

from __future__ import annotations

from typing import Any

from app.models import (
    CoinDetail,
    CurrencyDetail,
    Denomination,
    InventoryItem,
    ItemFieldChange,
    ItemKind,
)
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id, patch_current


def _patch(
    client: TestClient, headers: dict[str, str], item_id: int, body: dict[str, Any]
) -> Response:
    """The response to saving these fields on an item."""
    return patch_current(
        client, f"/api/inventory/{item_id}", json=body, headers=headers
    )


def _bullion(db: Session, serial: str | None = None) -> InventoryItem:
    """A bullion item with its coin detail row, holding `serial`."""
    item = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "bullion"))
    db.add(CoinDetail(inventory_item_id=item.id, serial_number=serial))
    db.commit()
    return item


def _note(db: Session, serial: str) -> InventoryItem:
    """A $1 note holding only a serial number."""
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        denomination_id=code_id(db, Denomination, "usd_note_1"),
        year_start=None,
    )
    db.add(CurrencyDetail(inventory_item_id=item.id, serial_number=serial))
    db.commit()
    return item


def test_a_bullion_item_takes_a_serial_kept_as_written(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bar = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "bullion"))

    response = _patch(client, admin_headers, bar.id, {"serial_number": " 007a-123 "})

    assert response.status_code == 200, response.text
    db.expire_all()
    # Stored as sent, as a note's serial is: case and leading zeros are kept.
    assert db.get_one(CoinDetail, bar.id).serial_number == " 007a-123 "
    shown = client.get(f"/api/inventory/{bar.id}", headers=admin_headers).json()
    assert shown["serial_number"] == " 007a-123 "


def test_a_bullion_serial_change_is_logged_and_moves_the_version(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bar = _bullion(db, "A100")
    version = bar.version

    assert (
        _patch(client, admin_headers, bar.id, {"serial_number": "A101"}).status_code
        == 200
    )

    db.expire_all()
    assert db.get_one(InventoryItem, bar.id).version != version
    logged = db.scalars(
        select(ItemFieldChange).where(
            ItemFieldChange.inventory_item_id == bar.id,
            ItemFieldChange.field_name == "serial_number",
        )
    ).all()
    assert [(c.old_value, c.new_value) for c in logged] == [("A100", "A101")]


def test_a_bullion_serial_sent_unchanged_moves_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bar = _bullion(db, "A100")
    version = bar.version

    assert (
        _patch(client, admin_headers, bar.id, {"serial_number": "A100"}).status_code
        == 200
    )

    db.expire_all()
    assert db.get_one(InventoryItem, bar.id).version == version


def test_a_bullion_serial_sent_blank_is_none(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bar = _bullion(db, "A100")

    assert (
        _patch(client, admin_headers, bar.id, {"serial_number": "   "}).status_code
        == 200
    )

    db.expire_all()
    assert db.get_one(CoinDetail, bar.id).serial_number is None


def test_a_coin_refuses_a_serial_number(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = build_bare_item(db)

    response = _patch(client, admin_headers, coin.id, {"serial_number": "A100"})

    assert response.status_code == 422, response.text
    assert "serial_number" in response.json()["detail"]
    db.expire_all()
    detail = db.get(CoinDetail, coin.id)
    assert detail is None or detail.serial_number is None


def test_a_note_made_bullion_keeps_its_serial(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _note(db, "M88888888")

    response = _patch(
        client,
        admin_headers,
        item.id,
        {"item_kind": "bullion", "denomination": None},
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(CurrencyDetail, item.id) is None
    assert db.get_one(CoinDetail, item.id).serial_number == "M88888888"


def test_a_note_made_bullion_takes_the_serial_sent_with_it(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _note(db, "M88888888")

    response = _patch(
        client,
        admin_headers,
        item.id,
        {"item_kind": "bullion", "denomination": None, "serial_number": "B2"},
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get_one(CoinDetail, item.id).serial_number == "B2"


def test_bullion_made_a_note_keeps_its_serial(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bar = _bullion(db, "GB-0042")

    response = _patch(
        client,
        admin_headers,
        bar.id,
        {"item_kind": "currency", "denomination": "usd_note_1", "year_start": None},
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(CoinDetail, bar.id) is None
    assert db.get_one(CurrencyDetail, bar.id).serial_number == "GB-0042"


def test_bullion_made_a_coin_is_refused_while_it_holds_a_serial(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bar = _bullion(db, "A100")

    refused = _patch(client, admin_headers, bar.id, {"item_kind": "coin"})
    assert refused.status_code == 422, refused.text
    assert "serial_number" in refused.json()["detail"]
    db.expire_all()
    assert db.get_one(InventoryItem, bar.id).item_kind_id == code_id(
        db, ItemKind, "bullion"
    )
    assert db.get_one(CoinDetail, bar.id).serial_number == "A100"

    # Cleared in the same request, the change goes through.
    cleared = _patch(
        client, admin_headers, bar.id, {"item_kind": "coin", "serial_number": None}
    )
    assert cleared.status_code == 200, cleared.text
    db.expire_all()
    assert db.get_one(CoinDetail, bar.id).serial_number is None


def test_bullion_made_a_coin_may_not_bring_a_new_serial(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bar = _bullion(db)

    response = _patch(
        client, admin_headers, bar.id, {"item_kind": "coin", "serial_number": "A1"}
    )

    assert response.status_code == 422, response.text
    assert "serial_number" in response.json()["detail"]


def test_a_coin_made_bullion_takes_a_serial_in_the_same_request(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = build_bare_item(db)

    response = _patch(
        client, admin_headers, coin.id, {"item_kind": "bullion", "serial_number": "Z9"}
    )

    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get_one(CoinDetail, coin.id).serial_number == "Z9"


def test_the_bulk_edit_refuses_a_bullion_serial(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    bar = _bullion(db)

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [bar.id], "changes": {"serial_number": "A1"}},
        headers=admin_headers,
    )

    assert response.status_code == 422, response.text
    db.expire_all()
    assert db.get_one(CoinDetail, bar.id).serial_number is None
