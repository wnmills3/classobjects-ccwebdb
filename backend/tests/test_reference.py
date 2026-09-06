"""The classifier vocabularies, as dropdowns consume them."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Grade, ProvenanceSource


def test_the_available_vocabularies_are_listed(client: TestClient) -> None:
    tables = client.get("/api/reference").json()
    assert "grade" in tables
    assert "item_kind" in tables
    assert "denomination" in tables


def test_a_vocabulary_comes_back_ready_for_a_picker(client: TestClient) -> None:
    body = client.get("/api/reference/item_kind").json()
    codes = [v["code"] for v in body["values"]]

    assert body["table"] == "item_kind"
    assert "coin" in codes and "bullion" in codes
    # Ordered by sort_order, so the picker shows them in a sensible sequence
    # rather than alphabetically or by insertion.
    orders = [v["sort_order"] for v in body["values"]]
    assert orders == sorted(orders)


def test_the_endpoint_is_public(client: TestClient) -> None:
    """The catalogue's own filters need it, and a vocabulary is not data:
    knowing MS64 exists reveals nothing about what anyone owns."""
    assert client.get("/api/reference/grade").status_code == 200


def test_table_specific_columns_come_through(client: TestClient) -> None:
    """A denomination's face value and an error type's applies_to are what
    make those pickers usable, and they differ per table -- so they arrive in
    `extra` rather than forcing a response model per table."""
    denominations = client.get("/api/reference/denomination").json()["values"]
    dollar = next(v for v in denominations if v["code"] == "usd_coin_1_00")
    assert dollar["extra"]["face_value"] == "1.0000"
    assert dollar["extra"]["kind"] == "coin"

    errors = client.get("/api/reference/error_type").json()["values"]
    doubled = next(v for v in errors if v["code"] == "doubled_die")
    assert doubled["extra"]["applies_to"] == "coin"


def test_foreign_keys_come_through_as_codes_not_ids(client: TestClient) -> None:
    """The same rule the rest of the API follows: a client never has to know
    an id, because ids differ between installations."""
    grades = client.get("/api/reference/grade").json()["values"]
    ms65 = next(v for v in grades if v["code"] == "MS65")

    assert ms65["extra"]["grade_scale"] == "sheldon"
    assert "grade_scale_id" not in ms65["extra"]


def test_grades_carry_the_number_that_makes_them_sortable(
    client: TestClient,
) -> None:
    grades = client.get("/api/reference/grade").json()["values"]
    ms65 = next(v for v in grades if v["code"] == "MS65")
    bu = next(v for v in grades if v["code"] == "BU")

    assert ms65["extra"]["numeric_value"] == 65
    # Adjectival grades carry no number rather than a fictional one.
    assert "numeric_value" not in bu["extra"]


def test_provenance_is_visible(client: TestClient, db: Session) -> None:
    """A picker can distinguish shipped vocabulary from values a local import
    invented, which is the same distinction that gates exporting."""
    db.add(Grade(code="LOCAL_X", label="Local", source=ProvenanceSource.derived))
    db.commit()

    values = client.get("/api/reference/grade").json()["values"]
    local = next(v for v in values if v["code"] == "LOCAL_X")
    seeded = next(v for v in values if v["code"] == "MS65")

    assert local["source"] == "derived"
    assert seeded["source"] == "seeded"


def test_retired_values_are_hidden_but_reachable(
    client: TestClient, db: Session
) -> None:
    """An old record may still reference a classifier that should not be
    offered for new ones, and the form still has to render it."""
    db.add(
        Grade(code="RETIRED_X", label="Retired", is_active=False,
              source=ProvenanceSource.manual)
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
