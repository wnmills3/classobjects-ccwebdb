"""Editing an item that is not for sale.

Which is all of them: the collection has thousands of items and no
listings, so before this endpoint existed there was no way to correct any of
them through the API.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import (
    CurrencyDetail,
    Denomination,
    Grade,
    ItemKind,
    Metal,
    SealColor,
    Series,
    StrikeType,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def test_an_unlisted_item_can_be_edited(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db, source_title="wrong", year_start=1878)

    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "1881-S Morgan Silver Dollar", "year_start": 1881},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_title"] == "1881-S Morgan Silver Dollar"
    assert body["year_start"] == 1881


def test_a_classifier_is_set_by_code(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Codes, never ids -- an id is meaningless to a client and unstable."""
    item = build_bare_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"grade": "MS63"}, headers=admin_headers
    )

    assert response.status_code == 200
    db.refresh(item)
    assert item.grade_id is not None


def test_an_unknown_code_is_refused_naming_the_field(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A silently null column is how 185 junk grades got in once already."""
    item = build_bare_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"grade": "NOT_A_GRADE"},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "grade" in response.json()["detail"]


def test_a_stale_version_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Two staff, one loaded form each; the second must not silently win."""
    item = build_bare_item(db)
    stale = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()[
        "version"
    ]

    first = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "careful", "version": stale},
        headers=admin_headers,
    )
    assert first.status_code == 200

    second = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "clobbering", "version": stale},
        headers=admin_headers,
    )
    assert second.status_code == 409

    db.refresh(item)
    assert item.source_title == "careful"


def test_omitting_the_version_edits_unconditionally(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A script that means 'set this regardless' can say so."""
    item = build_bare_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "no version sent"},
        headers=admin_headers,
    )
    assert response.status_code == 200


def test_an_omitted_field_is_left_alone(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """exclude_unset, so a partial form does not null everything it omits."""
    item = build_bare_item(db, source_title="keep me", year_start=1921)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"year_start": 1922}, headers=admin_headers
    )

    assert response.status_code == 200
    db.refresh(item)
    assert item.source_title == "keep me"


def test_a_customer_cannot_edit_inventory(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    """Everything on this router exposes cost basis."""
    item = build_bare_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "nope"},
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_money_survives_a_round_trip_as_a_string(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A Decimal that becomes a float has lost the guarantee it was for."""
    item = build_bare_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"item_cost": "19.99"},
        headers=admin_headers,
    )
    assert response.json()["item_cost"] == "19.99"
    db.refresh(item)
    assert item.item_cost == Decimal("19.99")


def test_a_piece_reports_what_its_lot_claimed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """So it is always visible what is being overridden.

    And what is still only the seller's word about the lot.
    """
    from tests.builders import TUBE, build_split_lot, do_split

    parent = build_split_lot(db, year_start=1881)
    piece_id = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"][0][
        "id"
    ]

    body = client.get(f"/api/inventory/{piece_id}", headers=admin_headers).json()

    assert body["parent_item_code"] == parent.item_code
    assert body["lot_claims"]["year_start"] == 1881


def test_an_item_with_no_parent_claims_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """No parent is the normal case, not an error -- every item starts that way."""
    item = build_bare_item(db)
    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()

    assert body["parent_item_code"] is None
    assert body["lot_claims"] == {}


def test_the_detail_carries_what_has_been_reviewed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """One round trip for the edit form, not two."""
    item = build_bare_item(db)
    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )

    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert body["reviewed"] == ["grade_id"]


def test_a_piece_reports_the_lot_s_claim_even_when_it_still_agrees(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The agreeing case is the important one, not the boring one.

    Fifty Morgans split out of one lot all read grade BU because they
    inherited the seller's claim, not because anyone graded them. A form that
    showed the lot's value only where the piece already differs would stay
    silent on exactly the fields that need the warning.
    """
    from tests.builders import TUBE, build_split_lot, do_split

    parent = build_split_lot(db, year_start=1881)
    piece_id = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"][0][
        "id"
    ]

    body = client.get(f"/api/inventory/{piece_id}", headers=admin_headers).json()

    assert body["year_start"] == 1881, "the piece inherited the lot's year"
    assert body["lot_claims"]["year_start"] == 1881, (
        "and the response must still say the lot is where that came from"
    )
    assert body["reviewed"] == [], "nobody has confirmed it"


def test_a_piece_reports_the_lot_s_claimed_grade_as_a_code(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """lot_claims crosses the API as codes, like every other classifier.

    Every other assertion in this file about lot_claims checks only
    year_start -- the one field where a raw database id and the value a
    person would type happen to look alike, so a lot_claims keyed by id
    (`{"grade_id": 1}`) would still pass them. This checks a classifier, where
    that distinction actually shows: the form must render "lot says 64",
    not "lot says 1".
    """
    from tests.builders import TUBE, build_split_lot, do_split

    parent = build_split_lot(
        db,
        grade_id=code_id(db, Grade, "64"),
        strike_type_id=code_id(db, StrikeType, "business"),
    )
    piece_id = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"][0][
        "id"
    ]

    body = client.get(f"/api/inventory/{piece_id}", headers=admin_headers).json()
    assert body["lot_claims"]["grade"] == "64"
    assert body["lot_claims"]["strike_type"] == "business"


def test_the_detail_payload_covers_every_editable_field(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A field the API accepts but never returns renders blank in the form.

    Someone walking the no_grade queue would then see an empty Grade box on a
    coin that already holds MS65, type a grade, and overwrite it. The two
    lists are defined in different modules, so nothing but this test keeps
    them in step.
    """
    from app.routers.inventory import EDITABLE_SCALARS, ITEM_CLASSIFIERS

    item = build_bare_item(db)
    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()

    missing = sorted((set(EDITABLE_SCALARS) | set(ITEM_CLASSIFIERS)) - set(body))
    assert not missing, f"editable but never returned: {missing}"


def test_the_detail_carries_the_numismatic_value(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The offer dialog's Value column reads it from here (the Offers panel).

    A search row always had it; the detail did not, so offering from the
    item editor showed no value to price against.
    """
    item = build_bare_item(db, numismatic_value=Decimal("245.00"))
    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()

    assert body["numismatic_value"] == "245.00"


def test_a_banknote_cannot_be_given_a_metal(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Paper has no metal, and `metal` is a coin-view column in the search.

    The console stopped offering the field for a note, but a stale tab or a
    script could still send it, and the column would have taken it.
    """
    note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))

    response = client.patch(
        f"/api/inventory/{note.id}", json={"metal": "silver"}, headers=admin_headers
    )

    assert response.status_code == 422
    assert "metal" in response.json()["detail"]
    db.refresh(note)
    assert note.metal_id is None


def test_a_coin_can_still_be_given_a_metal(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"metal": "silver"}, headers=admin_headers
    )

    assert response.status_code == 200
    db.refresh(item)
    assert item.metal_id == code_id(db, Metal, "silver")


def test_a_bulk_edit_cannot_give_a_banknote_a_metal(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """All or nothing: the coin in the same selection keeps its old metal."""
    coin = build_bare_item(db)
    note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [coin.id, note.id], "changes": {"metal": "gold"}},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "metal" in response.json()["detail"]
    db.refresh(coin)
    assert coin.metal_id != code_id(db, Metal, "gold")


def test_a_banknote_cannot_take_a_coin_denomination(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The same face value is a coin and a note, and they are different objects."""
    note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"denomination": "usd_coin_0_25"},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "denomination" in response.json()["detail"]


def test_a_coin_cannot_take_a_note_denomination(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = build_bare_item(db)

    response = client.patch(
        f"/api/inventory/{coin.id}",
        json={"denomination": "usd_note_1"},
        headers=admin_headers,
    )

    assert response.status_code == 422


def test_a_note_takes_a_note_denomination(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"denomination": "usd_note_1"},
        headers=admin_headers,
    )

    assert response.status_code == 200


def test_a_kind_only_edit_that_would_strand_a_denomination_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The invariant is about the item's resulting state, not the sent keys.

    Changing only `item_kind` on a note that already carries a note
    denomination would strand it on the coin side; that must be refused the
    same as sending the denomination directly would be, even though this
    request never mentions `denomination`.
    """
    note = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        denomination_id=code_id(db, Denomination, "usd_note_1"),
    )

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"item_kind": "coin"},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "denomination" in response.json()["detail"]
    db.refresh(note)
    assert note.item_kind_id == code_id(db, ItemKind, "currency")


def test_a_kind_only_edit_that_would_strand_a_metal_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Same shape as the denomination case, for the other coin-only field."""
    coin = build_bare_item(db, metal_id=code_id(db, Metal, "silver"))

    response = client.patch(
        f"/api/inventory/{coin.id}",
        json={"item_kind": "currency"},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "metal" in response.json()["detail"]
    db.refresh(coin)
    assert coin.item_kind_id == code_id(db, ItemKind, "coin")


def test_a_combined_kind_and_denomination_edit_to_a_consistent_pair_succeeds(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A note becoming a coin, with a coin denomination in the same request."""
    note = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        denomination_id=code_id(db, Denomination, "usd_note_1"),
    )

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"item_kind": "coin", "denomination": "usd_coin_0_25"},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    db.refresh(note)
    assert note.item_kind_id == code_id(db, ItemKind, "coin")
    assert note.denomination_id == code_id(db, Denomination, "usd_coin_0_25")


def test_a_combined_kind_and_metal_edit_to_a_consistent_pair_succeeds(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A coin becoming a note, clearing its metal in the same request."""
    coin = build_bare_item(db, metal_id=code_id(db, Metal, "silver"))

    response = client.patch(
        f"/api/inventory/{coin.id}",
        json={"item_kind": "currency", "metal": None},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    db.refresh(coin)
    assert coin.item_kind_id == code_id(db, ItemKind, "currency")
    assert coin.metal_id is None


def test_nulling_a_required_classifier_is_refused_naming_the_field(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """status_id is NOT NULL. Nulling it must be a 422, not a 500.

    catalog.py already guards item_kind this way; this router drifted from
    its neighbour by resolving every classifier through code_to_id, which
    happily returns None for a null code.
    """
    item = build_bare_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"status": None}, headers=admin_headers
    )

    assert response.status_code == 422
    assert "status" in response.json()["detail"]


def test_an_item_keeps_a_retired_value_it_already_holds(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Ruling S5: retiring a value must not make its items unsaveable.

    The editor renders a retired value (the reference API serves them with
    `include_inactive`) and sends it back on every save. Refusing it there
    would fail every save of the item with "Unknown series" about the value
    on screen. Only a *new* use of a retired value is refused.
    """
    morgan = code_id(db, Series, "morgan_dollar")
    holder = build_bare_item(db, series_id=morgan)
    other = build_bare_item(db)
    db.get_one(Series, morgan).is_active = False
    db.commit()

    kept = client.patch(
        f"/api/inventory/{holder.id}",
        headers=admin_headers,
        json={"series": "morgan_dollar", "description": "Re-saved"},
    )
    assert kept.status_code == 200, kept.text

    refused = client.patch(
        f"/api/inventory/{other.id}",
        headers=admin_headers,
        json={"series": "morgan_dollar"},
    )
    assert refused.status_code == 422, refused.text
    assert "Retired series" in refused.json()["detail"]


def test_a_retired_grade_sent_as_a_compound_grade_is_kept(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The editor sends "MS64", which is split into a grade and a strike first.

    `keep` has to compare after that split, against the grade id the item
    holds -- the path most likely to regress, because the code sent is not
    the code stored.
    """
    holder = build_bare_item(db, grade_id=code_id(db, Grade, "64"))
    other = build_bare_item(db, grade_id=None)
    db.get_one(Grade, holder.grade_id).is_active = False
    db.commit()

    kept = client.patch(
        f"/api/inventory/{holder.id}", headers=admin_headers, json={"grade": "MS64"}
    )
    assert kept.status_code == 200, kept.text

    refused = client.patch(
        f"/api/inventory/{other.id}", headers=admin_headers, json={"grade": "MS64"}
    )
    assert refused.status_code == 422, refused.text
    assert "Retired grade" in refused.json()["detail"]


def test_a_note_keeps_a_retired_seal_colour_it_already_holds(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The note fields resolve through `_note_changes`, which `keep` must reach."""
    blue = code_id(db, SealColor, "blue")
    note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))
    db.add(CurrencyDetail(inventory_item_id=note.id, seal_color_id=blue))
    other = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))
    db.add(CurrencyDetail(inventory_item_id=other.id))
    db.get_one(SealColor, blue).is_active = False
    db.commit()

    kept = client.patch(
        f"/api/inventory/{note.id}", headers=admin_headers, json={"seal_color": "blue"}
    )
    assert kept.status_code == 200, kept.text

    refused = client.patch(
        f"/api/inventory/{other.id}", headers=admin_headers, json={"seal_color": "blue"}
    )
    assert refused.status_code == 422, refused.text
    assert "Retired seal_color" in refused.json()["detail"]
