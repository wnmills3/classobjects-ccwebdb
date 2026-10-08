"""An item's weight: entered per piece, and fine weight worked out from it.

Gross weight, fineness, fine weight and the weight as written are a person's
to enter (`routers.inventory`); a fine weight left empty is the gross weight
times the fineness (`classifier_defaults.weight_outcome`).
"""

from __future__ import annotations

from decimal import Decimal

from app.field_sources import WEIGHT_TEXT, derived_fields, record_derived
from app.models import InventoryItem, PurchaseOrder, Vendor
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory


def _round(db: Session, make_item: ItemFactory, **fields: object) -> InventoryItem:
    """A bullion round: no denomination, so no composition decides its weight."""
    item = make_item(kind="bullion", title="Silver Round", year_start=None, **fields)
    db.commit()
    return item


def _patch(
    client: TestClient, headers: dict[str, str], item: InventoryItem, **body: object
) -> dict[str, object]:
    """Save `body` on `item`, and return the item as the editor then reads it."""
    response = client.patch(f"/api/inventory/{item.id}", json=body, headers=headers)
    assert response.status_code == 200, response.text
    detail: dict[str, object] = client.get(
        f"/api/inventory/{item.id}", headers=headers
    ).json()
    return detail


def test_gross_weight_and_fineness_give_the_fine_weight(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    item = _round(db, make_item)

    body = _patch(
        client, admin_headers, item, gross_weight_ozt="1.25", fineness="0.925"
    )

    assert body["fine_weight_ozt"] == "1.156250"
    assert derived_fields(db, item.id) == {"fine_weight_ozt": "weight"}


def test_a_worked_out_fine_weight_follows_its_inputs(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    item = _round(db, make_item)
    _patch(client, admin_headers, item, gross_weight_ozt="1", fineness="0.999")

    body = _patch(client, admin_headers, item, gross_weight_ozt="5")

    assert body["fine_weight_ozt"] == "4.995000"

    # With an input gone there is nothing to work it out from.
    body = _patch(client, admin_headers, item, fineness=None)
    assert body["fine_weight_ozt"] is None
    assert "fine_weight_ozt" not in derived_fields(db, item.id)


def test_a_typed_fine_weight_is_not_recomputed(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    item = _round(db, make_item)

    # A one-ounce coin that weighs more than an ounce: both are facts.
    body = _patch(
        client,
        admin_headers,
        item,
        gross_weight_ozt="1.0909",
        fineness="0.9167",
        fine_weight_ozt="1",
    )

    assert body["fine_weight_ozt"] == "1.000000"
    assert derived_fields(db, item.id) == {}
    body = _patch(client, admin_headers, item, gross_weight_ozt="1.1")
    assert body["fine_weight_ozt"] == "1.000000"


def test_an_emptied_fine_weight_stays_empty(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    item = _round(db, make_item)
    _patch(client, admin_headers, item, gross_weight_ozt="1", fineness="0.999")

    body = _patch(client, admin_headers, item, fine_weight_ozt=None)

    assert body["fine_weight_ozt"] is None
    body = _patch(client, admin_headers, item, gross_weight_ozt="2")
    assert body["fine_weight_ozt"] is None


def test_a_guessed_weight_survives_having_no_composition(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    """A round has no composition to lose, so a guess is not taken back."""
    item = _round(db, make_item, gross_weight_ozt=Decimal("1"))
    record_derived(db, item.id, ["gross_weight_ozt"], WEIGHT_TEXT)
    db.commit()

    body = _patch(client, admin_headers, item, description="Seen again.")

    assert body["gross_weight_ozt"] == "1.000000"
    assert derived_fields(db, item.id) == {"gross_weight_ozt": WEIGHT_TEXT}


def test_the_weight_as_written_is_kept_and_returned(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    item = _round(db, make_item)

    body = _patch(client, admin_headers, item, weight_note="1 oz each")

    assert body["weight_note"] == "1 oz each"
    assert client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()[
        "weight_note"
    ] == ("1 oz each")


def test_a_fine_weight_above_the_gross_weight_is_refused_naming_both(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    """A piece cannot hold more metal than it weighs: a 422, never a 500.

    Judged on the item as the edit leaves it, so a fine weight sent alone is
    held against the gross weight already stored, and the reverse.
    """
    item = _round(db, make_item)
    path = f"/api/inventory/{item.id}"

    both = client.patch(
        path,
        json={"gross_weight_ozt": "1", "fine_weight_ozt": "2"},
        headers=admin_headers,
    )
    assert both.status_code == 422, both.text
    assert "fine_weight_ozt" in both.json()["detail"]
    assert "gross_weight_ozt" in both.json()["detail"]

    _patch(client, admin_headers, item, gross_weight_ozt="1", fine_weight_ozt="1")
    fine_alone = client.patch(
        path, json={"fine_weight_ozt": "1.5"}, headers=admin_headers
    )
    assert fine_alone.status_code == 422, fine_alone.text
    gross_alone = client.patch(
        path, json={"gross_weight_ozt": "0.5"}, headers=admin_headers
    )
    assert gross_alone.status_code == 422, gross_alone.text

    db.expire_all()
    stored = db.get_one(InventoryItem, item.id)
    assert (stored.gross_weight_ozt, stored.fine_weight_ozt) == (
        Decimal("1.000000"),
        Decimal("1.000000"),
    )


def test_a_worked_out_fine_weight_follows_a_gross_weight_made_smaller(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    """The fine weight is the rule's, so it comes down with the gross weight.

    Between the new gross weight being written and the fine weight being
    worked out again, the stored fine weight is the larger of the two; that
    moment must not be what the database is asked to accept.
    """
    item = _round(db, make_item)
    _patch(client, admin_headers, item, gross_weight_ozt="1", fineness="0.999")

    body = _patch(client, admin_headers, item, gross_weight_ozt="0.5")

    assert body["fine_weight_ozt"] == "0.499500"
    assert derived_fields(db, item.id) == {"fine_weight_ozt": "weight"}


def test_a_bulk_edit_refuses_a_fine_weight_above_one_item_s_gross_weight(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    """All or nothing: the item it would fit is not changed either."""
    heavy = _round(db, make_item, gross_weight_ozt=Decimal("5"))
    light = _round(db, make_item, gross_weight_ozt=Decimal("1"))

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [heavy.id, light.id], "changes": {"fine_weight_ozt": "2"}},
        headers=admin_headers,
    )

    assert response.status_code == 422, response.text
    assert light.item_code in response.json()["detail"]
    db.expire_all()
    assert db.get_one(InventoryItem, heavy.id).fine_weight_ozt is None


def test_a_fineness_of_zero_is_refused(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    """Fineness is a fraction above zero; nothing is no fineness, not zero."""
    item = _round(db, make_item)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"fineness": "0"}, headers=admin_headers
    )

    assert response.status_code == 422, response.text


def test_a_new_item_with_more_fine_weight_than_gross_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = Vendor(name="Weight Refusal Vendor")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id)
    db.add(order)
    db.commit()

    response = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "bullion",
            "source_title": "Sterling round",
            "gross_weight_ozt": "0.8",
            "fine_weight_ozt": "0.9",
        },
        headers=admin_headers,
    )

    assert response.status_code == 422, response.text
    assert (
        db.scalars(
            select(InventoryItem.id).where(InventoryItem.purchase_order_id == order.id)
        ).all()
        == []
    )


def test_a_new_item_takes_its_weight(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = Vendor(name="Weight Test Vendor")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id)
    db.add(order)
    db.commit()

    response = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "bullion",
            "source_title": "Sterling round",
            "bullion_form": "round",
            "gross_weight_ozt": "0.8",
            "fineness": "0.925",
            "weight_note": "24.9 g",
        },
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["gross_weight_ozt"] == "0.800000"
    assert body["fineness"] == "0.9250"
    assert body["fine_weight_ozt"] == "0.740000"
    assert body["weight_note"] == "24.9 g"
