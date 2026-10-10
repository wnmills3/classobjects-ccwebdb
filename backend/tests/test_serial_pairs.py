"""A note and the star note of the same serial number, found for each other.

Every serial here is made up.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import CurrencyDetail, Denomination, InventoryItem, ItemKind
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def _note(db: Session, serial: str | None, **overrides: object) -> InventoryItem:
    """A $1 note with this serial."""
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        denomination_id=code_id(db, Denomination, "usd_note_1"),
        **overrides,
    )
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id, serial_number=serial, series_year=2017
        )
    )
    db.commit()
    db.refresh(item)
    return item


def _pairs(
    client: TestClient, headers: dict[str, str], item: InventoryItem, **params: str
) -> list[dict[str, object]]:
    """The notes the endpoint pairs with `item`."""
    resp = client.get(
        f"/api/inventory/{item.id}/serial-pairs", params=params, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body: list[dict[str, object]] = resp.json()
    return body


def test_a_note_finds_the_star_note_of_its_serial(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The same eight digits with a star, whatever the letters round them.

    Not another plain note of those digits, not a star note of other digits,
    and not the note itself.
    """
    plain = _note(db, "B12345678A")
    star = _note(db, "F12345678*")
    front_star = _note(db, "*12345678A")
    _note(db, "C12345678D")
    _note(db, "B12345679*")

    found = _pairs(client, admin_headers, plain)

    assert {row["id"] for row in found} == {star.id, front_star.id}
    by_id = {row["id"]: row for row in found}
    assert by_id[star.id]["item_code"] == star.item_code
    assert by_id[star.id]["serial_number"] == "F12345678*"
    assert by_id[star.id]["series"] == "2017"
    assert "$1" in str(by_id[star.id]["denomination"])


def test_a_star_note_finds_the_note_of_its_serial(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Asked from the star note, the plain notes are what match."""
    plain = _note(db, "b 1234 5678 a")
    star = _note(db, "F12345678*")
    _note(db, "G12345678*")

    found = _pairs(client, admin_headers, star)

    assert [row["id"] for row in found] == [plain.id]


def test_the_serial_being_typed_is_what_is_matched(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A serial sent is used in place of the one saved: checked before a save."""
    blank = _note(db, None)
    star = _note(db, "F12345678*")

    assert _pairs(client, admin_headers, blank) == []
    typed = _pairs(client, admin_headers, blank, serial_number=" b12345678a ")
    assert [row["id"] for row in typed] == [star.id]
    # Typed as the star note it is, the same record is no longer its pair.
    assert _pairs(client, admin_headers, blank, serial_number="B12345678*") == []


def test_a_serial_not_eight_digits_long_matches_nothing(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A serial cut short would match every note cut short the same way."""
    short = _note(db, "B1234567A")
    _note(db, "F1234567*")
    long = _note(db, "B123456789A")
    _note(db, "F123456789*")

    assert _pairs(client, admin_headers, short) == []
    assert _pairs(client, admin_headers, long) == []


def test_a_deleted_or_split_note_is_not_offered(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Only a note still in the collection is a match to go and find."""
    plain = _note(db, "B12345678A")
    _note(db, "F12345678*", deleted_at=datetime.now(UTC))
    _note(db, "G12345678*", split_at=datetime.now(UTC))
    kept = _note(db, "H12345678*")

    assert [row["id"] for row in _pairs(client, admin_headers, plain)] == [kept.id]


def test_a_coin_has_no_serial_to_pair(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """An item with no note detail pairs with nothing; a missing one is a 404."""
    coin = build_bare_item(db)
    _note(db, "F12345678*")

    assert _pairs(client, admin_headers, coin) == []
    missing = client.get("/api/inventory/999999/serial-pairs", headers=admin_headers)
    assert missing.status_code == 404, missing.text


def test_pairs_are_for_staff(client: TestClient, db: Session) -> None:
    """Signed out, the collection's serials are not listed."""
    plain = _note(db, "B12345678A")
    resp = client.get(f"/api/inventory/{plain.id}/serial-pairs")
    assert resp.status_code == 401, resp.text
