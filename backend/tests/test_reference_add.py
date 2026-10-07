"""Adding a value to a vocabulary, with the columns that are its own.

`GET /api/reference/{table}` says what a vocabulary asks for and
`POST /api/reference/{table}` reads it. See
docs/specs/vocabulary-and-errors-design.md.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.models import Currency, Denomination, DenominationKind
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

SEVEN_CENTS = {
    "label": "Seven Cents",
    "sort_order": 22,
    "extra": {"currency": "USD", "face_value": "0.07", "kind": "coin"},
}


def _fields(client: TestClient, table: str) -> dict[str, dict[str, object]]:
    """The vocabulary's own columns as its route describes them, by name."""
    body = client.get(f"/api/reference/{table}").json()
    return {field["name"]: field for field in body["fields"]}


def test_a_vocabulary_says_what_its_values_need(client: TestClient) -> None:
    fields = _fields(client, "denomination")

    assert list(fields) == ["currency", "face_value", "kind"]
    assert fields["currency"]["kind"] == "reference"
    assert fields["currency"]["table"] == "currency"
    assert fields["face_value"]["kind"] == "decimal"
    assert fields["kind"]["kind"] == "choice"
    assert fields["kind"]["choices"] == ["coin", "note"]
    assert all(field["required"] for field in fields.values())


def test_a_plain_vocabulary_needs_nothing_more(client: TestClient) -> None:
    assert client.get("/api/reference/set_form").json()["fields"] == []


def test_optional_and_computed_columns_are_told_apart(client: TestClient) -> None:
    grade = _fields(client, "grade")
    assert "grade_rank" not in grade
    assert grade["numeric_value"] == {
        "name": "numeric_value",
        "label": "Numeric value",
        "kind": "integer",
        "required": False,
        "choices": [],
        "table": None,
        "max_length": None,
    }
    assert grade["is_plus"]["kind"] == "boolean"

    series = _fields(client, "series")
    assert series["applies_to"]["choices"] == ["coin", "currency"]
    assert series["applies_to"]["required"] is False
    assert _fields(client, "mint")["mark"]["max_length"] == 4


def test_a_denomination_is_added_by_what_it_is(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    made = client.post(
        "/api/reference/denomination", json=SEVEN_CENTS, headers=admin_headers
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["code"] == "usd_coin_0_07"
    assert body["label"] == "Seven Cents"
    assert body["sort_order"] == 22
    assert body["source"] == "manual"
    assert body["extra"] == {
        "currency": "USD",
        "face_value": "0.0700",
        "kind": "coin",
    }

    row = db.scalar(select(Denomination).where(Denomination.code == "usd_coin_0_07"))
    assert row is not None
    usd = db.scalar(select(Currency.id).where(Currency.code == "USD"))
    assert row.currency_id == usd
    assert row.face_value == Decimal("0.07")
    assert row.kind is DenominationKind.coin

    codes = [
        v["code"] for v in client.get("/api/reference/denomination").json()["values"]
    ]
    assert codes.index("usd_coin_0_05") < codes.index("usd_coin_0_07")
    assert codes.index("usd_coin_0_07") < codes.index("usd_coin_0_10")


def test_a_note_denomination_is_coded_as_it_is_read(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    made = client.post(
        "/api/reference/denomination",
        json={
            "label": "$100,000",
            "extra": {"currency": "USD", "face_value": 100000, "kind": "note"},
        },
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text
    assert made.json()["code"] == "usd_note_100000"


def test_a_face_value_finer_than_a_cent_is_not_rounded_into_its_code(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A quarter cent must not take the code a cent has."""
    made = client.post(
        "/api/reference/denomination",
        json={
            "label": "Quarter Cent",
            "extra": {"currency": "USD", "face_value": "0.0025", "kind": "coin"},
        },
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text
    assert made.json()["code"] == "usd_coin_0_0025"


def test_a_second_denomination_of_one_face_value_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    again = client.post(
        "/api/reference/denomination",
        json={
            "code": "usd_coin_half_dime",
            "label": "Half Dime",
            "extra": {"currency": "USD", "face_value": "0.05", "kind": "coin"},
        },
        headers=admin_headers,
    )
    assert again.status_code == 409
    assert again.json()["detail"] == (
        "denomination already has a value with the same currency, face_value, kind."
    )


def test_a_code_is_named_for_the_label_when_none_is_sent(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    made = client.post(
        "/api/reference/set_form",
        json={"label": "  Collector's   Souvenir Set "},
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text
    assert made.json()["code"] == "collectors_souvenir_set"
    assert made.json()["label"] == "Collector's Souvenir Set"

    again = client.post(
        "/api/reference/set_form",
        json={"label": "Collectors Souvenir Set"},
        headers=admin_headers,
    )
    assert again.status_code == 409


def test_a_label_that_gives_no_code_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/reference/set_form", json={"label": "???"}, headers=admin_headers
    )
    assert res.status_code == 422


@pytest.mark.parametrize(
    ("extra", "said"),
    [
        ({"currency": "ZZZ", "face_value": "0.03", "kind": "coin"}, "currency"),
        ({"currency": "USD", "face_value": "three", "kind": "coin"}, "face_value"),
        ({"currency": "USD", "face_value": "NaN", "kind": "coin"}, "face_value"),
        ({"currency": "USD", "face_value": "0.03", "kind": "token"}, "kind"),
        ({"currency": "USD", "face_value": "0.03"}, "kind"),
        ({"currency": "USD", "face_value": " ", "kind": "coin"}, "face_value"),
        (
            {"currency": "USD", "face_value": "0.03", "kind": "coin", "is_active": 0},
            "is_active",
        ),
    ],
)
def test_what_a_column_cannot_hold_is_a_422_naming_it(
    client: TestClient,
    admin_headers: dict[str, str],
    extra: dict[str, object],
    said: str,
) -> None:
    res = client.post(
        "/api/reference/denomination",
        json={"label": "Seven Cents", "extra": extra},
        headers=admin_headers,
    )
    assert res.status_code == 422, res.text
    assert said in res.json()["detail"]


def test_whole_numbers_and_switches_are_read_strictly(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    for extra in ({"numeric_value": 64.5}, {"numeric_value": True}, {"is_plus": "yes"}):
        res = client.post(
            "/api/reference/grade",
            json={"code": "TESTGRADE", "label": "Test grade", "extra": extra},
            headers=admin_headers,
        )
        assert res.status_code == 422, extra

    made = client.post(
        "/api/reference/grade",
        json={
            "code": "TESTGRADE",
            "label": "Test grade",
            "extra": {"numeric_value": "64", "is_plus": True},
        },
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text
    assert made.json()["extra"]["numeric_value"] == 64
    assert made.json()["extra"]["is_plus"] is True


def test_text_longer_than_its_column_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/reference/mint",
        json={"label": "Test Mint", "extra": {"mark": "TOOLONG"}},
        headers=admin_headers,
    )
    assert res.status_code == 422
    assert "mark" in res.json()["detail"]


@pytest.mark.parametrize("table", ["item_status", "item_kind", "disposition"])
def test_nothing_is_added_to_a_vocabulary_the_application_acts_on(
    client: TestClient, admin_headers: dict[str, str], table: str
) -> None:
    assert client.get(f"/api/reference/{table}").json()["addable"] is False
    res = client.post(
        f"/api/reference/{table}",
        json={"code": "invented", "label": "Invented"},
        headers=admin_headers,
    )
    assert res.status_code == 409
    codes = {v["code"] for v in client.get(f"/api/reference/{table}").json()["values"]}
    assert "invented" not in codes


def test_a_descriptive_vocabulary_can_be_added_to(client: TestClient) -> None:
    assert client.get("/api/reference/denomination").json()["addable"] is True


# -- changing a value's own columns -------------------------------------------


def _added_series(client: TestClient, headers: dict[str, str]) -> None:
    """Add a series by label alone, and check it is taken to be a coin's."""
    made = client.post(
        "/api/reference/series", json={"label": "Horseblanket"}, headers=headers
    )
    assert made.status_code == 201, made.text
    # Left out, a series is a coin's: the column's own default.
    assert made.json()["extra"]["applies_to"] == "coin"


def test_a_values_own_columns_can_be_changed(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A series added as a coin's can be made a note's without leaving the page."""
    _added_series(client, admin_headers)
    path = "/api/reference/series/horseblanket"

    changed = client.patch(
        path,
        json={
            "label": "Horseblanket",
            "extra": {
                "applies_to": "currency",
                "year_start": "1861",
                "denomination": "usd_note_1",
            },
        },
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    extra = changed.json()["extra"]
    assert extra["applies_to"] == "currency"
    assert extra["year_start"] == 1861
    assert extra["denomination"] == "usd_note_1"

    # Only what is named changes; a blank empties a column that may be empty.
    cleared = client.patch(
        path,
        json={
            "label": "Horseblanket",
            "extra": {"denomination": "", "year_start": None},
        },
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    extra = cleared.json()["extra"]
    assert extra["applies_to"] == "currency"
    assert "denomination" not in extra
    assert "year_start" not in extra


def test_a_rename_alone_leaves_the_columns_as_they_are(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _added_series(client, admin_headers)
    renamed = client.patch(
        "/api/reference/series/horseblanket",
        json={"label": "Horse Blanket"},
        headers=admin_headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["label"] == "Horse Blanket"
    assert renamed.json()["extra"]["applies_to"] == "coin"


def test_changing_a_shipped_values_column_makes_it_this_installations(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """So the next seed load does not put the shipped column back."""
    before = client.get("/api/reference/mint").json()["values"]
    denver = next(v for v in before if v["code"] == "D")
    assert denver["source"] == "seeded"

    same = client.patch(
        "/api/reference/mint/D",
        json={"label": denver["label"], "extra": {"mark": "D"}},
        headers=admin_headers,
    )
    assert same.json()["source"] == "seeded"

    changed = client.patch(
        "/api/reference/mint/D",
        json={"label": denver["label"], "extra": {"mark": "DD"}},
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["source"] == "manual"
    assert changed.json()["extra"]["mark"] == "DD"


@pytest.mark.parametrize(
    ("extra", "said"),
    [
        ({"applies_to": "token"}, "applies_to"),
        ({"applies_to": ""}, "applies_to cannot be empty"),
        ({"year_start": "soon"}, "year_start"),
        ({"denomination": "usd_coin_9_99"}, "denomination"),
        ({"is_active": False}, "is_active"),
    ],
)
def test_a_change_a_column_cannot_hold_is_a_422_naming_it(
    client: TestClient,
    admin_headers: dict[str, str],
    extra: dict[str, object],
    said: str,
) -> None:
    _added_series(client, admin_headers)
    res = client.patch(
        "/api/reference/series/horseblanket",
        json={"label": "Horseblanket", "extra": extra},
        headers=admin_headers,
    )
    assert res.status_code == 422, res.text
    assert said in res.json()["detail"]
    kept = client.get("/api/reference/series").json()["values"]
    assert next(v for v in kept if v["code"] == "horseblanket")["extra"] == {
        "applies_to": "coin",
        "needs_evidence": False,
    }


def test_a_change_that_makes_a_second_denomination_of_a_face_value_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.patch(
        "/api/reference/denomination/usd_coin_0_10",
        json={"label": "Dime", "extra": {"face_value": "0.05"}},
        headers=admin_headers,
    )
    assert res.status_code == 409
    assert "currency, face_value, kind" in res.json()["detail"]
