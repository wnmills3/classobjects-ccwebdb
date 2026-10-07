"""Spot prices: a quote recorded, the newest one shown, and what it values."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.models import InventoryItem, ItemKind, Metal, MetalPrice
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id

URL = "/api/metal-prices"


def _quote(db: Session, metal: str, price: str, *, days_ago: int) -> None:
    """A quote for `metal` made that many days ago."""
    db.add(
        MetalPrice(
            metal_id=code_id(db, Metal, metal),
            price_per_ozt=Decimal(price),
            quoted_at=datetime.now(UTC) - timedelta(days=days_ago),
        )
    )
    db.commit()


def _bullion(db: Session, metal: str, ounces: str, pieces: int = 1) -> InventoryItem:
    """A live bullion item of this metal, fine weight per piece and piece count."""
    return build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, "bullion"),
        metal_id=code_id(db, Metal, metal),
        fine_weight_ozt=Decimal(ounces),
        piece_count=pieces,
    )


def _by_metal(body: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """The table's rows, by metal code."""
    return {str(row["metal"]): row for row in body}


def test_a_metal_never_quoted_has_no_price_and_no_melt_value(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _bullion(db, "silver", "1")
    db.commit()

    body = client.get(URL, headers=admin_headers).json()

    silver = _by_metal(body)["silver"]
    assert silver["label"] == "Silver"
    # Not quoted is not "quoted at nothing".
    assert (silver["price_per_ozt"], silver["quoted_at"], silver["melt_value"]) == (
        None,
        None,
        None,
    )
    assert Decimal(str(silver["fine_ozt_held"])) == Decimal("1")


def test_the_newest_quote_is_the_price_whatever_order_they_were_entered_in(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    # The newest by date is entered first: the last row added is not it.
    _quote(db, "silver", "31.50", days_ago=1)
    _quote(db, "silver", "28.00", days_ago=30)
    _quote(db, "gold", "2650.00", days_ago=5)

    rows = _by_metal(client.get(URL, headers=admin_headers).json())

    assert Decimal(str(rows["silver"]["price_per_ozt"])) == Decimal("31.50")
    assert Decimal(str(rows["gold"]["price_per_ozt"])) == Decimal("2650.00")
    assert rows["silver"]["source"] == "manual"
    assert rows["platinum"]["price_per_ozt"] is None


def test_melt_value_is_the_fine_ounces_held_at_the_newest_quote(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _quote(db, "silver", "30.00", days_ago=1)
    # 1 oz, a tube of twenty 1 oz rounds, and a 0.7734 oz dollar: 21.7734 oz.
    _bullion(db, "silver", "1")
    _bullion(db, "silver", "1", pieces=20)
    _bullion(db, "silver", "0.7734")
    # Not counted: another metal, no weight, and one that was deleted.
    _bullion(db, "gold", "1")
    build_bare_item(db, metal_id=code_id(db, Metal, "silver"))
    gone = _bullion(db, "silver", "100")
    gone.deleted_at = datetime.now(UTC)
    db.commit()

    rows = _by_metal(client.get(URL, headers=admin_headers).json())

    assert Decimal(str(rows["silver"]["fine_ozt_held"])) == Decimal("21.7734")
    assert Decimal(str(rows["silver"]["melt_value"])) == Decimal("653.20")
    # Gold is held but not quoted: ounces, and no value put on them.
    assert Decimal(str(rows["gold"]["fine_ozt_held"])) == Decimal("1")
    assert rows["gold"]["melt_value"] is None


def test_recording_a_price_adds_a_quote_and_keeps_the_one_before(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _quote(db, "silver", "28.00", days_ago=30)

    res = client.post(
        URL, json={"metal": "silver", "price_per_ozt": "31.4567"}, headers=admin_headers
    )

    assert res.status_code == 201, res.text
    silver = _by_metal(res.json())["silver"]
    assert Decimal(str(silver["price_per_ozt"])) == Decimal("31.4567")
    assert silver["source"] == "manual"
    # A time series: the earlier quote is still there, behind the new one.
    prices = db.scalars(
        select(MetalPrice.price_per_ozt)
        .where(MetalPrice.metal_id == code_id(db, Metal, "silver"))
        .order_by(MetalPrice.quoted_at)
    ).all()
    assert prices == [Decimal("28.0000"), Decimal("31.4567")]


def test_the_answer_to_a_recording_is_the_whole_table(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _quote(db, "gold", "2650.00", days_ago=5)

    res = client.post(
        URL, json={"metal": "silver", "price_per_ozt": "31.50"}, headers=admin_headers
    )

    rows = _by_metal(res.json())
    assert Decimal(str(rows["gold"]["price_per_ozt"])) == Decimal("2650.00")
    assert Decimal(str(rows["silver"]["price_per_ozt"])) == Decimal("31.50")
    # In the vocabulary's own order, every metal in use.
    codes = [row["metal"] for row in res.json()]
    assert codes.index("silver") < codes.index("gold") < codes.index("platinum")


def test_a_price_that_is_not_one_is_refused_and_nothing_is_recorded(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    for bad in ("-1", "abc", "1.23456", "12345678901234"):
        res = client.post(
            URL, json={"metal": "silver", "price_per_ozt": bad}, headers=admin_headers
        )
        assert res.status_code == 422, (bad, res.text)
    unknown = client.post(
        URL, json={"metal": "unobtainium", "price_per_ozt": "5"}, headers=admin_headers
    )
    assert unknown.status_code == 422
    assert "unobtainium" in unknown.text
    extra = client.post(
        URL,
        json={"metal": "silver", "price_per_ozt": "5", "source": "kitco"},
        headers=admin_headers,
    )
    assert extra.status_code == 422

    assert db.scalar(select(func.count()).select_from(MetalPrice)) == 0


def test_a_price_of_zero_is_a_price(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    _bullion(db, "copper", "16")
    db.commit()

    res = client.post(
        URL, json={"metal": "copper", "price_per_ozt": "0"}, headers=admin_headers
    )

    assert res.status_code == 201
    copper = _by_metal(res.json())["copper"]
    # Quoted at nothing is a fact, and values the holding at nothing.
    assert Decimal(str(copper["price_per_ozt"])) == Decimal("0")
    assert Decimal(str(copper["melt_value"])) == Decimal("0")


def test_spot_prices_are_for_managers_only(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers=customer_headers).status_code == 403
    refused = client.post(
        URL, json={"metal": "silver", "price_per_ozt": "5"}, headers=customer_headers
    )
    assert refused.status_code == 403
