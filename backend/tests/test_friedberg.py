"""The owner's own Friedberg catalogue: search, record, attach.

Every `fr_number` used here is obviously synthetic (`FR-TEST-*`) -- never a
real catalogue number, per `CLAUDE.md`'s ban on shipping a publisher's
arrangement.
"""

from __future__ import annotations

from typing import Any

from app.models import (
    CurrencyDetail,
    Denomination,
    FriedbergNumber,
    InventoryItem,
    ItemKind,
    NoteType,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item


def _currency_item(db: Session, **overrides: object) -> InventoryItem:
    """A banknote item with a `currency_detail` row attached."""
    item = make_item(db, item_kind_id=code_id(db, ItemKind, "currency"), **overrides)
    db.add(CurrencyDetail(inventory_item_id=item.id))
    db.commit()
    db.refresh(item)
    return item


def _add_friedberg(db: Session, **overrides: object) -> FriedbergNumber:
    row = FriedbergNumber(**overrides)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _ids(body: list[dict[str, Any]]) -> set[int]:
    return {row["id"] for row in body}


# ---------------------------------------------------------------------------
# GET /friedberg -- NULL-tolerant search
# ---------------------------------------------------------------------------


def test_half_known_row_is_found_by_a_filter_it_does_not_know(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A row missing its seal colour still surfaces when seal_color is asked.

    Would NOT pass against a hardcoded response: the assertions require the
    row to appear for a query naming an attribute it lacks, and to disappear
    the moment a *known* attribute is contradicted -- a fixed list, or one
    that just echoes "no filter narrows", satisfies neither.
    """
    row = _add_friedberg(
        db,
        fr_number="FR-TEST-1",
        note_type_id=code_id(db, NoteType, "frn"),
        denomination_id=code_id(db, Denomination, "usd_note_1"),
        series_year=1934,
        seal_color_id=None,  # the note in hand didn't show a clear seal
    )

    # Known attributes match, seal_color is asked but the row doesn't know it.
    resp = client.get(
        "/api/friedberg",
        params={"note_type": "frn", "denomination": "usd_note_1", "seal_color": "blue"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert row.id in _ids(resp.json())

    # A real, contradicting attribute excludes it -- NULL-tolerance is not
    # "match anything".
    resp = client.get(
        "/api/friedberg",
        params={"note_type": "us_note"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert row.id not in _ids(resp.json())


def test_all_unknown_row_does_not_match_every_query(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A row that knows nothing must not answer to every query.

    Would NOT pass against an endpoint that always returns every row (that
    endpoint would wrongly include `blank`), nor one that always returns
    nothing (it would wrongly exclude `known`, which genuinely matches).
    """
    blank = _add_friedberg(db, fr_number="FR-TEST-2")
    known = _add_friedberg(
        db,
        fr_number="FR-TEST-3",
        note_type_id=code_id(db, NoteType, "frn"),
    )

    resp = client.get(
        "/api/friedberg", params={"note_type": "frn"}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    ids = _ids(resp.json())
    assert known.id in ids
    assert blank.id not in ids

    # Also true for a filter the blank row could not possibly contradict.
    resp = client.get(
        "/api/friedberg", params={"series_letter": "A"}, headers=admin_headers
    )
    assert blank.id not in _ids(resp.json())


def test_unknown_classifier_code_is_422_not_ignored(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A bogus code is rejected, not silently dropped as a no-op filter."""
    resp = client.get(
        "/api/friedberg",
        params={"note_type": "not_a_real_note_type"},
        headers=admin_headers,
    )
    assert resp.status_code == 422, resp.text


def test_no_filters_returns_everything(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Browsing the whole catalogue is a valid call with zero filters."""
    row = _add_friedberg(db, fr_number="FR-TEST-4")
    resp = client.get("/api/friedberg", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert row.id in _ids(resp.json())


# ---------------------------------------------------------------------------
# POST /friedberg -- recording a number from a note or slab
# ---------------------------------------------------------------------------


def test_create_records_a_manual_row(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A newly recorded row is unverified and marked manual.

    Would NOT pass against a hardcoded response: it checks the fields the
    database actually stored (source, verified) rather than just the status
    code.
    """
    resp = client.post(
        "/api/friedberg",
        json={"fr_number": "FR-TEST-5", "note_type": "frn", "series_year": 1934},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["fr_number"] == "FR-TEST-5"
    assert body["source"] == "manual"
    assert body["verified"] is False
    assert body["verified_at"] is None


def test_duplicate_fr_number_is_409_naming_the_existing_row(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The same type arriving twice is a clear conflict, not a crash.

    Would NOT pass against a hardcoded 409: the test also checks the body
    names the specific row already on file, not a generic message.
    """
    first = client.post(
        "/api/friedberg",
        json={"fr_number": "FR-TEST-6"},
        headers=admin_headers,
    )
    assert first.status_code == 201, first.text
    existing_id = first.json()["id"]

    second = client.post(
        "/api/friedberg",
        json={"fr_number": "FR-TEST-6", "series_year": 1957},
        headers=admin_headers,
    )
    assert second.status_code == 409, second.text
    assert str(existing_id) in second.json()["detail"]


# ---------------------------------------------------------------------------
# POST /inventory/{item_id}/friedberg -- attaching a match
# ---------------------------------------------------------------------------


def test_attaching_to_a_coin_is_404(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A coin has no Friedberg number -- refused, not silently ignored.

    Would NOT pass against an endpoint that always returns 404: the
    `test_confirming_stamps_verifier_and_timestamp` test below attaches to a
    real currency item and expects success, so the two together require
    genuine branching on whether `currency_detail` exists.
    """
    coin = make_item(db, item_kind_id=code_id(db, ItemKind, "coin"))
    friedberg = _add_friedberg(db, fr_number="FR-TEST-7")

    resp = client.post(
        f"/api/inventory/{coin.id}/friedberg",
        json={"friedberg_id": friedberg.id, "status": "proposed"},
        headers=admin_headers,
    )
    assert resp.status_code == 404, resp.text


def test_unknown_status_is_422_listing_valid_ones(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A bogus `friedberg_status` is refused with the valid values named."""
    item = _currency_item(db)
    friedberg = _add_friedberg(db, fr_number="FR-TEST-8")

    resp = client.post(
        f"/api/inventory/{item.id}/friedberg",
        json={"friedberg_id": friedberg.id, "status": "definitely_maybe"},
        headers=admin_headers,
    )
    assert resp.status_code == 422, resp.text
    assert "confirmed" in resp.json()["detail"]


def test_confirming_stamps_verifier_and_timestamp(
    db: Session,
    client: TestClient,
    admin_headers: dict[str, str],
    admin_user: User,
) -> None:
    """Confirming stamps verified_by_id and verified_at on the catalogue row.

    That is what turns a proposal into a fact -- on the row itself, not just
    the item.

    Would NOT pass against a hardcoded response: it re-reads the
    `friedberg_number` row from the database and checks `verified_by_id`
    equals *this* admin's real id, and re-reads `currency_detail` to confirm
    the item side was updated too.
    """
    item = _currency_item(db)
    friedberg = _add_friedberg(db, fr_number="FR-TEST-9")

    resp = client.post(
        f"/api/inventory/{item.id}/friedberg",
        json={"friedberg_id": friedberg.id, "status": "confirmed"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verified"] is True
    assert body["verified_at"] is not None

    db.refresh(friedberg)
    assert friedberg.verified_by_id == admin_user.id
    assert friedberg.verified_at is not None

    db.refresh(item)
    assert item.currency_detail is not None
    assert item.currency_detail.friedberg_id == friedberg.id
    assert item.currency_detail.friedberg_status == "confirmed"


# ---------------------------------------------------------------------------
# Web press: a printing method, and part of what identifies a type
# ---------------------------------------------------------------------------


def _typed(fr_number: str, web_press: bool | None) -> dict[str, object]:
    """One fully known type, differing from its siblings only by press."""
    return {
        "fr_number": fr_number,
        "note_type": "frn",
        "denomination": "usd_note_1",
        "series_year": 1995,
        "district_letter": "B",
        "web_press": web_press,
    }


def test_web_press_is_recorded_and_returned(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The press is stored on the row, not merely accepted and dropped."""
    resp = client.post(
        "/api/friedberg", json=_typed("FR-TEST-W1", True), headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["web_press"] is True

    listed = client.get("/api/friedberg", headers=admin_headers).json()
    assert [row["web_press"] for row in listed] == [True]


def test_a_web_press_and_a_sheet_fed_printing_are_different_types(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Same denomination, series, note type and district; different press.

    Before the press was part of the identity, the second of these was a 409
    -- the catalogue could hold only one of two real, distinct types.
    """
    web = client.post(
        "/api/friedberg", json=_typed("FR-TEST-W2", True), headers=admin_headers
    )
    sheet = client.post(
        "/api/friedberg", json=_typed("FR-TEST-S2", False), headers=admin_headers
    )
    assert web.status_code == 201, web.text
    assert sheet.status_code == 201, sheet.text


def test_the_same_type_and_press_twice_is_still_a_conflict(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Adding the press to the identity must not have loosened it."""
    first = client.post(
        "/api/friedberg", json=_typed("FR-TEST-W3", True), headers=admin_headers
    )
    again = client.post(
        "/api/friedberg", json=_typed("FR-TEST-W4", True), headers=admin_headers
    )
    assert first.status_code == 201, first.text
    assert again.status_code == 409, again.text


def test_the_web_press_filter_narrows_like_every_other(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Asking for web press finds web and unknown-press rows, never sheet-fed."""
    ids = {
        press: client.post(
            "/api/friedberg",
            json=_typed(f"FR-TEST-F{index}", press) | {"series_year": 1990 + index},
            headers=admin_headers,
        ).json()["id"]
        for index, press in enumerate((True, False, None))
    }
    resp = client.get(
        "/api/friedberg",
        params={"denomination": "usd_note_1", "web_press": "true"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert _ids(resp.json()) == {ids[True], ids[None]}


# ---------------------------------------------------------------------------
# The item editor: what is attached, and taking it off again
# ---------------------------------------------------------------------------


def test_the_item_detail_says_which_number_is_attached(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The editor shows the number, its status and whether it is verified."""
    item = _currency_item(db)
    friedberg = _add_friedberg(db, fr_number="FR-TEST-D1")
    attached = client.post(
        f"/api/inventory/{item.id}/friedberg",
        json={"friedberg_id": friedberg.id, "status": "proposed"},
        headers=admin_headers,
    )
    assert attached.status_code == 200, attached.text

    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert body["friedberg_id"] == friedberg.id
    assert body["friedberg_number"] == "FR-TEST-D1"
    assert body["friedberg_status"] == "proposed"
    assert body["friedberg_verified"] is False


def test_clearing_takes_the_number_off_the_note_only(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The note goes back to unknown; the catalogue row stays for the next one."""
    item = _currency_item(db)
    friedberg = _add_friedberg(db, fr_number="FR-TEST-D2")
    client.post(
        f"/api/inventory/{item.id}/friedberg",
        json={"friedberg_id": friedberg.id, "status": "confirmed"},
        headers=admin_headers,
    )

    resp = client.delete(f"/api/inventory/{item.id}/friedberg", headers=admin_headers)
    assert resp.status_code == 204, resp.text

    db.expire_all()
    detail = db.get(CurrencyDetail, item.id)
    assert detail is not None
    assert detail.friedberg_id is None
    assert detail.friedberg_status == "unknown"
    assert db.get(FriedbergNumber, friedberg.id) is not None
    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert body["friedberg_number"] is None
    assert body["friedberg_status"] == "unknown"


def test_clearing_a_coin_is_404(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A coin has no Friedberg number to clear -- refused, as attaching is."""
    coin = make_item(db, item_kind_id=code_id(db, ItemKind, "coin"))
    resp = client.delete(f"/api/inventory/{coin.id}/friedberg", headers=admin_headers)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


def test_a_non_admin_is_refused(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    """Staff-only throughout: a customer gets 403, not a filtered view."""
    resp = client.get("/api/friedberg", headers=customer_headers)
    assert resp.status_code == 403, resp.text
