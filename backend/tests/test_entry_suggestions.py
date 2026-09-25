"""What the New item form suggests before the item exists.

`POST /api/inventory/suggested-description` describes the form's draft the
way the editor's Suggest describes a saved item; `GET /api/defaults/note`
says so when no issue of the denomination is of the series typed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest
from app.models import AppliesTo, ErrorType, InventoryItem, PurchaseOrder, Vendor
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

DRAFT = "/api/inventory/suggested-description"

COIN = {
    "item_kind": "coin",
    "year_start": 1881,
    "country": "US",
    "denomination": "usd_coin_1_00",
    "series": "morgan_dollar",
    "grade": "MS64",
    "mint": "S",
    "grading_service": "PCGS",
}
NOTE = {
    "item_kind": "currency",
    "denomination": "usd_note_1",
    "grade": "N64",
    "grade_designation": "EPQ",
    "series_year": 1957,
    "series_letter": "B",
    "note_type": "silver_certificate",
    "seal_color": "blue",
    # No pattern in its digits: a fancy serial is tested on its own below,
    # because the save does not record the attributes it earns.
    "serial_number": "A31415926B",
}


def _order(db: Session) -> PurchaseOrder:
    vendor = Vendor(name="Draft Test Vendor")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id)
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _error_code(db: Session, side: AppliesTo) -> str:
    """An error type the kind may carry."""
    return db.scalars(
        select(ErrorType.code)
        .where(ErrorType.applies_to.in_([side, AppliesTo.any]))
        .order_by(ErrorType.id)
    ).first() or pytest.fail(f"no {side.value} error type seeded")


def _saved_description(
    client: TestClient,
    headers: dict[str, str],
    order: PurchaseOrder,
    fields: dict[str, object],
    errors: Sequence[Mapping[str, object]],
) -> str:
    created = client.post(
        "/api/inventory",
        json={**fields, "purchase_order_id": order.id, "source_title": "t"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    if errors:
        put = client.put(
            f"/api/inventory/{item_id}/errors", json={"errors": errors}, headers=headers
        )
        assert put.status_code == 200, put.text
    got = client.get(f"/api/inventory/{item_id}/suggested-description", headers=headers)
    assert got.status_code == 200, got.text
    return str(got.json()["description"])


def test_a_draft_coin_reads_as_the_same_coin_saved(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Through the save and through the draft: one wording, composition too."""
    errors = [{"error_type": _error_code(db, AppliesTo.coin), "details": "x"}]
    saved = _saved_description(client, admin_headers, _order(db), COIN, errors)
    draft = client.post(DRAFT, json={**COIN, "errors": errors}, headers=admin_headers)
    assert draft.status_code == 200, draft.text
    assert draft.json()["description"] == saved
    # The composition filled the metal and weight, as the save does.
    assert "ozt fine" in saved


def test_a_draft_note_reads_as_the_same_note_saved(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    errors = [{"error_type": _error_code(db, AppliesTo.currency), "details": None}]
    saved = _saved_description(client, admin_headers, _order(db), NOTE, errors)
    draft = client.post(DRAFT, json={**NOTE, "errors": errors}, headers=admin_headers)
    assert draft.status_code == 200, draft.text
    assert draft.json()["description"] == saved
    assert "1957B $1 S/N A31415926B" in saved


def test_a_fancy_serial_is_promoted_before_it_is_saved(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Three distinct digits: a trinary, named right after the grade."""
    draft = client.post(
        DRAFT,
        json={**NOTE, "serial_number": "B12211221A", "grade_designation": None},
        headers=admin_headers,
    )
    assert draft.status_code == 200, draft.text
    text_ = draft.json()["description"]
    first = text_.split(" 1957B ")[0]
    assert "Trinary" not in first  # 12211221 has two digits: a binary
    assert "Binary" in first and "Radar" in first
    # The umbrella says nothing beside the pattern itself.
    assert "Fancy" not in first


def test_a_draft_writes_nothing_and_takes_no_item_code(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """An item code taken and rolled back would leave a gap in the CC- numbers."""
    before_items = db.scalar(select(func.count()).select_from(InventoryItem))
    before_code = db.execute(text("SELECT last_value FROM item_code_seq")).scalar()
    for body in (COIN, NOTE):
        assert client.post(DRAFT, json=body, headers=admin_headers).status_code == 200
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(InventoryItem)) == before_items
    after_code = db.execute(text("SELECT last_value FROM item_code_seq")).scalar()
    assert after_code == before_code


def test_an_empty_draft_suggests_nothing_and_a_bad_code_is_named(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    empty = client.post(DRAFT, json={"item_kind": "coin"}, headers=admin_headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["description"] == ""
    bad = client.post(DRAFT, json={**COIN, "mint": "ZZ"}, headers=admin_headers)
    assert bad.status_code == 422
    assert "mint" in bad.text
    wrong_error = client.post(
        DRAFT,
        json={**COIN, "errors": [{"error_type": "no_such"}]},
        headers=admin_headers,
    )
    assert wrong_error.status_code == 422
    assert "no_such" in wrong_error.text


# -- a series with no issue -----------------------------------------------------


def _note_lookup(
    client: TestClient, headers: dict[str, str], **params: object
) -> dict[str, object]:
    response = client.get("/api/defaults/note", params=params, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_a_series_no_note_was_issued_in_is_said_and_the_real_ones_named(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The owner's $2 1953-E: the series ends at 1953-C."""
    found = _note_lookup(
        client,
        admin_headers,
        denomination="usd_note_2",
        series_year=1953,
        series_letter="E",
    )
    assert found["signature_combination"] is None
    assert found["warning"] == (
        "No $2 note of Series 1953E is on record. "
        "Series 1953 on record: 1953, 1953A, 1953B, 1953C."
    )


def test_a_real_series_brings_its_signatures_and_no_warning(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    found = _note_lookup(
        client,
        admin_headers,
        denomination="usd_note_2",
        series_year=1953,
        series_letter="b",  # typed lower case
    )
    assert found["warning"] is None
    assert found["signature_combination"] == "smith_dillon"


def test_a_year_with_no_issue_lists_the_years_that_have_one(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    found = _note_lookup(
        client, admin_headers, denomination="usd_note_2", series_year=1954
    )
    warning = str(found["warning"])
    assert warning.startswith("No $2 note of Series 1954 is on record. $2 note series")
    assert "1928, 1953, 1963" in warning


def test_no_warning_where_the_record_does_not_cover_the_note(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A large-size note, or a denomination the record holds no issues of."""
    large = _note_lookup(
        client, admin_headers, denomination="usd_note_2", series_year=1917
    )
    assert large["warning"] is None
    foreign = _note_lookup(
        client, admin_headers, denomination="mxn_note_5", series_year=1953
    )
    assert foreign["warning"] is None
