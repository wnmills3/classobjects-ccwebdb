"""`sl_ready`: which items in hand could be listed now, and what the rest lack."""

from __future__ import annotations

from decimal import Decimal

from app import image_links
from app.image_store import ingest
from app.models import (
    Disposition,
    Grade,
    InventoryItem,
    ItemKind,
    ItemStatus,
    StorageLocation,
    StorageLocationKind,
)
from app.reports.selling import SL_READY, ReadyParams
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id
from tests.test_image_enlarge import _picture

MEASURES = ("items", "photographed", "described", "located", "costed", "ready")


def _box(db: Session) -> StorageLocation:
    """A safe-deposit box to keep things in."""
    box = StorageLocation(
        storage_location_kind_id=code_id(db, StorageLocationKind, "safe_deposit_box"),
        institution="First National",
        identifier="804",
    )
    db.add(box)
    db.flush()
    return box


def _grade_id(db: Session) -> int:
    """Any grade there is: which one does not matter here."""
    grade_id = db.scalar(select(Grade.id).order_by(Grade.id).limit(1))
    assert grade_id is not None
    return grade_id


def _photograph(db: Session, item: InventoryItem, color: str, url: str | None) -> None:
    """File a photograph on `item`: the owner's own, or fetched from `url`."""
    image = ingest(db, _picture(color, (40, 30)), f"{color}.jpg", url)
    image_links.attach(db, image=image, item=item, role=None, is_primary=True)


def _piece(db: Session, kind: str = "coin", **columns: object) -> InventoryItem:
    """A received, held piece of this kind with nothing a listing needs."""
    columns.setdefault("item_cost", Decimal("0"))
    columns.setdefault("shipping_cost", Decimal("0"))
    columns.setdefault("tax_rate", Decimal("0"))
    return build_bare_item(db, item_kind_id=code_id(db, ItemKind, kind), **columns)


def _row(db: Session, kind_label: str) -> dict[str, object]:
    """The report's row for one kind."""
    result = SL_READY.run(db, ReadyParams())
    return next(row for row in result.rows if row["kind"] == kind_label)


def _counts(row: dict[str, object]) -> tuple[object, ...]:
    """A row's measures, in column order."""
    return tuple(row[key] for key in MEASURES)


def test_each_thing_a_listing_needs_is_counted_on_its_own(db: Session) -> None:
    box = _box(db)
    grade = _grade_id(db)
    # Five coins, each with one thing more than the last; only the fifth
    # has all four. Made in an order that is not the order of readiness.
    everything = _piece(
        db, grade_id=grade, storage_location_id=box.id, item_cost=Decimal("25.00")
    )
    _photograph(db, everything, "navy", None)
    nothing = _piece(db)
    photographed = _piece(db)
    _photograph(db, photographed, "olive", None)
    graded = _piece(db, grade_id=grade)
    located_and_costed = _piece(
        db, storage_location_id=box.id, item_cost=Decimal("9.00")
    )
    db.commit()
    assert all([nothing, graded, located_and_costed])

    #                         items photo described located costed ready
    assert _counts(_row(db, "Coin")) == (5, 2, 2, 2, 2, 1)


def test_a_seller_s_listing_picture_is_not_an_own_photograph(db: Session) -> None:
    box = _box(db)
    complete = {
        "grade_id": _grade_id(db),
        "storage_location_id": box.id,
        "item_cost": Decimal("25.00"),
    }
    sellers = _piece(db, "coin", **complete)
    _photograph(db, sellers, "navy", "https://i.ebayimg.com/images/g/a/s-l1600.jpg")
    both = _piece(db, "coin", **complete)
    _photograph(db, both, "olive", "https://i.ebayimg.com/images/g/b/s-l1600.jpg")
    own = ingest(db, _picture("teal", (40, 30)), "own.jpg")
    image_links.attach(db, image=own, item=both, role=None, is_primary=False)
    db.commit()

    # Two coins, each with all else; only the one with a picture of its own
    # is photographed, and so only it is ready.
    assert _counts(_row(db, "Coin")) == (2, 1, 2, 2, 2, 1)


def test_bullion_is_described_by_its_weight_and_a_set_by_nothing(db: Session) -> None:
    grade = _grade_id(db)
    # A grade does not describe a bar; a weight does.
    _piece(db, "bullion", grade_id=grade)
    _piece(db, "bullion", fine_weight_ozt=Decimal("1"))
    # A weight does not describe a coin; a grade does.
    _piece(db, "coin", fine_weight_ozt=Decimal("0.7734"))
    _piece(db, "set")
    _piece(db, "set")
    db.commit()

    assert _row(db, "Bullion")["described"] == 1
    assert _row(db, "Coin")["described"] == 0
    assert _counts(_row(db, "Set"))[:3] == (2, 0, 2)


def test_only_items_in_hand_and_not_already_offered_are_counted(db: Session) -> None:
    in_hand = _piece(db)
    _piece(db, status_id=code_id(db, ItemStatus, "ordered"))
    _piece(db, disposition_id=code_id(db, Disposition, "listed"))
    _piece(db, disposition_id=code_id(db, Disposition, "sold"))
    gone = _piece(db)
    gone.deleted_at = in_hand.created_at
    db.commit()

    assert _row(db, "Coin")["items"] == 1


def test_the_total_adds_the_kinds_and_each_row_opens_its_items(db: Session) -> None:
    _piece(db, "coin", item_cost=Decimal("5.00"))
    _piece(db, "currency", item_cost=Decimal("5.00"))
    _piece(db, "currency")
    db.commit()

    result = SL_READY.run(db, ReadyParams())

    assert [row["kind"] for row in result.rows] == ["Coin", "Currency"]
    assert result.totals == {
        "kind": "All kinds",
        "items": 3,
        "photographed": 0,
        "described": 0,
        "located": 0,
        "costed": 2,
        "ready": 0,
    }
    assert result.drills == [
        "/inventory/coins?status=received&kind=coin",
        "/inventory/currency?status=received",
    ]
    assert len(result.notes) == 4


def test_with_nothing_in_hand_there_are_no_rows_and_no_total(db: Session) -> None:
    result = SL_READY.run(db, ReadyParams())
    assert (result.rows, result.totals, result.drills) == ([], None, [])


def test_the_report_is_offered_and_runs_over_the_api(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _piece(db, item_cost=Decimal("5.00"))
    db.commit()

    listed = client.get("/api/reports", headers=admin_headers).json()
    assert "sl_ready" in {report["id"] for report in listed}

    res = client.get("/api/reports/sl_ready", headers=admin_headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert [column["label"] for column in body["columns"]] == [
        "Kind",
        "In hand, not offered",
        "Own photograph",
        "Graded or weighed",
        "Location",
        "Cost",
        "Ready to sell",
    ]
    assert body["rows"][0]["costed"] == 1
