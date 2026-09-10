"""Sales tax on an acquisition: a configured default, stamped onto each row.

The rate, and whether shipping is taxed, are settings -- but a setting is read
once, when the item is created, and copied onto the row. Tax paid is a
historical fact. Reading the setting at query time instead would let one
change to it silently rewrite the cost basis of every purchase ever made.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.config import Settings, settings
from app.models import InventoryItem
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def configure(monkeypatch: pytest.MonkeyPatch, rate: str, *, shipping: bool) -> None:
    """Set both tax settings for the rest of one test."""
    monkeypatch.setattr(settings, "sales_tax_rate", Decimal(rate))
    monkeypatch.setattr(settings, "sales_tax_includes_shipping", shipping)


def test_the_shipped_default_is_6_35_percent_including_shipping() -> None:
    """The defaults reproduce every figure the database held before them.

    Whatnot's own order report confirms both: 375.74 x 6.35% = 23.86, exactly
    what it charged, shipping included.
    """
    fields = Settings.model_fields
    assert fields["sales_tax_rate"].default == Decimal("0.0635")
    assert fields["sales_tax_includes_shipping"].default is True


# ---------------------------------------------------------------------------
# The generated column
# ---------------------------------------------------------------------------


def test_shipping_is_taxed_when_the_row_says_so(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, "0.0635", shipping=True)
    item = make_item(db, item_cost=Decimal("100.00"), shipping_cost=Decimal("10.00"))

    # 110.00 x 0.0635 = 6.985 -> 6.99
    assert item.sales_tax == Decimal("6.99")
    assert item.total_cost == Decimal("116.99")


def test_shipping_is_left_out_when_the_row_says_so(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, "0.0635", shipping=False)
    item = make_item(db, item_cost=Decimal("100.00"), shipping_cost=Decimal("10.00"))

    # 100.00 x 0.0635 = 6.35; the 10.00 of shipping carries no tax but is
    # still part of what was paid.
    assert item.sales_tax == Decimal("6.35")
    assert item.total_cost == Decimal("116.35")


# ---------------------------------------------------------------------------
# Stamped at creation, never after
# ---------------------------------------------------------------------------


def test_a_new_item_takes_the_configured_rate_and_rule(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(monkeypatch, "0.0700", shipping=False)
    item = make_item(db, item_cost=Decimal("100.00"), shipping_cost=Decimal("10.00"))

    assert item.tax_rate == Decimal("0.0700")
    assert item.tax_includes_shipping is False
    assert item.sales_tax == Decimal("7.00")


def test_changing_the_setting_later_leaves_existing_items_alone(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guarantee the design rests on: the setting is not re-read on save.

    The item is re-saved after the change, through the ordinary edit path,
    because restamping on update is the way this would actually break -- a
    row nobody touches could not be affected by any default either way.
    """
    configure(monkeypatch, "0.0635", shipping=True)
    item = make_item(db, item_cost=Decimal("100.00"), shipping_cost=Decimal("10.00"))

    configure(monkeypatch, "0.0800", shipping=False)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "Renamed after the rate changed"},
        headers=admin_headers,
    )
    assert response.status_code == 200

    db.refresh(item)
    assert item.tax_rate == Decimal("0.0635")
    assert item.tax_includes_shipping is True
    assert item.sales_tax == Decimal("6.99")


# ---------------------------------------------------------------------------
# Correcting it: one item, many items, and what is refused
# ---------------------------------------------------------------------------


def test_no_tax_charged_is_a_rate_of_zero(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db, item_cost=Decimal("179.00"), shipping_cost=Decimal("0.00"))

    response = client.patch(
        f"/api/inventory/{item.id}", json={"tax_rate": "0"}, headers=admin_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sales_tax"] == "0.00"
    assert body["total_cost"] == "179.00"


def test_shipping_can_be_left_untaxed_on_one_item(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(monkeypatch, "0.0635", shipping=True)
    item = make_item(db, item_cost=Decimal("100.00"), shipping_cost=Decimal("10.00"))

    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"tax_includes_shipping": False},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["sales_tax"] == "6.35"


def test_many_items_can_be_marked_untaxed_at_once(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """How a batch of orders known to be untaxed gets corrected in one go."""
    first, second = make_item(db), make_item(db)

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [first.id, second.id], "changes": {"tax_rate": "0"}},
        headers=admin_headers,
    )

    assert response.status_code == 200
    for item in (first, second):
        db.refresh(item)
        assert item.sales_tax == Decimal("0.00")


@pytest.mark.parametrize("field", ["tax_rate", "tax_includes_shipping"])
def test_nulling_a_tax_field_is_refused_naming_it(
    client: TestClient, admin_headers: dict[str, str], db: Session, field: str
) -> None:
    """Both columns are NOT NULL: a null must be a 422, not a database 500."""
    item = make_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}", json={field: None}, headers=admin_headers
    )

    assert response.status_code == 422
    assert field in response.json()["detail"]


def test_a_rate_above_one_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A rate is a fraction. 6.35 typed for 6.35% would multiply cost by 7.35."""
    item = make_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"tax_rate": "6.35"}, headers=admin_headers
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# What the edit form is told, and what a split passes on
# ---------------------------------------------------------------------------


def test_the_edit_form_is_told_the_configured_rate(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unticking "No sales tax charged" restores this rate, so it must be sent."""
    configure(monkeypatch, "0.0700", shipping=True)
    item = make_item(db, tax_rate=Decimal("0.0000"))

    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()

    assert body["tax_rate"] == "0.0000"
    assert body["tax_includes_shipping"] is True
    assert body["default_tax_rate"] == "0.0700"


def test_a_piece_keeps_the_lots_tax_rule(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A piece inherits the lot's rule rather than today's setting.

    The configured default is deliberately the opposite of the lot's rule, so
    a piece that took the default instead of inheriting would be caught.
    """
    configure(monkeypatch, "0.0635", shipping=True)
    lot = make_item(
        db,
        source_title="Two rounds",
        piece_count=2,
        item_cost=Decimal("100.00"),
        tax_includes_shipping=False,
    )

    response = client.post(
        f"/api/inventory/{lot.id}/split",
        json={
            "mode": "equal",
            "pieces": [{"source_title": "A"}, {"source_title": "B"}],
        },
        headers=admin_headers,
    )
    assert response.is_success, response.text

    db.expire_all()
    pieces = db.scalars(
        select(InventoryItem).where(InventoryItem.parent_item_id == lot.id)
    ).all()
    assert len(pieces) == 2
    assert all(piece.tax_includes_shipping is False for piece in pieces)
