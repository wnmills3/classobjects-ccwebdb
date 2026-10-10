"""The owner's own Friedberg catalog: search, record, attach.

Every `fr_number` used here is obviously synthetic -- in the 9900s, past
any real Friedberg number, yet in a number's form so `app.fr_format`
accepts it -- never a real catalog number, per `docs/reference-data.md`.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.fr_format import fr_traits
from app.models import (
    CurrencyDetail,
    Denomination,
    FriedbergNumber,
    InventoryItem,
    ItemKind,
    NoteType,
    ProvenanceSource,
    SealColor,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def _currency_item(db: Session, **overrides: object) -> InventoryItem:
    """A banknote item with a `currency_detail` row attached."""
    item = build_bare_item(
        db, item_kind_id=code_id(db, ItemKind, "currency"), **overrides
    )
    db.add(CurrencyDetail(inventory_item_id=item.id))
    db.commit()
    db.refresh(item)
    return item


def _add_friedberg(db: Session, **overrides: object) -> FriedbergNumber:
    """A catalog row with these columns, committed."""
    row = FriedbergNumber(**overrides)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _ids(body: list[dict[str, Any]]) -> set[int]:
    """The ids of the rows in a search response."""
    return {row["id"] for row in body}


# ---------------------------------------------------------------------------
# GET /friedberg -- NULL-tolerant search
# ---------------------------------------------------------------------------


def test_half_known_row_is_found_by_a_filter_it_does_not_know(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A row missing its seal color still surfaces when seal_color is asked.

    Would NOT pass against a hardcoded response: the assertions require the
    row to appear for a query naming an attribute it lacks, and to disappear
    the moment a *known* attribute is contradicted -- a fixed list, or one
    that just echoes "no filter narrows", satisfies neither.
    """
    row = _add_friedberg(
        db,
        fr_number="9901",
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
    blank = _add_friedberg(db, fr_number="9902")
    known = _add_friedberg(
        db,
        fr_number="9903",
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
    """Browsing the whole catalog is a valid call with zero filters."""
    row = _add_friedberg(db, fr_number="9904")
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
        json={"fr_number": "9905", "note_type": "frn", "series_year": 1934},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["fr_number"] == "9905"
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
        json={"fr_number": "9906"},
        headers=admin_headers,
    )
    assert first.status_code == 201, first.text
    existing_id = first.json()["id"]

    second = client.post(
        "/api/friedberg",
        json={"fr_number": "9906", "series_year": 1957},
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
    coin = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "coin"))
    friedberg = _add_friedberg(db, fr_number="9907")

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
    friedberg = _add_friedberg(db, fr_number="9908")

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
    """Confirming stamps verified_by_id and verified_at on the catalog row.

    That is what turns a proposal into a fact -- on the row itself, not just
    the item.

    Would NOT pass against a hardcoded response: it re-reads the
    `friedberg_number` row from the database and checks `verified_by_id`
    equals *this* admin's real id, and re-reads `currency_detail` to confirm
    the item side was updated too.
    """
    item = _currency_item(db)
    friedberg = _add_friedberg(db, fr_number="9909")

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
        "/api/friedberg", json=_typed("9910", True), headers=admin_headers
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["web_press"] is True

    listed = client.get("/api/friedberg", headers=admin_headers).json()
    assert [row["web_press"] for row in listed] == [True]


def test_a_web_press_and_a_sheet_fed_printing_are_different_types(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Same denomination, series, note type and district; different press.

    The press is part of the identity, so the catalog holds both of two
    real, distinct types.
    """
    web = client.post(
        "/api/friedberg", json=_typed("9911", True), headers=admin_headers
    )
    sheet = client.post(
        "/api/friedberg", json=_typed("9912", False), headers=admin_headers
    )
    assert web.status_code == 201, web.text
    assert sheet.status_code == 201, sheet.text


def test_the_same_type_and_press_twice_is_still_a_conflict(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Adding the press to the identity must not have loosened it."""
    first = client.post(
        "/api/friedberg", json=_typed("9913", True), headers=admin_headers
    )
    again = client.post(
        "/api/friedberg", json=_typed("9914", True), headers=admin_headers
    )
    assert first.status_code == 201, first.text
    assert again.status_code == 409, again.text


def test_a_mule_and_a_star_are_types_of_their_own(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The same note, its mule and its star note are three numbers.

    A mule differs only in its plates and a star note only in its serial,
    neither of which is a catalog fact -- so the number's own `m` and `*`
    are part of the identity, or the mule on file would refuse the plain
    number for the same note, and the plain one the mule.
    """
    for number in ("9915-Bm", "9915-B", "9915-B*", "9915-Bm*"):
        resp = client.post(
            "/api/friedberg", json=_typed(number, None), headers=admin_headers
        )
        assert resp.status_code == 201, (number, resp.text)


def test_a_seal_shade_is_part_of_the_number(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """`9921-B LGS` beside `9921-B`: the shade tells the two seals apart.

    The seal itself is already part of a type's identity; the form of the
    number takes the suffix as well.
    """
    _light_green(db)
    plain = client.post(
        "/api/friedberg",
        json={**_typed("9921-B", None), "seal_color": "green"},
        headers=admin_headers,
    )
    shade = client.post(
        "/api/friedberg",
        json={**_typed("9921-b lgs", None), "seal_color": "light_green"},
        headers=admin_headers,
    )
    star = client.post(
        "/api/friedberg",
        json={**_typed("9921-B* LGS", None), "seal_color": "light_green"},
        headers=admin_headers,
    )
    assert plain.status_code == 201, plain.text
    assert shade.status_code == 201, shade.text
    assert shade.json()["fr_number"] == "9921-B LGS"
    # A star note of the same shade is a type of its own too: the star is
    # read past the shade.
    assert star.status_code == 201, star.text


def test_the_database_reads_star_and_mule_as_fr_traits_does(db: Session) -> None:
    """The generated columns and `fr_traits` are one rule written twice."""
    numbers = [
        "9931-L",
        "9932-L*",
        "9933-Lm",
        "9934-Lm*",
        "9935m",
        "9936-L LGS",
        "9937-L* LGS",
        "9938-Lm DGS",
        "9939-Lm* DGS",
    ]
    for number in numbers:
        db.add(FriedbergNumber(fr_number=number, source=ProvenanceSource.manual))
    db.flush()
    stored = {
        row.fr_number: (row.is_star, row.is_mule)
        for row in db.scalars(
            select(FriedbergNumber).where(FriedbergNumber.fr_number.in_(numbers))
        )
    }
    assert stored == {number: fr_traits(number) for number in numbers}


def test_a_second_plain_number_for_a_type_with_a_mule_is_still_a_conflict(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Telling a mule apart must not let the plain type be recorded twice."""
    for number in ("9916-Bm", "9916-B"):
        resp = client.post(
            "/api/friedberg", json=_typed(number, None), headers=admin_headers
        )
        assert resp.status_code == 201, resp.text
    again = client.post(
        "/api/friedberg", json=_typed("9917-B", None), headers=admin_headers
    )
    assert again.status_code == 409, again.text
    assert again.json()["existing"]["fr_number"] == "9916-B"
    mule_again = client.post(
        "/api/friedberg", json=_typed("9917-Bm", None), headers=admin_headers
    )
    assert mule_again.status_code == 409, mule_again.text
    assert mule_again.json()["existing"]["fr_number"] == "9916-Bm"


def test_correcting_a_mule_onto_a_plain_type_on_file_is_a_409(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Dropping the `m` from a mule whose plain type is recorded is refused.

    It would make two rows one type; refused by name, not as a 500 from the
    identity index.
    """
    plain = client.post(
        "/api/friedberg", json=_typed("9918-B", None), headers=admin_headers
    ).json()
    mule = client.post(
        "/api/friedberg", json=_typed("9918-Bm", None), headers=admin_headers
    ).json()

    refused = client.patch(
        f"/api/friedberg/{mule['id']}",
        json={"fr_number": "9919-B"},
        headers=admin_headers,
    )

    assert refused.status_code == 409, refused.text
    assert refused.json()["existing"]["id"] == plain["id"]


def test_a_correction_racing_onto_a_type_on_file_is_a_409_not_a_500(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two corrections at once both pass the checks; the index stops one.

    Simulated by making the checks themselves miss, as if they ran before the
    other request committed: the commit then meets
    `uq_friedberg_number_identity`, and that reads as a 409, as it does when
    a number is recorded.
    """
    client.post("/api/friedberg", json=_typed("9951-B", None), headers=admin_headers)
    mule = client.post(
        "/api/friedberg", json=_typed("9951-Bm", None), headers=admin_headers
    ).json()
    monkeypatch.setattr(db, "scalar", lambda *args, **kwargs: None)

    refused = client.patch(
        f"/api/friedberg/{mule['id']}",
        json={"fr_number": "9952-B"},
        headers=admin_headers,
    )

    assert refused.status_code == 409, refused.text
    monkeypatch.undo()
    db.expire_all()
    assert db.get_one(FriedbergNumber, mule["id"]).fr_number == "9951-Bm"


def test_a_district_letter_is_kept_in_capitals(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """`b` and `B` are one district, so they are one type.

    Kept as typed, the second recording would pass the identity index as a
    different type, and a search for district B would not strictly match
    the first.
    """
    first = client.post(
        "/api/friedberg",
        json=_typed("9953-B", None) | {"district_letter": "b"},
        headers=admin_headers,
    )
    assert first.status_code == 201, first.text
    assert first.json()["district_letter"] == "B"

    again = client.post(
        "/api/friedberg", json=_typed("9954-B", None), headers=admin_headers
    )
    assert again.status_code == 409, again.text
    assert again.json()["existing"]["fr_number"] == "9953-B"


def test_two_districts_numbers_recorded_without_a_district_are_two_types(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A number's own district is its row's when the note records none.

    Left unknown, the first number recorded for a series would hold the
    combination for every district, and the next district's number would be
    refused as that type recorded twice.
    """
    unplaced = {**_typed("", None), "district_letter": None}

    first = client.post(
        "/api/friedberg", json=unplaced | {"fr_number": "9961-E"}, headers=admin_headers
    )
    second = client.post(
        "/api/friedberg", json=unplaced | {"fr_number": "9961-G"}, headers=admin_headers
    )

    assert first.status_code == 201, first.text
    assert first.json()["district_letter"] == "E"
    assert second.status_code == 201, second.text
    assert second.json()["district_letter"] == "G"
    # The same district again is still the same type.
    again = client.post(
        "/api/friedberg", json=unplaced | {"fr_number": "9962-G"}, headers=admin_headers
    )
    assert again.status_code == 409, again.text
    assert again.json()["existing"]["fr_number"] == "9961-G"


def test_a_number_of_another_district_than_the_notes_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """District C's number is not recorded for a district B note.

    One of the two is a slip, and saved it would be offered to every later
    district B note of the series.
    """
    resp = client.post(
        "/api/friedberg", json=_typed("9963-C", None), headers=admin_headers
    )
    assert resp.status_code == 422, resp.text
    assert "district C" in resp.text
    assert "district B" in resp.text
    listed = client.get("/api/friedberg", headers=admin_headers).json()
    assert listed == []


def test_a_search_by_district_leaves_out_another_districts_number(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A row with no district is still its number's district's.

    `9971-E` recorded before a number's district was kept would otherwise
    be offered, as unknown, to a note of any district.
    """
    typed = {
        "note_type_id": code_id(db, NoteType, "frn"),
        "denomination_id": code_id(db, Denomination, "usd_note_1"),
        "series_year": 1995,
    }
    richmond = _add_friedberg(db, fr_number="9971-E", **typed)
    mule = _add_friedberg(db, fr_number="9971-Em* LGS", **typed)
    unplaced = _add_friedberg(db, fr_number="9972", series_letter="A", **typed)
    asked = {"note_type": "frn", "denomination": "usd_note_1", "series_year": 1995}

    def found(**more: str) -> set[int]:
        resp = client.get("/api/friedberg", params=asked | more, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        return _ids(resp.json())

    # A number with no district may be any district's: still found.
    assert found(district_letter="G") == {unplaced.id}
    assert found(district_letter="E") == {richmond.id, mule.id, unplaced.id}
    assert found() == {richmond.id, mule.id, unplaced.id}
    # The number's district is something the row knows: asked for alone, it
    # finds the row, as a recorded district would.
    resp = client.get(
        "/api/friedberg", params={"district_letter": "E"}, headers=admin_headers
    )
    assert _ids(resp.json()) == {richmond.id, mule.id}


def test_correcting_a_number_to_another_districts_moves_the_row(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A row's district follows a corrected number that names another.

    The number is what was wrong, so the district read from it was too; and
    the type it becomes may already be on file.
    """
    row = client.post(
        "/api/friedberg",
        json=_typed("9973-B", None) | {"district_letter": None},
        headers=admin_headers,
    ).json()
    assert row["district_letter"] == "B"

    corrected = client.patch(
        f"/api/friedberg/{row['id']}",
        json={"fr_number": "9973-C"},
        headers=admin_headers,
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["district_letter"] == "C"

    other = client.post(
        "/api/friedberg", json=_typed("9974-B", None), headers=admin_headers
    ).json()
    refused = client.patch(
        f"/api/friedberg/{other['id']}",
        json={"fr_number": "9974-C"},
        headers=admin_headers,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["existing"]["id"] == row["id"]


def test_correcting_a_number_to_one_with_a_district_records_the_district(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A row with no district takes its corrected number's.

    And is refused where that district's type is already on file.
    """
    unplaced = {**_typed("", None), "district_letter": None}
    bare = client.post(
        "/api/friedberg", json=unplaced | {"fr_number": "9964"}, headers=admin_headers
    ).json()
    assert bare["district_letter"] is None

    corrected = client.patch(
        f"/api/friedberg/{bare['id']}",
        json={"fr_number": "9964-L"},
        headers=admin_headers,
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["district_letter"] == "L"

    other = client.post(
        "/api/friedberg", json=unplaced | {"fr_number": "9965"}, headers=admin_headers
    ).json()
    refused = client.patch(
        f"/api/friedberg/{other['id']}",
        json={"fr_number": "9965-L"},
        headers=admin_headers,
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["existing"]["id"] == bare["id"]


def test_a_district_letter_past_l_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """There are twelve districts, A to L."""
    for letter in ("M", "z", "1"):
        resp = client.post(
            "/api/friedberg",
            json=_typed("9955", None) | {"district_letter": letter},
            headers=admin_headers,
        )
        assert resp.status_code == 422, (letter, resp.text)


def test_attaching_a_number_as_unknown_is_refused(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """`unknown` is a note with no number: it cannot be how one is attached."""
    item = _currency_item(db)
    friedberg = _add_friedberg(db, fr_number="9956")

    resp = client.post(
        f"/api/inventory/{item.id}/friedberg",
        json={"friedberg_id": friedberg.id, "status": "unknown"},
        headers=admin_headers,
    )

    assert resp.status_code == 422, resp.text
    assert "proposed" in resp.json()["detail"]
    db.expire_all()
    detail = db.get(CurrencyDetail, item.id)
    assert detail is not None
    assert detail.friedberg_id is None


def test_the_same_series_under_two_signature_pairs_are_two_types(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Many series carry no letter and differ only by who signed them.

    Signatures are part of what identifies a type, so the second of these
    is not a duplicate. Seal color is the same case -- a wartime brown or
    yellow seal beside the regular blue.
    """
    base = {
        "note_type": "silver_certificate",
        "denomination": "usd_note_1",
        "series_year": 1935,
        "seal_color": "blue",
    }
    first = client.post(
        "/api/friedberg",
        json=base | {"fr_number": "9915", "signature_combination": "julian_morgenthau"},
        headers=admin_headers,
    )
    other_signers = client.post(
        "/api/friedberg",
        json=base | {"fr_number": "9916", "signature_combination": "julian_vinson"},
        headers=admin_headers,
    )
    other_seal = client.post(
        "/api/friedberg",
        json=base
        | {
            "fr_number": "9917",
            "signature_combination": "julian_morgenthau",
            "seal_color": "brown",
        },
        headers=admin_headers,
    )
    again = client.post(
        "/api/friedberg",
        json=base | {"fr_number": "9918", "signature_combination": "julian_morgenthau"},
        headers=admin_headers,
    )
    assert first.status_code == 201, first.text
    assert other_signers.status_code == 201, other_signers.text
    assert other_seal.status_code == 201, other_seal.text
    assert again.status_code == 409, again.text


def test_the_web_press_filter_narrows_like_every_other(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Asking for web press finds web and unknown-press rows, never sheet-fed."""
    ids = {
        press: client.post(
            "/api/friedberg",
            json=_typed(f"994{index}", press) | {"series_year": 1990 + index},
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
    friedberg = _add_friedberg(db, fr_number="9920")
    attached = client.post(
        f"/api/inventory/{item.id}/friedberg",
        json={"friedberg_id": friedberg.id, "status": "proposed"},
        headers=admin_headers,
    )
    assert attached.status_code == 200, attached.text

    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert body["friedberg_id"] == friedberg.id
    assert body["friedberg_number"] == "9920"
    assert body["friedberg_status"] == "proposed"
    assert body["friedberg_verified"] is False


def test_clearing_takes_the_number_off_the_note_only(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The note goes back to unknown; the catalog row stays for the next one."""
    item = _currency_item(db)
    friedberg = _add_friedberg(db, fr_number="9921")
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
    coin = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "coin"))
    resp = client.delete(f"/api/inventory/{coin.id}/friedberg", headers=admin_headers)
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# GET /friedberg/signatures -- the pairs a note of this series can carry
# ---------------------------------------------------------------------------


def _signatures(
    client: TestClient, headers: dict[str, str], **params: object
) -> tuple[list[str], str]:
    """The signature codes offered for these facts, and the rule that chose them."""
    resp = client.get("/api/friedberg/signatures", params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return [value["code"] for value in body["values"]], body["source"]


def test_a_lettered_series_offers_its_own_later_signers(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Series 1963-A was signed by Granahan and Fowler, whose term began in 1965.

    Narrowing by "whose term covers 1963" would offer only Granahan /
    Dillon, so the right pair for a 1963-A note could not be chosen. The
    seeded `note_issue` facts answer it exactly.
    """
    codes, source = _signatures(
        client,
        admin_headers,
        denomination="usd_note_1",
        note_type="frn",
        series_year=1963,
        series_letter="A",
    )
    assert codes == ["granahan_fowler"]
    assert source == "note_issue"


def test_no_letter_yet_offers_every_letter_of_the_series(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A blank letter may not be typed yet: wider is safe, narrower hides it."""
    codes, _ = _signatures(
        client,
        admin_headers,
        denomination="usd_note_1",
        note_type="frn",
        series_year=1963,
    )
    assert {"granahan_dillon", "granahan_fowler", "granahan_barr"} <= set(codes)


def test_a_series_with_no_facts_falls_back_to_signers_still_in_office(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Without facts, a pair whose term ended before the series cannot sign it.

    Any pair still in office at or after the series year can -- a lettered
    series is printed later -- so the fallback keeps those, Fowler included.
    """
    codes, source = _signatures(
        client,
        admin_headers,
        denomination="usd_note_2",
        note_type="frn",
        series_year=1963,
    )
    assert source == "term"
    assert "granahan_fowler" in codes
    assert "smith_dillon" not in codes  # left office in 1962


def test_with_no_series_year_every_pair_is_offered(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Nothing to narrow by yet: the whole list, not an empty picker."""
    codes, source = _signatures(client, admin_headers)
    assert source == "all"
    assert {"smith_dillon", "granahan_fowler"} <= set(codes)


def test_an_unknown_code_narrowing_signatures_is_422(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A typo must not quietly widen or empty the list."""
    resp = client.get(
        "/api/friedberg/signatures",
        params={"denomination": "usd_note_one", "series_year": 1963},
        headers=admin_headers,
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_a_non_admin_is_refused(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    """Staff-only throughout: a customer gets 403, not a filtered view."""
    resp = client.get("/api/friedberg", headers=customer_headers)
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Seal shade: an LGS number goes with a light green seal
# ---------------------------------------------------------------------------


def _light_green(db: Session) -> int:
    """The `light_green` seal, added here when the seed data has none."""
    existing = db.scalar(select(SealColor).where(SealColor.code == "light_green"))
    if existing is not None:
        return existing.id
    row = SealColor(code="light_green", label="Light Green")
    db.add(row)
    db.commit()
    return row.id


def test_an_lgs_number_with_another_seal_is_refused(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The suffix says light green; a recorded seal that is not is a slip."""
    _light_green(db)
    refused = client.post(
        "/api/friedberg",
        json={**_typed("9941-B LGS", None), "seal_color": "green"},
        headers=admin_headers,
    )
    assert refused.status_code == 422, refused.text
    assert "light green" in refused.json()["detail"].lower()

    accepted = client.post(
        "/api/friedberg",
        json={**_typed("9941-B LGS", None), "seal_color": "light_green"},
        headers=admin_headers,
    )
    assert accepted.status_code == 201, accepted.text


def test_an_lgs_number_with_no_seal_recorded_is_allowed(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Not knowing the seal yet is an incomplete record, not a contradiction."""
    _light_green(db)
    resp = client.post(
        "/api/friedberg", json=_typed("9942-B LGS", None), headers=admin_headers
    )
    assert resp.status_code == 201, resp.text


def test_a_dgs_number_with_a_light_green_seal_is_refused(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The other shade's half of the same rule."""
    _light_green(db)
    refused = client.post(
        "/api/friedberg",
        json={**_typed("9943-B DGS", None), "seal_color": "light_green"},
        headers=admin_headers,
    )
    assert refused.status_code == 422, refused.text


def test_an_lgs_number_cannot_be_attached_to_a_note_with_another_seal(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """What the note in hand shows is what the number must agree with."""
    light = _light_green(db)
    green_note = _currency_item(db)
    assert green_note.currency_detail is not None
    green_note.currency_detail.seal_color_id = code_id(db, SealColor, "green")
    light_note = _currency_item(db)
    assert light_note.currency_detail is not None
    light_note.currency_detail.seal_color_id = light
    db.commit()
    friedberg = _add_friedberg(db, fr_number="9944-B LGS")

    refused = client.post(
        f"/api/inventory/{green_note.id}/friedberg",
        json={"friedberg_id": friedberg.id, "status": "proposed"},
        headers=admin_headers,
    )
    assert refused.status_code == 422, refused.text
    db.refresh(green_note.currency_detail)
    assert green_note.currency_detail.friedberg_id is None

    attached = client.post(
        f"/api/inventory/{light_note.id}/friedberg",
        json={"friedberg_id": friedberg.id, "status": "proposed"},
        headers=admin_headers,
    )
    assert attached.status_code == 200, attached.text


def test_correcting_a_number_to_lgs_checks_the_rows_seal(
    db: Session, client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Adding the suffix later meets the same rule as recording it."""
    _light_green(db)
    row = client.post(
        "/api/friedberg",
        json={**_typed("9945-B", None), "seal_color": "green"},
        headers=admin_headers,
    ).json()

    refused = client.patch(
        f"/api/friedberg/{row['id']}",
        json={"fr_number": "9945-B LGS"},
        headers=admin_headers,
    )
    assert refused.status_code == 422, refused.text
