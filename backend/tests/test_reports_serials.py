"""`cb_serials`: notes whose serial numbers match one another.

Every serial here is made up.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import (
    CurrencyDetail,
    Denomination,
    InventoryItem,
    ItemKind,
    ItemStatus,
)
from app.reports.serials import CB_SERIALS, NO_MATCHES, SerialsParams
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def _note(
    db: Session, serial: str | None, denomination: str = "usd_note_1", **over: object
) -> InventoryItem:
    """A note of this serial and denomination."""
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        denomination_id=code_id(db, Denomination, denomination),
        **over,
    )
    db.add(CurrencyDetail(inventory_item_id=item.id, serial_number=serial))
    db.commit()
    db.refresh(item)
    return item


def _sets(db: Session, **params: str) -> list[set[str]]:
    """The report's sets, each as its item codes, in the order shown."""
    result = CB_SERIALS.run(db, SerialsParams.model_validate(params))
    sets: list[set[str]] = []
    at = 0
    while at < len(result.rows):
        size = result.rows[at]["notes"]
        assert isinstance(size, int)
        members = result.rows[at : at + size]
        # A set is consecutive rows, each saying how many it has.
        assert {row["notes"] for row in members} == {size}
        assert len({row["digits"] for row in members}) == 1
        sets.append({str(row["item"]) for row in members})
        at += size
    return sets


def test_by_default_the_digits_alone_are_matched(db: Session) -> None:
    """Any letters, any denomination, star or not; a lone serial is no set."""
    a = _note(db, "B12345678A")
    b = _note(db, "f 1234 5678 c", "usd_note_5")
    star = _note(db, "G12345678*")
    _note(db, "B12345679A")
    _note(db, None)
    _note(db, "no digits")

    assert _sets(db) == [{a.item_code, b.item_code, star.item_code}]

    result = CB_SERIALS.run(db, SerialsParams())
    assert result.notes == ["1 sets, 3 notes."]
    assert result.link_column == "item"
    first = result.rows[0]
    assert first["digits"] == "12345678"
    assert first["notes"] == 3
    assert first["serial"] == "B12345678A"
    assert first["star"] == ""
    assert "$1" in str(first["denomination"])
    assert result.rows[2]["star"] == "Star"
    assert result.drills[0] == f"/inventory/currency?item={a.item_code}"


def test_the_denomination_can_be_made_to_match(db: Session) -> None:
    """Then a $1 and a $5 of one serial are not a set; two $5s are."""
    _note(db, "B12345678A")
    five = _note(db, "C12345678D", "usd_note_5")
    other = _note(db, "E12345678F", "usd_note_5")

    assert _sets(db, denomination="must_match") == [{five.item_code, other.item_code}]


def test_the_letters_can_be_made_to_match(db: Session) -> None:
    """Then only the same serial is a match, however it was typed."""
    a = _note(db, "B12345678A")
    b = _note(db, "b 1234 5678 a")
    _note(db, "B12345678C")
    _note(db, "F12345678A")

    assert _sets(db, letters="must_match", stars="only_other_stars") == [
        {a.item_code, b.item_code}
    ]


def test_star_notes_can_be_kept_to_themselves(db: Session) -> None:
    """Then a star note matches a star note and a plain note a plain one."""
    plain = _note(db, "B12345678A")
    other = _note(db, "C12345678D")
    star = _note(db, "F12345678*")
    front = _note(db, "*12345678G")
    _note(db, "H87654321*")
    _note(db, "H87654321J")

    assert _sets(db, stars="only_other_stars") == [
        {plain.item_code, other.item_code},
        {star.item_code, front.item_code},
    ]


def test_with_letters_a_star_stands_where_a_letter_would(db: Session) -> None:
    """A star note matches the plain notes that agree on the letters it has.

    Two plain notes differing in one letter are not matched by that alone:
    only the star note brings them into a set.
    """
    star = _note(db, "F12345678*")
    h = _note(db, "F12345678H")
    j = _note(db, "F12345678J")
    _note(db, "G12345678H")
    # No star note of these: one letter apart, and no set.
    _note(db, "B87654321A")
    _note(db, "B87654321C")
    front = _note(db, "*11112222K")
    k = _note(db, "L11112222K")
    _note(db, "L11112222M")

    assert _sets(db, letters="must_match") == [
        {front.item_code, k.item_code},
        {star.item_code, h.item_code, j.item_code},
    ]


def test_only_live_notes_of_the_status_asked_for_are_matched(db: Session) -> None:
    """A deleted note is no half of a set, and nor is one not yet received."""
    held = _note(db, "B12345678A")
    _note(db, "C12345678D", deleted_at=datetime.now(UTC))
    ordered = _note(db, "E12345678F", status_id=code_id(db, ItemStatus, "ordered"))

    result = CB_SERIALS.run(db, SerialsParams())

    assert result.rows == []
    assert result.notes == [NO_MATCHES]
    assert _sets(db, status="all") == [{held.item_code, ordered.item_code}]


def test_the_report_is_in_the_catalog_with_its_choices(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The form offers the three options as choices, widest first."""
    catalog = client.get("/api/reports", headers=admin_headers).json()
    entry = next(e for e in catalog if e["id"] == "cb_serials")

    assert entry["group"] == "Collection"
    assert entry["title"] == "Matching serial numbers"
    options = {
        p["name"]: (p["label"], p["default"], p["choices"]) for p in entry["params"]
    }
    assert options["denomination"] == ("Denomination", "any", ["any", "must_match"])
    assert options["letters"] == ("Letters", "ignore", ["ignore", "must_match"])
    assert options["stars"] == (
        "Star notes",
        "match_plain_notes",
        ["match_plain_notes", "only_other_stars"],
    )

    ran = client.get(
        "/api/reports/cb_serials?letters=must_match", headers=admin_headers
    )
    assert ran.status_code == 200, ran.text
    refused = client.get("/api/reports/cb_serials?letters=some", headers=admin_headers)
    assert refused.status_code == 422, refused.text
