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
