"""`POST /api/inventory`: entering a coin, banknote or lot on a purchase.

Before this endpoint the only creation paths were the importer, the demo
seed and `POST /api/catalog` (which always creates a shop listing). This is
the console's own path, mirroring `SchemaLoader.load()` -- see
`docs/specs/entry-panels-design.md`.
"""

from __future__ import annotations

from decimal import Decimal

from app.config import settings
from app.models import (
    CoinDetail,
    CurrencyDetail,
    InventoryItem,
    ItemCertification,
    ItemStatusHistory,
    Mint,
    PurchaseOrder,
    Vendor,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _purchase_order(db: Session, name: str = "Item Test Vendor") -> PurchaseOrder:
    vendor = Vendor(name=name)
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id)
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _coin_payload(order_id: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "purchase_order_id": order_id,
        "item_kind": "coin",
        "source_title": "1881-S Morgan Silver Dollar",
        "year_start": 1881,
        "item_cost": "50.00",
        "shipping_cost": "5.00",
        "country": "US",
        "grade": "MS64",
        "mint": "S",
    }
    payload.update(overrides)
    return payload


def _currency_payload(order_id: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "purchase_order_id": order_id,
        "item_kind": "currency",
        "source_title": "1957 $1 Silver Certificate",
        "item_cost": "20.00",
        "note_type": "silver_certificate",
        "series_year": 1957,
        "series_letter": "B",
        "seal_color": "blue",
        "fed_district": "B",
        "serial_number": "A12345678B",
    }
    payload.update(overrides)
    return payload


def test_a_coin_gets_exactly_one_coin_detail_row_with_its_mint(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory", json=_coin_payload(order.id), headers=admin_headers
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["item_code"].startswith("CC-")
    assert body["item_kind"] == "coin"
    assert body["grade"] == "MS64"

    item_id = body["id"]
    detail = db.scalars(
        select(CoinDetail).where(CoinDetail.inventory_item_id == item_id)
    ).one()
    mint_code = db.scalar(select(Mint.code).where(Mint.id == detail.mint_id))
    assert mint_code == "S"
    assert (
        db.scalar(
            select(CurrencyDetail).where(CurrencyDetail.inventory_item_id == item_id)
        )
        is None
    )


def test_a_banknote_gets_exactly_one_currency_detail_row(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory", json=_currency_payload(order.id), headers=admin_headers
    )
    assert res.status_code == 201, res.text
    item_id = res.json()["id"]

    detail = db.scalars(
        select(CurrencyDetail).where(CurrencyDetail.inventory_item_id == item_id)
    ).one()
    assert detail.serial_number == "A12345678B"
    assert detail.series_year == 1957
    assert detail.series_letter == "B"
    assert detail.series_designation == "1957B"
    assert detail.seal_color_id is not None
    assert detail.fed_district_id is not None
    assert detail.note_type_id is not None
    assert (
        db.scalar(select(CoinDetail).where(CoinDetail.inventory_item_id == item_id))
        is None
    )


def test_a_lot_records_its_piece_count(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, piece_count=5),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["piece_count"] == 5


def test_status_defaults_to_ordered_with_one_opening_history_row(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory", json=_coin_payload(order.id), headers=admin_headers
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "ordered"

    rows = db.scalars(
        select(ItemStatusHistory).where(
            ItemStatusHistory.inventory_item_id == body["id"]
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].from_status_id is None


def test_status_received_is_accepted_and_recorded(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, status="received"),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "received"

    rows = db.scalars(
        select(ItemStatusHistory).where(
            ItemStatusHistory.inventory_item_id == body["id"]
        )
    ).all()
    assert len(rows) == 1


def test_an_unknown_status_is_a_422(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, status="canceled"),
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_tax_defaults_to_the_configured_rate_when_omitted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory", json=_coin_payload(order.id), headers=admin_headers
    )
    assert res.status_code == 201, res.text
    assert Decimal(res.json()["tax_rate"]) == Decimal(str(settings.sales_tax_rate))


def test_tax_rate_can_be_set_to_explicit_zero(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, tax_rate="0.0000"),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    assert Decimal(res.json()["tax_rate"]) == Decimal("0.0000")


def test_tax_includes_shipping_false_is_stored_and_excludes_shipping(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(
            order.id,
            item_cost="50.00",
            shipping_cost="10.00",
            tax_rate="0.1000",
            tax_includes_shipping=False,
        ),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    item = db.get(InventoryItem, res.json()["id"])
    assert item is not None
    assert item.tax_includes_shipping is False
    assert item.sales_tax == Decimal("5.00")


def test_tax_includes_shipping_true_is_stored_and_includes_shipping(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(
            order.id,
            item_cost="50.00",
            shipping_cost="10.00",
            tax_rate="0.1000",
            tax_includes_shipping=True,
        ),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    item = db.get(InventoryItem, res.json()["id"])
    assert item is not None
    assert item.tax_includes_shipping is True
    assert item.sales_tax == Decimal("6.00")


def test_a_bullion_item_gets_a_coin_detail(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, item_kind="bullion"),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    item_id = res.json()["id"]
    assert (
        db.scalar(select(CoinDetail).where(CoinDetail.inventory_item_id == item_id))
        is not None
    )
    assert (
        db.scalar(
            select(CurrencyDetail).where(CurrencyDetail.inventory_item_id == item_id)
        )
        is None
    )


def test_a_certificate_number_creates_one_certification_row(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, grading_service="PCGS", cert_number="12345678"),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    item_id = res.json()["id"]

    cert = db.scalars(
        select(ItemCertification).where(ItemCertification.inventory_item_id == item_id)
    ).one()
    assert cert.cert_number == "12345678"
    assert cert.grading_service_id is not None


def test_a_single_year_is_normalised_to_start_equals_end(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, year_start=1921),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["year_start"] == 1921
    assert body["year_end"] == 1921


def test_year_end_before_year_start_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, year_start=1921, year_end=1900),
        headers=admin_headers,
    )
    assert res.status_code == 422


def test_an_unknown_classifier_code_is_a_422_naming_the_field(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, grade="NOT_A_REAL_GRADE"),
        headers=admin_headers,
    )
    assert res.status_code == 422
    assert "grade" in res.text


def test_currency_fields_on_a_coin_are_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(order.id, serial_number="A12345678B"),
        headers=admin_headers,
    )
    assert res.status_code == 422
    # The field name must appear in the error message itself -- not merely
    # somewhere in the response body, which a 422 also echoes back as the
    # rejected input regardless of which field was actually named.
    detail = res.json()["detail"]
    assert any("serial_number" in str(err.get("msg", "")) for err in detail)


def test_coin_fields_on_a_currency_item_are_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_currency_payload(order.id, mint="P"),
        headers=admin_headers,
    )
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert any("mint" in str(err.get("msg", "")) for err in detail)


def test_an_unknown_purchase_order_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/inventory",
        json=_coin_payload(10_000_000),
        headers=admin_headers,
    )
    assert res.status_code == 404


def test_an_unknown_field_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    payload = _coin_payload(order.id)
    payload["not_a_field"] = "x"
    res = client.post("/api/inventory", json=payload, headers=admin_headers)
    assert res.status_code == 422


def test_a_customer_cannot_create_an_item(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory", json=_coin_payload(order.id), headers=customer_headers
    )
    assert res.status_code == 403
    count = db.scalar(
        select(func.count())
        .select_from(InventoryItem)
        .where(InventoryItem.purchase_order_id == order.id)
    )
    assert count == 0


def test_a_signed_out_caller_cannot_create_an_item(
    client: TestClient, db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post("/api/inventory", json=_coin_payload(order.id))
    assert res.status_code == 401
    count = db.scalar(
        select(func.count())
        .select_from(InventoryItem)
        .where(InventoryItem.purchase_order_id == order.id)
    )
    assert count == 0
