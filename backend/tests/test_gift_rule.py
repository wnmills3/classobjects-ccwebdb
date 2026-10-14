"""Gift: the attribute a banknote saved at no cost is given.

docs/specs/item-attributes-design.md, section 2, "Attributes from facts: Gift".
Everything goes through the editor's own routes.
"""

from __future__ import annotations

import itertools

from app import gift_rule
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_purchase_order, patch_current

Headers = dict[str, str]

#: A vendor's name is unique, and one test enters several notes.
_NUMBER = itertools.count(1)


def _enter(
    client: TestClient, headers: Headers, db: Session, **overrides: object
) -> dict:
    """A 1957B $1 Silver Certificate entered at no cost, overridden."""
    payload: dict[str, object] = {
        "purchase_order_id": build_purchase_order(
            db, vendor_name=f"Gift Vendor {next(_NUMBER)}"
        ).id,
        "item_kind": "currency",
        "source_title": "$1 Bill",
        "item_cost": "0.00",
        "denomination": "usd_note_1",
        "note_type": "silver_certificate",
        "series_year": 1957,
        "series_letter": "B",
        "seal_color": "blue",
        "serial_number": "A38164927B",
    }
    payload.update(overrides)
    made = _make(client, headers, payload)
    # The editor's first save, changing nothing: what enters the note.
    return _save(client, headers, made, source_title=payload["source_title"])


def _make(client: TestClient, headers: Headers, payload: dict[str, object]) -> dict:
    """The row a new item starts as, before the editor has saved it."""
    res = client.post("/api/inventory", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _save(client: TestClient, headers: Headers, item: dict, **fields: object) -> dict:
    """The item after an edit sending these fields."""
    res = patch_current(
        client, f"/api/inventory/{item['id']}", json=fields, headers=headers
    )
    assert res.status_code == 200, res.text
    return client.get(f"/api/inventory/{item['id']}", headers=headers).json()


def _gift(item: dict) -> dict | None:
    """The item's Gift as its detail lists it, or None."""
    return next((a for a in item["attributes"] if a["code"] == gift_rule.GIFT), None)


def test_a_note_entered_at_no_cost_is_a_gift_and_described_as_one(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    item = _enter(client, admin_headers, db)
    assert _gift(item) == {
        "code": "gift",
        "label": "Gift",
        "group": "variety",
        "source": "derived",
        "derived_by": "no_cost",
    }
    assert item["description"] == (
        "Gift 1957B $1 S/N A38164927B. Silver Certificate Blue Seal."
    )


def test_a_row_just_made_is_no_gift_yet_and_has_no_description(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    """Its cost has not been typed: $0 here means nothing was entered."""
    made = _make(
        client,
        admin_headers,
        {
            "purchase_order_id": build_purchase_order(
                db, vendor_name="Unsaved Vendor"
            ).id,
            "item_kind": "currency",
            "source_title": "Gift $1 Bill",
            "series_year": 1957,
        },
    )
    assert made["item_cost"] == "0.00"
    assert _gift(made) is None
    assert made["description"] == ""
    assert made["source_title"] == "Gift $1 Bill"


def test_a_note_entered_at_a_cost_is_no_gift(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    item = _enter(client, admin_headers, db, item_cost="20.00")
    assert _gift(item) is None
    assert item["description"] == ""


def test_a_coin_entered_at_no_cost_is_no_gift(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    made = _make(
        client,
        admin_headers,
        {
            "purchase_order_id": build_purchase_order(db, vendor_name="Coin Gift").id,
            "item_kind": "coin",
            "source_title": "Gift 1881-S Morgan Dollar",
            "item_cost": "0.00",
            "year_start": 1881,
        },
    )
    # The editor's save is what runs the rule, so that is what must leave it.
    coin = _save(client, admin_headers, made, source_title=made["source_title"])
    assert coin["item_cost"] == "0.00"
    assert _gift(coin) is None
    assert coin["source_title"] == "Gift 1881-S Morgan Dollar"


def test_the_word_leaves_the_title_of_a_gift(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    for title, kept in (
        ("Gift $1 Bill Green Seal", "$1 Bill Green Seal"),
        ("$2 Bill Gift", "$2 Bill"),
        ("$1 Bill - GIFT - Trinary", "$1 Bill Trinary"),
        ("Gifted $1 Bill", "Gifted $1 Bill"),
        ("Gift", "Gift"),
    ):
        item = _enter(client, admin_headers, db, source_title=title)
        assert item["source_title"] == kept


def test_the_word_stays_in_the_title_of_a_note_that_cost_something(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    item = _enter(
        client, admin_headers, db, item_cost="5.00", source_title="Gift $1 Bill"
    )
    assert item["source_title"] == "Gift $1 Bill"


def test_a_persons_own_description_is_kept(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    item = _enter(client, admin_headers, db, description="Freebie from the show")
    assert _gift(item) is not None
    assert item["description"] == "Freebie from the show"


def test_a_note_edited_down_to_no_cost_becomes_a_gift_and_the_log_says_so(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    item = _enter(
        client, admin_headers, db, item_cost="20.00", source_title="Gift $1 Bill"
    )
    saved = _save(client, admin_headers, item, item_cost="0.00")
    assert _gift(saved) is not None
    assert saved["source_title"] == "$1 Bill"
    assert saved["description"].startswith("Gift 1957B $1")
    assert saved["version"] > item["version"]

    history = client.get(
        f"/api/inventory/{item['id']}/history", headers=admin_headers
    ).json()
    logged = {e["field"]: e["new_value"] for e in history if e["kind"] == "field"}
    assert logged["attributes"] == ["Gift"]
    assert logged["source_title"] == "$1 Bill"
    assert logged["description"] == saved["description"]


def test_the_rule_takes_its_own_gift_back_when_the_note_gets_a_cost(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    item = _enter(client, admin_headers, db)
    saved = _save(client, admin_headers, item, item_cost="12.00")
    assert _gift(saved) is None
    assert saved["description"] == (
        "1957B $1 S/N A38164927B. Silver Certificate Blue Seal."
    )


def test_a_gift_a_person_set_stays_when_the_note_gets_a_cost(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    item = _enter(client, admin_headers, db, attributes=["gift"])
    assert (_gift(item) or {})["source"] == "manual"
    saved = _save(client, admin_headers, item, item_cost="12.00")
    assert (_gift(saved) or {})["source"] == "manual"


def test_a_gift_a_person_removed_stays_removed(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    item = _enter(client, admin_headers, db)
    removed = _save(client, admin_headers, item, attributes=[])
    assert _gift(removed) is None
    again = _save(client, admin_headers, removed, source_title="Gift $1 Bill")
    assert _gift(again) is None
    assert again["source_title"] == "Gift $1 Bill"


def test_suggest_describes_a_note_at_no_cost_as_the_gift_save_will_make_it(
    client: TestClient, admin_headers: Headers, db: Session
) -> None:
    item = _enter(client, admin_headers, db, item_cost="20.00")
    res = client.post(
        f"/api/inventory/{item['id']}/suggested-description",
        json={"changes": {"item_cost": "0.00", "attributes": []}},
        headers=admin_headers,
    )
    assert res.status_code == 200, res.text
    assert res.json()["description"].startswith("Gift 1957B $1")
    # Nothing was written.
    after = client.get(f"/api/inventory/{item['id']}", headers=admin_headers).json()
    assert _gift(after) is None
