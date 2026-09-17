"""Item attributes: coins and notes, derived and set by hand, and removal.

docs/specs/item-attributes-design.md, section 2. The attribute vocabulary is
seeded, so these read it.
"""

from __future__ import annotations

from collections.abc import Callable

from app import aliases
from app.importers.engine import COMMIT, ImportEngine
from app.importers.models import ImportRow
from app.importers.profiles.collection_v1 import CollectionV1Profile
from app.inventory_search import COIN_VIEW, CURRENCY_VIEW, search
from app.models import (
    CurrencyDetail,
    InventoryItem,
    ItemAttribute,
    ItemAttributeLink,
    ProvenanceSource,
)
from app.serial_patterns import run as run_serial_patterns
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_importer import FakeSource, make_row

ItemFactory = Callable[..., InventoryItem]

#: A star and nothing else: no ladder, repeater or fancy digits.
STAR_SERIAL = "A38164927*"


def _attribute(db: Session, code: str) -> ItemAttribute:
    return db.execute(
        select(ItemAttribute).where(ItemAttribute.code == code)
    ).scalar_one()


def _note(
    db: Session, make_item: ItemFactory, serial: str | None = None
) -> InventoryItem:
    note = make_item(
        kind="currency", description="plain", grade_id=None, strike_type_id=None
    )
    db.add(CurrencyDetail(inventory_item_id=note.id, serial_number=serial))
    db.commit()
    return note


def _coin(make_item: ItemFactory) -> InventoryItem:
    return make_item(description="plain")


def _link(db: Session, item: InventoryItem, code: str) -> ItemAttributeLink | None:
    return db.scalar(
        select(ItemAttributeLink).where(
            ItemAttributeLink.inventory_item_id == item.id,
            ItemAttributeLink.item_attribute_id == _attribute(db, code).id,
        )
    )


def _detail(client: TestClient, headers: dict[str, str], item: InventoryItem) -> dict:
    return client.get(f"/api/inventory/{item.id}", headers=headers).json()


def _set(
    client: TestClient,
    headers: dict[str, str],
    item: InventoryItem,
    codes: list[str] | None,
    **extra: object,
) -> Response:
    return client.patch(
        f"/api/inventory/{item.id}",
        json={"attributes": codes, **extra},
        headers=headers,
    )


def _codes(body: dict) -> list[str]:
    return [a["code"] for a in body["attributes"]]


# --- the vocabulary ---------------------------------------------------------------


def test_the_note_attributes_kept_their_codes_and_gained_a_group(db: Session) -> None:
    star = _attribute(db, "star")
    assert (star.label, star.applies_to.value, star.attribute_group.value) == (
        "Star Note",
        "currency",
        "serial",
    )
    assert _attribute(db, "web_press").attribute_group.value == "variety"


def test_coins_have_attributes_too(db: Session) -> None:
    for code, applies_to, group in (
        ("first_strike", "coin", "release"),
        ("cac", "coin", "verification"),
        ("no_motto", "any", "variety"),
        ("genuine", "any", "qualifier"),
    ):
        row = _attribute(db, code)
        assert (row.applies_to.value, row.attribute_group.value) == (applies_to, group)


def test_the_owners_words_are_aliases(db: Session) -> None:
    no_motto = _attribute(db, "no_motto").id
    assert aliases.resolve(db, ItemAttribute, "No God") == aliases.Resolved(
        no_motto, "alias"
    )
    assert aliases.resolve(db, ItemAttribute, "godless") == aliases.Resolved(
        no_motto, "alias"
    )


# --- reading ----------------------------------------------------------------------


def test_the_detail_lists_attributes_with_where_they_came_from(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    note = _note(db, make_item, STAR_SERIAL)
    run_serial_patterns(db, commit=True)

    body = _detail(client, admin_headers, note)
    assert body["attributes"] == [
        {
            "code": "star",
            "label": "Star Note",
            "group": "serial",
            "source": "derived",
            "derived_by": "serial_pattern",
        }
    ]


def test_the_importer_records_what_it_read_as_derived(db: Session) -> None:
    row = make_row(2, Denom="$1 Bill", Year="1957", Rating="Blue Seal Star Note")
    report = ImportEngine(CollectionV1Profile(), session=db).run(
        FakeSource([row]), mode=COMMIT
    )
    staged = db.query(ImportRow).filter(ImportRow.batch_id == report.batch_id).one()
    link = db.scalar(
        select(ItemAttributeLink).where(
            ItemAttributeLink.inventory_item_id == staged.inventory_item_id
        )
    )
    assert link is not None
    assert (link.source, link.derived_by) == (ProvenanceSource.derived, "import")


# --- setting ----------------------------------------------------------------------


def test_a_person_sets_a_coins_attributes(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
    admin_user: object,
) -> None:
    coin = _coin(make_item)
    before = _detail(client, admin_headers, coin)["version"]

    response = _set(
        client,
        admin_headers,
        coin,
        ["first_strike", "cac"],
        version=before,
    )
    assert response.status_code == 200
    body = _detail(client, admin_headers, coin)
    # Vocabulary order, not the order sent.
    assert _codes(body) == ["first_strike", "cac"]
    assert {a["source"] for a in body["attributes"]} == {"manual"}
    link = _link(db, coin, "cac")
    assert link is not None and link.noted_by_id == admin_user.id  # type: ignore[attr-defined]
    # Only the links changed, and the item's version still moved.
    assert body["version"] == before + 1


def test_a_stale_form_cannot_put_the_old_attributes_back(
    make_item: ItemFactory, client: TestClient, admin_headers: dict[str, str]
) -> None:
    coin = _coin(make_item)
    version = _detail(client, admin_headers, coin)["version"]
    assert (
        _set(client, admin_headers, coin, ["cac"], version=version).status_code == 200
    )

    stale = _set(client, admin_headers, coin, [], version=version)
    assert stale.status_code == 409
    assert _codes(_detail(client, admin_headers, coin)) == ["cac"]


def test_saving_the_same_attributes_changes_nothing(
    make_item: ItemFactory, client: TestClient, admin_headers: dict[str, str]
) -> None:
    coin = _coin(make_item)
    _set(client, admin_headers, coin, ["cac"])
    version = _detail(client, admin_headers, coin)["version"]
    _set(client, admin_headers, coin, ["cac"])
    assert _detail(client, admin_headers, coin)["version"] == version


def test_a_removed_derived_attribute_stays_removed(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    note = _note(db, make_item, STAR_SERIAL)
    run_serial_patterns(db, commit=True)

    assert _set(client, admin_headers, note, []).status_code == 200
    stats, _ = run_serial_patterns(db, commit=True)

    assert stats["already:star"] == 1
    assert "written" not in stats
    assert _codes(_detail(client, admin_headers, note)) == []
    link = _link(db, note, "star")
    assert link is not None and link.removed_at is not None
    rows, _ = search(db, CURRENCY_VIEW, params={}, query="star note")
    assert note.item_code not in {r["item_code"] for r in rows}
    rows, _ = search(db, CURRENCY_VIEW, params={"attribute": "star"})
    assert note.item_code not in {r["item_code"] for r in rows}


def test_setting_a_removed_attribute_again_restores_it_as_it_was(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    note = _note(db, make_item, STAR_SERIAL)
    run_serial_patterns(db, commit=True)
    _set(client, admin_headers, note, [])
    _set(client, admin_headers, note, ["star"])

    body = _detail(client, admin_headers, note)
    assert [(a["code"], a["source"]) for a in body["attributes"]] == [
        ("star", "derived")
    ]
    # And removing it a second time still holds.
    _set(client, admin_headers, note, [])
    run_serial_patterns(db, commit=True)
    assert _codes(_detail(client, admin_headers, note)) == []


def test_an_attribute_a_person_added_and_removed_is_not_derived_back(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    note = _note(db, make_item, STAR_SERIAL)
    _set(client, admin_headers, note, ["star"])
    _set(client, admin_headers, note, [])
    run_serial_patterns(db, commit=True)
    assert _codes(_detail(client, admin_headers, note)) == []


def test_an_attribute_for_the_other_kind_of_item_is_refused(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    coin = _coin(make_item)
    note = _note(db, make_item)

    on_coin = _set(client, admin_headers, coin, ["cac", "star"])
    assert on_coin.status_code == 422
    assert "Not an attribute of a coin: ['star']" in on_coin.json()["detail"]
    on_note = _set(client, admin_headers, note, ["first_strike"])
    assert on_note.status_code == 422
    # Nothing half-applied.
    assert _codes(_detail(client, admin_headers, coin)) == []
    # "any" fits both.
    assert _set(client, admin_headers, coin, ["no_motto"]).status_code == 200
    assert _set(client, admin_headers, note, ["no_motto"]).status_code == 200


def test_a_kind_changed_in_the_same_save_decides_what_fits(
    make_item: ItemFactory, client: TestClient, admin_headers: dict[str, str]
) -> None:
    coin = _coin(make_item)
    response = _set(client, admin_headers, coin, ["star"], item_kind="currency")
    assert response.status_code == 200
    assert _codes(_detail(client, admin_headers, coin)) == ["star"]


def test_nonsense_attributes_are_refused(
    make_item: ItemFactory, client: TestClient, admin_headers: dict[str, str]
) -> None:
    coin = _coin(make_item)
    assert _set(client, admin_headers, coin, ["no_such"]).status_code == 422
    assert _set(client, admin_headers, coin, None).status_code == 422
    assert _set(client, admin_headers, coin, ["cac", "cac"]).status_code == 422


def test_bulk_edit_does_not_set_attributes(
    make_item: ItemFactory, client: TestClient, admin_headers: dict[str, str]
) -> None:
    coin = _coin(make_item)
    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [coin.id], "changes": {"attributes": ["cac"]}},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "one item at a time" in response.json()["detail"]


# --- search -----------------------------------------------------------------------


def test_search_finds_an_item_by_attribute_name_alias_or_filter(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    coin = _coin(make_item)
    note = _note(db, make_item)
    _set(client, admin_headers, coin, ["first_strike"])
    _set(client, admin_headers, note, ["no_motto"])

    def found(view: object, **kwargs: object) -> set[str]:
        rows, _ = search(db, view, **kwargs)  # type: ignore[arg-type]
        return {r["item_code"] for r in rows}

    assert coin.item_code in found(COIN_VIEW, params={}, query="first strike")
    assert found(COIN_VIEW, params={"attribute": "first_strike"}) == {coin.item_code}
    assert note.item_code in found(CURRENCY_VIEW, params={}, query="godless")
    assert found(CURRENCY_VIEW, params={"attribute": "no_motto"}) == {note.item_code}


def test_a_removed_star_no_longer_disagrees_with_a_plain_serial(
    db: Session,
    make_item: ItemFactory,
    client: TestClient,
    admin_headers: dict[str, str],
) -> None:
    note = _note(db, make_item, "A38164927B")

    def flagged() -> bool:
        rows, _ = search(db, CURRENCY_VIEW, params={"issue": "star_mismatch"})
        return note.item_code in {r["item_code"] for r in rows}

    _set(client, admin_headers, note, ["star"])
    assert flagged()
    _set(client, admin_headers, note, [])
    assert not flagged()
