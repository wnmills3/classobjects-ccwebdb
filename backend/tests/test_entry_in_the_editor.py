"""Entering an item on a purchase through the item editor.

The purchase page makes the row from a kind and a title, opens the editor on
it, and removes it again if the editor is closed without a save. See
docs/specs/entry-panels-design.md.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import build_purchase_order


@pytest.mark.parametrize("kind", ["coin", "currency", "bullion"])
def test_a_kind_and_a_title_are_enough_to_make_an_item(
    client: TestClient, admin_headers: dict[str, str], db: Session, kind: str
) -> None:
    order = build_purchase_order(db, vendor_name="Entry Vendor", order_number="E-1")

    made = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": kind,
            "source_title": "What the seller called it",
            "tax_rate": None,
            "tax_includes_shipping": None,
        },
        headers=admin_headers,
    )

    assert made.status_code == 201, made.text
    body = made.json()
    assert body["item_kind"] == kind
    assert body["status"] == "ordered"
    # The editor opens on it straight away, and can describe what it shows.
    path = f"/api/inventory/{body['id']}"
    assert client.get(path, headers=admin_headers).status_code == 200
    described = client.post(
        f"{path}/suggested-description", json={"changes": {}}, headers=admin_headers
    )
    assert described.status_code == 200, described.text


def test_an_item_nobody_saved_is_removed_and_leaves_the_purchase(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = build_purchase_order(db, vendor_name="Entry Vendor", order_number="E-2")
    made = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "currency",
            "source_title": "A note",
        },
        headers=admin_headers,
    ).json()
    lines = client.get(
        f"/api/purchase-orders/{order.id}", headers=admin_headers
    ).json()["lines"]
    assert [line["id"] for line in lines] == [made["id"]]

    gone = client.delete(f"/api/inventory/{made['id']}", headers=admin_headers)

    assert gone.status_code == 204
    after = client.get(f"/api/purchase-orders/{order.id}", headers=admin_headers)
    assert after.json()["lines"] == []
