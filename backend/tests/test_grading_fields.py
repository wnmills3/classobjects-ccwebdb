"""Grading in the item editor: service, designation and certificate numbers.

The editor could set none of them (owner, 2026-09-23: EPQ could not be
chosen for a note). Designations say which kind they fit
(`grade_designation.applies_to`) and the API refuses the other kind's;
certificate numbers are edited as a set on `PATCH /inventory/{id}`.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.models import (
    GradeDesignation,
    GradingService,
    ItemCertification,
    ItemFieldChange,
    ItemKind,
)
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def _patch(
    client: TestClient, headers: dict[str, str], item_id: int, body: dict[str, Any]
) -> Response:
    return client.patch(f"/api/inventory/{item_id}", json=body, headers=headers)


def _certs(db: Session, item_id: int) -> list[ItemCertification]:
    db.expire_all()
    return list(
        db.scalars(
            select(ItemCertification)
            .where(ItemCertification.inventory_item_id == item_id)
            .order_by(ItemCertification.id)
        )
    )


def _note(db: Session) -> int:
    item = build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "currency"),
        country_id=None,
        year_start=None,
    )
    return item.id


def test_designations_say_which_kind_they_fit(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    body = client.get("/api/reference/grade_designation", headers=admin_headers).json()
    sides = {v["code"]: v["extra"]["applies_to"] for v in body["values"]}
    assert sides["EPQ"] == sides["PPQ"] == "currency"
    assert sides["DCAM"] == sides["FBL"] == "coin"


def test_a_note_takes_a_paper_designation_and_a_coin_does_not(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = _note(db)
    ok = _patch(client, admin_headers, note, {"grade_designation": "EPQ"})
    assert ok.status_code == 200, ok.text

    coin = build_bare_item(db)
    refused = _patch(client, admin_headers, coin.id, {"grade_designation": "EPQ"})
    assert refused.status_code == 422
    assert "EPQ belongs to banknotes" in refused.json()["detail"]
    strike = _patch(client, admin_headers, note, {"grade_designation": "DCAM"})
    assert strike.status_code == 422
    assert "DCAM belongs to coins" in strike.json()["detail"]


def test_changing_kind_is_checked_against_the_designation_held(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = build_bare_item(
        db, grade_designation_id=code_id(db, GradeDesignation, "DCAM")
    )
    stranded = _patch(client, admin_headers, coin.id, {"item_kind": "currency"})
    assert stranded.status_code == 422
    assert coin.item_code in stranded.json()["detail"]

    cleared = _patch(
        client,
        admin_headers,
        coin.id,
        {"item_kind": "currency", "grade_designation": None},
    )
    assert cleared.status_code == 200, cleared.text


def test_certificate_numbers_are_edited_as_a_set(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    pmg = code_id(db, GradingService, "PMG")
    item = build_bare_item(db, grading_service_id=pmg)
    db.add(ItemCertification(inventory_item_id=item.id, cert_number="111"))
    db.add(ItemCertification(inventory_item_id=item.id, cert_number="222"))
    db.commit()
    kept_id = _certs(db, item.id)[0].id

    response = _patch(
        client, admin_headers, item.id, {"cert_numbers": ["111", " 8061234-005 "]}
    )
    assert response.status_code == 200, response.text

    rows = _certs(db, item.id)
    assert [r.cert_number for r in rows] == ["111", "8061234-005"]
    # Kept as it was, not re-created; the new one graded by the item's service.
    assert rows[0].id == kept_id and rows[0].grading_service_id is None
    assert rows[1].grading_service_id == pmg
    detail = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert detail["cert_numbers"] == ["111", "8061234-005"]

    logged = db.scalars(
        select(ItemFieldChange).where(
            ItemFieldChange.inventory_item_id == item.id,
            ItemFieldChange.field_name == "cert_numbers",
        )
    ).one()
    assert (logged.old_value, logged.new_value) == (
        ["111", "222"],
        ["111", "8061234-005"],
    )

    cleared = _patch(client, admin_headers, item.id, {"cert_numbers": []})
    assert cleared.status_code == 200
    assert _certs(db, item.id) == []


def test_a_certificate_added_with_a_service_takes_that_service(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db)
    response = _patch(
        client,
        admin_headers,
        item.id,
        {"grading_service": "NGC", "cert_numbers": ["5790123-001"]},
    )
    assert response.status_code == 200, response.text
    (row,) = _certs(db, item.id)
    assert row.grading_service_id == code_id(db, GradingService, "NGC")


def test_a_certificate_change_moves_the_version(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db)
    before = item.version
    _patch(client, admin_headers, item.id, {"cert_numbers": ["1"]})
    after = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert after["version"] > before


@pytest.mark.parametrize(
    ("numbers", "reason"),
    [(None, "may not be null"), (["9", "9"], "at most once"), (["  "], "at least 1")],
)
def test_bad_certificate_lists_are_refused(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    numbers: list[str] | None,
    reason: str,
) -> None:
    item = build_bare_item(db)
    response = _patch(client, admin_headers, item.id, {"cert_numbers": numbers})
    assert response.status_code == 422
    assert reason in response.text
