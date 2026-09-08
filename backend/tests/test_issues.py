"""Anomalies as named, kind-aware checks rather than generic field filters.

`issue=no_grade` means *a coin or banknote with no grade*, because bullion has
no grade by nature and 712 rounds have no weight either, so a generic
grade=null would bury 2,965 real cases under rounds that will never have one.
The domain knowledge belongs in the check, defined once, rather than in the
head of whoever types the filter.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import ItemKind
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item


def search(client: TestClient, headers: dict[str, str], query: str) -> dict:
    return client.get(f"/api/inventory/coins/search?{query}", headers=headers).json()


def test_a_named_check_finds_only_its_own_anomaly(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    missing = make_item(db, year_start=None)
    make_item(db, year_start=1881)

    rows = search(client, admin_headers, "issue=no_year")["rows"]
    assert [r["id"] for r in rows] == [missing.id]


def test_no_grade_ignores_bullion(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A round will never have a grade.

    Counting it as an anomaly buries the 2,965 coins that should have one.
    """
    coin = make_item(db, grade_id=None)
    make_item(db, grade_id=None, item_kind_id=code_id(db, ItemKind, "bullion"))

    rows = search(client, admin_headers, "issue=no_grade")["rows"]
    assert [r["id"] for r in rows] == [coin.id]


def test_no_weight_applies_only_to_bullion(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The mirror image: a Morgan's weight is not the interesting gap."""
    round_ = make_item(
        db, fine_weight_ozt=None, item_kind_id=code_id(db, ItemKind, "bullion")
    )
    make_item(db, fine_weight_ozt=None)

    rows = search(client, admin_headers, "issue=no_weight_bullion")["rows"]
    assert [r["id"] for r in rows] == [round_.id]


def test_zero_cost_catches_null_and_zero_alike(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    zero = make_item(db, item_cost=Decimal("0.00"))
    make_item(db, item_cost=Decimal("19.99"))

    rows = search(client, admin_headers, "issue=zero_cost")["rows"]
    assert [r["id"] for r in rows] == [zero.id]


def test_unreviewed_is_the_default_state(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Absent means unconfirmed, which is right for every imported item."""
    item = make_item(db)
    assert search(client, admin_headers, "issue=unreviewed")["total"] == 1

    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )
    assert search(client, admin_headers, "issue=unreviewed")["total"] == 0


def test_a_near_duplicate_serial_is_found_within_one_order(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Exact matching misses `O` for `U`, a dropped digit, `6` for `3`.

    Scoped to one purchase order because that is where mistranscriptions
    cluster, and because two unrelated notes one digit apart are two unrelated
    notes.
    """
    from app.models import CurrencyDetail, PurchaseOrder, Vendor

    vendor = Vendor(name="test-vendor")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, order_number="X1")
    db.add(order)
    db.flush()

    kind = code_id(db, ItemKind, "currency")
    pair = [
        make_item(db, item_kind_id=kind, purchase_order_id=order.id) for _ in range(2)
    ]
    apart = make_item(db, item_kind_id=kind, purchase_order_id=order.id)
    db.add_all(
        [
            CurrencyDetail(inventory_item_id=pair[0].id, serial_number="B08084501A"),
            CurrencyDetail(inventory_item_id=pair[1].id, serial_number="B08084S01A"),
            CurrencyDetail(inventory_item_id=apart.id, serial_number="Z99999999A"),
        ]
    )
    db.commit()

    body = client.get(
        "/api/inventory/currency/search?issue=near_duplicate_serial",
        headers=admin_headers,
    ).json()

    assert sorted(r["id"] for r in body["rows"]) == sorted(i.id for i in pair)


def test_an_unknown_issue_is_refused_listing_the_known_ones(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Silently ignoring it returns the whole collection as a clean bill."""
    response = client.get(
        "/api/inventory/coins/search?issue=no_such_check", headers=admin_headers
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "no_such_check" in detail
    assert "no_year" in detail


def test_issue_counts_come_back_with_the_page(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """So the size of a job is visible before committing to it."""
    make_item(db, year_start=None)
    make_item(db, year_start=None, country_id=None)

    body = client.get(
        "/api/inventory/coins/search?facets=true", headers=admin_headers
    ).json()

    assert body["issues"]["no_year"] == 2
    assert body["issues"]["no_country"] == 1


def test_issue_counts_ignore_the_issue_filter_itself(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Otherwise every count but the selected one collapses.

    The panel stops being a way to choose the next job.
    """
    make_item(db, year_start=None)
    make_item(db, country_id=None)

    body = client.get(
        "/api/inventory/coins/search?issue=no_year&facets=true", headers=admin_headers
    ).json()

    assert body["total"] == 1
    assert body["issues"]["no_country"] == 1


def test_a_currency_only_check_is_not_offered_to_coins(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A star note check over the coin view would be a 422, not empty."""
    response = client.get(
        "/api/inventory/coins/search?issue=star_mismatch", headers=admin_headers
    )
    assert response.status_code == 422
