"""The classifier vocabularies, as dropdowns consume them."""

from __future__ import annotations

import json

import pytest
from app.models import Grade, ProvenanceSource
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_the_available_vocabularies_are_listed(client: TestClient) -> None:
    tables = client.get("/api/reference").json()
    assert "grade" in tables
    assert "item_kind" in tables
    assert "denomination" in tables


def test_a_vocabulary_comes_back_ready_for_a_picker(client: TestClient) -> None:
    body = client.get("/api/reference/item_kind").json()
    codes = [v["code"] for v in body["values"]]

    assert body["table"] == "item_kind"
    assert "coin" in codes
    assert "bullion" in codes
    # Ordered by sort_order, so the picker shows them in a sensible sequence
    # rather than alphabetically or by insertion.
    orders = [v["sort_order"] for v in body["values"]]
    assert orders == sorted(orders)


def test_the_endpoint_is_public(client: TestClient) -> None:
    """Vocabularies are public; they reveal nothing held.

    The catalogue's own filters need it, and a vocabulary is not data:
    knowing MS64 exists reveals nothing about what anyone owns.
    """
    assert client.get("/api/reference/grade").status_code == 200


def test_table_specific_columns_come_through(client: TestClient) -> None:
    """Table-specific columns travel in `extra`.

    A denomination's face value and an error type's applies_to are what
    make those pickers usable, and they differ per table -- so they arrive in
    `extra` rather than forcing a response model per table.
    """
    denominations = client.get("/api/reference/denomination").json()["values"]
    dollar = next(v for v in denominations if v["code"] == "usd_coin_1_00")
    assert dollar["extra"]["face_value"] == "1.0000"
    assert dollar["extra"]["kind"] == "coin"

    errors = client.get("/api/reference/error_type").json()["values"]
    doubled = next(v for v in errors if v["code"] == "doubled_die")
    assert doubled["extra"]["applies_to"] == "coin"


def test_foreign_keys_come_through_as_codes_not_ids(client: TestClient) -> None:
    """Foreign keys inside a vocabulary resolve to codes.

    The same rule the rest of the API follows: a client never has to know
    an id, because ids differ between installations.
    """
    grades = client.get("/api/reference/grade").json()["values"]
    ms65 = next(v for v in grades if v["code"] == "65")

    assert ms65["extra"]["grade_scale"] == "sheldon"
    assert "grade_scale_id" not in ms65["extra"]


def test_grades_carry_the_number_that_makes_them_sortable(
    client: TestClient,
) -> None:
    grades = client.get("/api/reference/grade").json()["values"]
    ms65 = next(v for v in grades if v["code"] == "65")
    circulated = next(v for v in grades if v["code"] == "CIRC")

    assert ms65["extra"]["numeric_value"] == 65
    # A grade with no number carries none rather than a fictional one.
    assert "numeric_value" not in circulated["extra"]


def test_provenance_is_visible(client: TestClient, db: Session) -> None:
    """Provenance is visible on every value.

    A picker can distinguish shipped vocabulary from values a local import
    invented, which is the same distinction that gates exporting.
    """
    db.add(Grade(code="LOCAL_X", label="Local", source=ProvenanceSource.derived))
    db.commit()

    values = client.get("/api/reference/grade").json()["values"]
    local = next(v for v in values if v["code"] == "LOCAL_X")
    seeded = next(v for v in values if v["code"] == "65")

    assert local["source"] == "derived"
    assert seeded["source"] == "seeded"


def test_retired_values_are_hidden_but_reachable(
    client: TestClient, db: Session
) -> None:
    """Retired values are hidden but still fetchable.

    An old record may still reference a classifier that should not be
    offered for new ones, and the form still has to render it.
    """
    db.add(
        Grade(
            code="RETIRED_X",
            label="Retired",
            is_active=False,
            source=ProvenanceSource.manual,
        )
    )
    db.commit()

    visible = {v["code"] for v in client.get("/api/reference/grade").json()["values"]}
    assert "RETIRED_X" not in visible

    everything = client.get("/api/reference/grade?include_inactive=true").json()
    assert "RETIRED_X" in {v["code"] for v in everything["values"]}


def test_an_unknown_vocabulary_is_a_404(client: TestClient) -> None:
    response = client.get("/api/reference/not_a_table")
    assert response.status_code == 404
    assert "not_a_table" in response.json()["detail"]


def test_every_listed_vocabulary_can_actually_be_fetched(
    client: TestClient,
) -> None:
    """Guards against the index and the handler drifting apart."""
    for table in client.get("/api/reference").json():
        response = client.get(f"/api/reference/{table}")
        assert response.status_code == 200, f"{table} listed but not fetchable"
        assert response.json()["table"] == table


# ---------------------------------------------------------------------------
# Picker order: alphabetical, except where the order is the meaning
# ---------------------------------------------------------------------------


def test_a_descriptive_vocabulary_comes_back_alphabetically(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Attributes are scanned by name; their seeded sort_order groups them."""
    labels = [
        value["label"]
        for value in client.get("/api/reference/item_attribute").json()["values"]
    ]

    assert labels == sorted(labels, key=str.lower)


def test_grades_keep_their_scale_order(client: TestClient) -> None:
    """70, 69+, 69 ... is the scale's own order; alphabetical would be noise."""
    codes = [v["code"] for v in client.get("/api/reference/grade").json()["values"]]

    assert codes.index("70") < codes.index("65") < codes.index("50")


def test_a_lifecycle_vocabulary_keeps_its_sequence(client: TestClient) -> None:
    """Ordered -> Received -> ... is read constantly; alphabetical scrambles it."""
    codes = [
        v["code"] for v in client.get("/api/reference/item_status").json()["values"]
    ]

    assert codes.index("ordered") < codes.index("received") < codes.index("canceled")


def test_item_kind_keeps_its_curated_order(client: TestClient) -> None:
    """Coin and currency lead, ranked by how often a kind occurs.

    Alphabetical would put Bullion first, ahead of the two kinds that
    between them cover the whole collection.
    """
    codes = [v["code"] for v in client.get("/api/reference/item_kind").json()["values"]]

    assert codes.index("coin") < codes.index("currency") < codes.index("bullion")


def test_signature_combinations_keep_their_chronological_order(
    client: TestClient,
) -> None:
    """Oldest first: the picker narrows to a stretch of this timeline by year."""
    codes = [
        v["code"]
        for v in client.get("/api/reference/signature_combination").json()["values"]
    ]

    assert codes.index("tate_mellon") < codes.index("woods_mellon")


def test_a_value_added_late_still_sorts_alphabetically(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A mid-entry addition appears where it is looked for, not at the end."""
    response = client.post(
        "/api/reference/item_attribute",
        json={
            "code": "aardvark_test",
            "label": "Aardvark Test",
            "extra": {"applies_to": "any", "attribute_group": "variety"},
        },
        headers=admin_headers,
    )
    assert response.status_code == 201

    labels = [
        v["label"] for v in client.get("/api/reference/item_attribute").json()["values"]
    ]

    assert labels[0] == "Aardvark Test"


# ---------------------------------------------------------------------------
# Growing the vocabularies with use
# ---------------------------------------------------------------------------


def test_a_value_can_be_added_while_picking(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A value missing from a picker can be added from it.

    The organic-growth path: an operator entering an item that does not fit
    the shipped vocabulary adds the missing value rather than abandoning the
    entry or forcing it into an approximate one.
    """
    response = client.post(
        "/api/reference/grade",
        json={"code": "MS64PL", "label": "MS-64 Prooflike"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "MS64PL"

    # Marked as this installation's own, not shipped catalogue.
    assert body["source"] == "manual"

    values = client.get("/api/reference/grade").json()["values"]
    assert "MS64PL" in {v["code"] for v in values}


def test_added_values_do_not_leak_into_a_shared_export(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """An installation's own values stay out of an export.

    `manual` is what keeps one collection's additions out of a catalogue
    handed to another installation.
    """
    client.post(
        "/api/reference/grade",
        json={"code": "HOUSE_X", "label": "House grade"},
        headers=admin_headers,
    )
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from app.seeding import export_reference_data

    with TemporaryDirectory() as tmp:
        export_reference_data(db, Path(tmp), sources=["seeded"])
        payload = json.loads((Path(tmp) / "grade.json").read_text(encoding="utf-8"))
    assert "HOUSE_X" not in {r["code"] for r in payload["grade"]}


def test_adding_a_duplicate_code_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/reference/grade",
        json={"code": "65", "label": "Another MS-65"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_adding_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/reference/grade",
        json={"code": "ANY", "label": "Any"},
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_a_table_specific_column_can_be_supplied(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/reference/error_type",
        json={
            "code": "clashed_dies",
            "label": "Clashed Dies",
            "extra": {"applies_to": "coin"},
        },
        headers=admin_headers,
    )
    assert response.status_code == 201
    assert response.json()["extra"]["applies_to"] == "coin"


def test_a_missing_required_column_is_explained(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A missing required column is explained, not a 500.

    A denomination without a currency or a face value cannot exist, and the
    caller should be told which, not handed a 500.
    """
    response = client.post(
        "/api/reference/denomination",
        json={"code": "usd_coin_3_00", "label": "Three Dollars"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_an_unknown_column_is_named(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/reference/grade",
        json={"code": "X1", "label": "X", "extra": {"nonsense": 1}},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "nonsense" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Renaming
# ---------------------------------------------------------------------------


def test_renaming_a_label_takes_effect_everywhere_at_once(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A rename reaches every record at once.

    The payoff for keeping one copy: every record refers to the value by
    foreign key, so there is nothing to migrate.
    """
    from app.models import InventoryItem, ItemKind

    from tests.test_schema import code_id, make_item

    item = make_item(
        db,
        item_kind_id=code_id(db, ItemKind, "coin"),
        grade_id=code_id(db, Grade, "65"),
    )

    response = client.patch(
        "/api/reference/grade/65",
        json={"label": "MS-65 (Gem Uncirculated)"},
        headers=admin_headers,
    )
    assert response.status_code == 200

    db.expire_all()
    refreshed = db.get(InventoryItem, item.id)
    assert refreshed.grade.label == "MS-65 (Gem Uncirculated)"
    # ...and the code, which is the contract, is untouched.
    assert refreshed.grade.code == "65"


def test_renaming_does_not_change_the_code(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Renaming changes the label and never the code.

    The code appears in saved filters and bookmarked searches, so renaming
    the label is precisely the operation that must not break them.
    """
    client.patch(
        "/api/reference/grade/64", json={"label": "Renamed"}, headers=admin_headers
    )
    values = client.get("/api/reference/grade").json()["values"]
    entry = next(v for v in values if v["code"] == "64")
    assert entry["label"] == "Renamed"


def test_a_value_can_be_retired_without_breaking_existing_records(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A value can be retired without breaking old records.

    Deleting is refused by the foreign keys anyway; retiring removes it from
    the pickers while leaving old records valid.
    """
    from app.models import ItemKind

    from tests.test_schema import code_id, make_item

    make_item(
        db,
        item_kind_id=code_id(db, ItemKind, "coin"),
        grade_id=code_id(db, Grade, "8"),
    )

    client.patch(
        "/api/reference/grade/8",
        json={"label": "VG-8", "is_active": False},
        headers=admin_headers,
    )

    offered = {v["code"] for v in client.get("/api/reference/grade").json()["values"]}
    assert "8" not in offered
    everything = client.get("/api/reference/grade?include_inactive=true").json()
    assert "8" in {v["code"] for v in everything["values"]}


def _value(client: TestClient, table: str, code: str) -> dict:
    body = client.get(f"/api/reference/{table}?include_inactive=true").json()
    return next(v for v in body["values"] if v["code"] == code)


@pytest.mark.parametrize(
    ("table", "code"),
    [
        ("item_status", "received"),
        ("disposition", "held"),
        ("strike_type", "proof"),
        ("storage_form", "single"),
        ("country", "US"),
    ],
)
def test_a_value_the_application_looks_up_cannot_be_retired(
    client: TestClient, admin_headers: dict[str, str], table: str, code: str
) -> None:
    """Retiring "received" would stop receiving; renaming it is only words."""
    label = _value(client, table, code)["label"]
    assert _value(client, table, code)["retirable"] is False

    refused = client.patch(
        f"/api/reference/{table}/{code}",
        json={"label": label, "is_active": False},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert "looks it up by its code" in refused.json()["detail"]
    assert _value(client, table, code)["is_active"] is True

    renamed = client.patch(
        f"/api/reference/{table}/{code}",
        json={"label": f"{label} (renamed)"},
        headers=admin_headers,
    )
    assert renamed.status_code == 200
    assert renamed.json()["label"] == f"{label} (renamed)"


def test_a_descriptive_value_can_be_retired_and_restored(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    assert _value(client, "grade_designation", "FT")["retirable"] is True
    url = "/api/reference/grade_designation/FT"
    label = _value(client, "grade_designation", "FT")["label"]

    retired = client.patch(
        url, json={"label": label, "is_active": False}, headers=admin_headers
    )
    assert retired.json()["is_active"] is False
    # Retiring alone is not a change of wording: the shipped row stays shipped.
    assert retired.json()["source"] == "seeded"

    restored = client.patch(
        url, json={"label": label, "is_active": True}, headers=admin_headers
    )
    assert restored.json()["is_active"] is True


def test_a_renamed_value_keeps_its_name_through_a_seed_load(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A person's wording outranks the shipped one, so it becomes manual."""
    from app.models import GradeDesignation
    from app.seeding import _seed_table

    response = client.patch(
        "/api/reference/grade_designation/DCAM",
        json={"label": "  Deep   Cameo "},
        headers=admin_headers,
    )
    assert response.json()["label"] == "Deep Cameo"
    assert response.json()["source"] == "manual"

    shipped = [{"code": "DCAM", "label": "DCAM (Deep Cameo)", "sort_order": 10}]
    assert _seed_table(db, GradeDesignation, shipped) == {"skipped_manual": 1}
    assert _value(client, "grade_designation", "DCAM")["label"] == "Deep Cameo"


def test_a_blank_label_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/reference/grade_designation/DCAM",
        json={"label": "   "},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert _value(client, "grade_designation", "DCAM")["label"] == "DCAM (Deep Cameo)"


def test_only_staff_rename(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    url = "/api/reference/grade_designation/DCAM"
    assert client.patch(url, json={"label": "x"}).status_code == 401
    assert (
        client.patch(url, json={"label": "x"}, headers=customer_headers).status_code
        == 403
    )


def test_renaming_an_unknown_value_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/reference/grade/NOPE", json={"label": "x"}, headers=admin_headers
    )
    assert response.status_code == 404


def test_renaming_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    response = client.patch(
        "/api/reference/grade/65", json={"label": "x"}, headers=customer_headers
    )
    assert response.status_code == 403
