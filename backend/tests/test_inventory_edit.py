"""Editing an item that is not for sale.

`PATCH /api/inventory/{id}` corrects any item, listed or not; most of the
collection has no listing.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app.models import (
    CurrencyDetail,
    Denomination,
    Grade,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Metal,
    SealColor,
    Series,
    StrikeType,
)
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from tests.builders import TUBE, build_bare_item, build_split_lot, code_id


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
    """An unknown code must not become a silently null column."""
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

    Without it, offering from the item editor would show no value to price
    against.
    """
    item = build_bare_item(db, numismatic_value=Decimal("245.00"))
    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()

    assert body["numismatic_value"] == "245.00"


def test_a_banknote_cannot_be_given_a_metal(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Paper has no metal, and `metal` is a coin-view column in the search.

    The console does not offer the field for a note, but a stale tab or a
    script can still send it.
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

    A NOT NULL classifier is resolved with `require_code`, which refuses a
    null code by name; `code_to_id` returns None for one.
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
    """Retiring a value must not make its items unsaveable.

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


def test_an_unknown_field_is_refused_not_dropped(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A field the edit does not know is a 422, and nothing else is applied.

    Dropped silently, a misspelt field or one this route cannot change
    (`purchase_order_id`) would answer 200 with the rest applied, and the
    caller would believe a change was made that never was. `ItemCreate`
    refuses the same way.
    """
    item = build_bare_item(db, source_title="before")

    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "after", "purchase_order_id": 1},
        headers=admin_headers,
    )

    assert response.status_code == 422, response.text
    assert "purchase_order_id" in response.text
    db.refresh(item)
    assert item.source_title == "before"


def test_a_bulk_edit_refuses_an_unknown_field(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Bulk takes the same fields as a single edit, so it refuses the same way."""
    item = build_bare_item(db, source_title="before")

    for body in (
        {"ids": [item.id], "changes": {"source_title": "after", "sorce_title": "x"}},
        {"ids": [item.id], "changes": {"source_title": "after"}, "id": [item.id]},
    ):
        response = client.post("/api/inventory/bulk", json=body, headers=admin_headers)
        assert response.status_code == 422, response.text

    db.refresh(item)
    assert item.source_title == "before"


def test_the_rating_is_shown_and_corrected_like_any_text(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A rating the passes read as evidence can be put right by a person."""
    item = build_bare_item(db, rating="Funny Back")
    path = f"/api/inventory/{item.id}"
    assert client.get(path, headers=admin_headers).json()["rating"] == "Funny Back"

    changed = client.patch(path, json={"rating": "  No Motto  "}, headers=admin_headers)
    assert changed.status_code == 200, changed.text
    assert client.get(path, headers=admin_headers).json()["rating"] == "No Motto"

    cleared = client.patch(path, json={"rating": " "}, headers=admin_headers)
    assert cleared.status_code == 200, cleared.text
    assert client.get(path, headers=admin_headers).json()["rating"] is None
    db.refresh(item)
    assert item.rating is None


def _note(db: Session, **detail: object) -> InventoryItem:
    """A banknote with its currency detail row, holding these detail columns."""
    note = build_bare_item(
        db, item_kind_id=code_id(db, ItemKind, "currency"), year_start=None
    )
    db.add(CurrencyDetail(inventory_item_id=note.id, **detail))
    db.commit()
    return note


def test_an_edit_of_a_note_s_own_fields_moves_the_item_s_version(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The note's fields are another table, and the version guards the whole item.

    A form opened before a serial number changed must be told, and an open
    editor notices a change made elsewhere by the version alone.
    """
    note = _note(db, serial_number="A11111111A")
    path = f"/api/inventory/{note.id}"
    opened = client.get(path, headers=admin_headers).json()["version"]

    first = client.patch(
        path,
        json={"serial_number": "A22222222A", "version": opened},
        headers=admin_headers,
    )
    assert first.status_code == 200, first.text
    assert client.get(path, headers=admin_headers).json()["version"] == opened + 1

    # The form that opened before it still holds the old version.
    stale = client.patch(
        path,
        json={"serial_number": "A33333333A", "version": opened},
        headers=admin_headers,
    )
    assert stale.status_code == 409, stale.text
    db.expire_all()
    detail = db.get_one(CurrencyDetail, note.id)
    assert detail.serial_number == "A22222222A"


def test_a_note_field_sent_unchanged_does_not_move_the_version(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = _note(db, serial_number="A11111111A")
    path = f"/api/inventory/{note.id}"
    opened = client.get(path, headers=admin_headers).json()["version"]

    same = client.patch(
        path, json={"serial_number": "A11111111A"}, headers=admin_headers
    )

    assert same.status_code == 200, same.text
    assert client.get(path, headers=admin_headers).json()["version"] == opened


def test_a_serial_number_sent_blank_is_stored_as_none(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """An empty string is not a serial: "no serial recorded" is NULL."""
    note = _note(db, serial_number="A11111111A")

    for blank in ("", "   "):
        cleared = client.patch(
            f"/api/inventory/{note.id}",
            json={"serial_number": blank},
            headers=admin_headers,
        )
        assert cleared.status_code == 200, cleared.text
        db.expire_all()
        assert db.get_one(CurrencyDetail, note.id).serial_number is None, repr(blank)


@pytest.mark.parametrize(
    "field",
    ["item_cost", "shipping_cost", "piece_count", "source_title", "description"],
)
def test_nulling_a_required_scalar_is_refused_naming_the_field(
    client: TestClient, admin_headers: dict[str, str], db: Session, field: str
) -> None:
    """Each is NOT NULL: an explicit null is a 422 naming it, never a 500."""
    item = build_bare_item(db, source_title="kept", description="kept")

    response = client.patch(
        f"/api/inventory/{item.id}",
        json={field: None, "rating": "sent with it"},
        headers=admin_headers,
    )

    assert response.status_code == 422, response.text
    assert field in response.json()["detail"]
    db.refresh(item)
    assert item.rating is None, "nothing sent with the refused field is applied"


def test_a_bulk_edit_refuses_a_nulled_required_scalar(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db)

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [item.id], "changes": {"item_cost": None}},
        headers=admin_headers,
    )

    assert response.status_code == 422, response.text
    assert "item_cost" in response.json()["detail"]


def test_a_piece_is_not_told_its_lot_claimed_a_series(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A piece does not inherit its lot's design series, so it is no claim.

    `lot_claims` says what the piece took from the seller's description of
    the lot. Naming a series there would mark as inherited a value the piece
    never held.
    """
    parent = build_split_lot(db, series_id=code_id(db, Series, "morgan_dollar"))
    piece_id = client.post(
        f"/api/inventory/{parent.id}/split", json=TUBE, headers=admin_headers
    ).json()["pieces"][0]["id"]

    body = client.get(f"/api/inventory/{piece_id}", headers=admin_headers).json()

    assert body["series"] is None
    assert "series" not in body["lot_claims"]
    # The fields a piece does inherit are still claimed.
    assert body["lot_claims"]["year_start"] == 1881


#: A request as `client.request` takes it: method, path and JSON body.
Request = tuple[str, str, dict[str, object] | None]


def _bulk(db: Session) -> Request:
    """A bulk edit of one item."""
    item = build_bare_item(db)
    return (
        "POST",
        "/api/inventory/bulk",
        {"ids": [item.id], "changes": {"description": "x"}},
    )


def _delete(db: Session) -> Request:
    """A soft delete of an item nothing refers to."""
    return "DELETE", f"/api/inventory/{build_bare_item(db).id}", None


def _detach(db: Session) -> Request:
    """A piece detached from its lot."""
    parent = build_bare_item(db)
    piece = build_bare_item(db, parent_item_id=parent.id)
    return "DELETE", f"/api/inventory/{piece.id}/parent", None


def _errors(db: Session) -> Request:
    """An item's errors replaced."""
    item = build_bare_item(db)
    return (
        "PUT",
        f"/api/inventory/{item.id}/errors",
        {"errors": [{"error_type": "doubled_die"}]},
    )


def _receive(db: Session) -> Request:
    """An ordered item received."""
    item = build_bare_item(db, status_id=code_id(db, ItemStatus, "ordered"))
    return (
        "POST",
        "/api/inventory/receive",
        {"item_ids": [item.id], "outcome": "received"},
    )


def _split(db: Session) -> Request:
    """A lot split into its pieces."""
    return "POST", f"/api/inventory/{build_split_lot(db).id}/split", dict(TUBE)


@pytest.mark.parametrize("build", [_bulk, _delete, _detach, _errors, _receive, _split])
def test_a_write_that_loses_to_another_writer_is_a_409(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    build: Callable[[Session], Request],
) -> None:
    """A version that moved under a write is the caller's conflict, not a 500.

    Every one of these writes a versioned item row. The rows are built and
    committed first; from then on the session's commit answers as the
    database does when an UPDATE's `WHERE version = ...` matches no row.
    """
    method, path, body = build(db)

    def stale() -> None:
        """Refuse the commit as a lost race."""
        raise StaleDataError("UPDATE statement on table matched 0 rows")

    monkeypatch.setattr(db, "commit", stale)

    response: Response = client.request(method, path, json=body, headers=admin_headers)

    assert response.status_code == 409, response.text
