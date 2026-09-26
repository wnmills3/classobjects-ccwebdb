"""A coin's mint and variety are editable (`PATCH /inventory/{id}`).

They were set only when an item was created, so a mint mark found later --
"1885-O" in a seller's title -- could not be recorded (owner, 2026-09-24).
They live on the coin's own detail row; a banknote refuses them.
"""

from __future__ import annotations

from typing import Any

from app.models import CoinDetail, ItemFieldChange, ItemKind, Mint
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id


def _patch(
    client: TestClient, headers: dict[str, str], item_id: int, body: dict[str, Any]
) -> Response:
    return client.patch(f"/api/inventory/{item_id}", json=body, headers=headers)


def test_a_coin_with_no_detail_row_gets_its_mint_and_variety(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = build_bare_item(db)
    assert db.get(CoinDetail, coin.id) is None

    response = _patch(
        client, admin_headers, coin.id, {"mint": "O", "variety": " VAM-3 ", "base": {}}
    )
    assert response.status_code == 422  # base must hold every field sent
    response = _patch(
        client,
        admin_headers,
        coin.id,
        {"mint": "O", "variety": " VAM-3 ", "base": {"mint": None, "variety": None}},
    )
    assert response.status_code == 200, response.text

    db.expire_all()
    detail = db.get(CoinDetail, coin.id)
    assert detail is not None
    assert detail.mint_id == code_id(db, Mint, "O")
    assert detail.variety == "VAM-3"
    shown = client.get(f"/api/inventory/{coin.id}", headers=admin_headers).json()
    assert (shown["mint"], shown["variety"]) == ("O", "VAM-3")


def test_a_mint_change_is_logged_and_shown_by_label(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = build_bare_item(db)
    _patch(client, admin_headers, coin.id, {"mint": "S"})
    _patch(client, admin_headers, coin.id, {"mint": "D"})
    logged = db.scalars(
        select(ItemFieldChange)
        .where(
            ItemFieldChange.inventory_item_id == coin.id,
            ItemFieldChange.field_name == "mint",
        )
        .order_by(ItemFieldChange.id)
    ).all()
    assert [(c.old_value, c.new_value) for c in logged] == [(None, "S"), ("S", "D")]
    history = client.get(
        f"/api/inventory/{coin.id}/history", headers=admin_headers
    ).json()
    labels = dict(db.execute(select(Mint.code, Mint.label)).tuples().all())
    mint_events = [e for e in history if e["field"] == "mint"]
    assert mint_events[0]["new_value"] == labels["D"]


def test_a_banknote_refuses_a_mint(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = build_bare_item(db, item_kind_id=code_id(db, ItemKind, "currency"))
    response = _patch(client, admin_headers, note.id, {"mint": "S"})
    assert response.status_code == 422
    assert "belongs to coins" in response.json()["detail"]
    assert db.get(CoinDetail, note.id) is None


def test_an_unknown_mint_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = build_bare_item(db)
    response = _patch(client, admin_headers, coin.id, {"mint": "ZZ"})
    assert response.status_code == 422


def test_clearing_the_mint_keeps_the_variety(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = build_bare_item(db)
    _patch(client, admin_headers, coin.id, {"mint": "CC", "variety": "Micro O"})
    _patch(client, admin_headers, coin.id, {"mint": None})
    db.expire_all()
    detail = db.get(CoinDetail, coin.id)
    assert detail is not None
    assert (detail.mint_id, detail.variety) == (None, "Micro O")
