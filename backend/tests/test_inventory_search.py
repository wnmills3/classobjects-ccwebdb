"""Browsing the coin and currency inventories."""

from __future__ import annotations

from decimal import Decimal

from app.models import CurrencyDetail, Grade, ItemKind, SealColor
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item


def coin(db: Session, **overrides: object) -> None:
    return make_item(db, item_kind_id=code_id(db, ItemKind, "coin"), **overrides)


def note(
    db: Session,
    *,
    seal: str | None = None,
    serial: str | None = None,
    **overrides: object,
) -> None:
    item = make_item(db, item_kind_id=code_id(db, ItemKind, "currency"), **overrides)
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id,
            seal_color_id=code_id(db, SealColor, seal) if seal else None,
            serial_number=serial,
            series_year=overrides.get("year_start"),
        )
    )
    db.commit()
    return item


def search(
    client: TestClient, view: str, headers: dict[str, str], **params: object
) -> None:
    return client.get(f"/api/inventory/{view}/search", params=params, headers=headers)


# ---------------------------------------------------------------------------
# The two views are genuinely separate inventories
# ---------------------------------------------------------------------------


def test_each_view_holds_only_its_own_kind(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    a_coin = coin(db, source_title="A Morgan Dollar")
    a_note = note(db, source_title="A Silver Certificate")

    coins = search(client, "coins", admin_headers).json()
    currency = search(client, "currency", admin_headers).json()

    assert a_coin.id in {r["id"] for r in coins["rows"]}
    assert a_coin.id not in {r["id"] for r in currency["rows"]}
    assert a_note.id in {r["id"] for r in currency["rows"]}
    assert a_note.id not in {r["id"] for r in coins["rows"]}


def test_each_view_returns_the_columns_that_matter_to_it(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Each view carries the columns its own kind needs.

    The reason for two views: a coin has a mint mark, a note has a seal
    colour and its own serial. One grid would leave most columns empty.
    """
    coin(db)
    note(db, seal="blue", serial="A12345678B")

    coin_row = search(client, "coins", admin_headers).json()["rows"][0]
    note_row = search(client, "currency", admin_headers).json()["rows"][0]

    assert "mint_mark" in coin_row and "metal" in coin_row
    assert "seal_color" not in coin_row

    assert "seal_color" in note_row and "serial_number" in note_row
    assert "mint_mark" not in note_row


# ---------------------------------------------------------------------------
# Searching and filtering
# ---------------------------------------------------------------------------


def test_free_text_searches_title_and_item_code(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    wanted = coin(db, source_title="1881-S Morgan Dollar")
    coin(db, source_title="Walking Liberty Half")

    by_title = search(client, "coins", admin_headers, q="morgan").json()
    assert {r["id"] for r in by_title["rows"]} == {wanted.id}

    by_code = search(client, "coins", admin_headers, q=wanted.item_code).json()
    assert {r["id"] for r in by_code["rows"]} == {wanted.id}


def test_a_note_can_be_found_by_its_printed_serial(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    wanted = note(db, source_title="Blue seal", serial="A12345678B")
    note(db, source_title="Another", serial="C98765432D")

    found = search(client, "currency", admin_headers, serial_number="12345678").json()
    assert {r["id"] for r in found["rows"]} == {wanted.id}


def test_filters_combine(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    wanted = coin(db, year_start=1881, item_cost=Decimal("50.00"))
    coin(db, year_start=1921)

    body = search(client, "coins", admin_headers, year_min=1880, year_max=1890).json()
    assert {r["id"] for r in body["rows"]} == {wanted.id}


def test_an_unknown_filter_is_refused_rather_than_ignored(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """An unknown filter fails loudly rather than widening the search.

    A silently dropped filter returns the whole collection and looks like a
    matching result -- the worst possible failure for a search.
    """
    coin(db)
    response = search(client, "coins", admin_headers, mint_mark_typo="D")

    assert response.status_code == 422
    assert "mint_mark_typo" in response.json()["detail"]


def test_a_filter_from_the_other_view_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A filter belonging to the other view is refused.

    seal_color means nothing to coins, and pretending otherwise would
    return every coin.
    """
    coin(db)
    assert search(client, "coins", admin_headers, seal_color="blue").status_code == 422
    assert search(client, "currency", admin_headers, metal="silver").status_code == 422


def test_an_unknown_view_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.get("/api/inventory/medals/search", headers=admin_headers)
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Sorting, paging, money
# ---------------------------------------------------------------------------


def test_sorting_both_ways(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin(db, source_title="old", year_start=1878)
    coin(db, source_title="new", year_start=2020)

    up = search(client, "coins", admin_headers, sort="year_start").json()
    down = search(client, "coins", admin_headers, sort="year_start", desc=True).json()

    assert up["rows"][0]["year_start"] == 1878
    assert down["rows"][0]["year_start"] == 2020


def test_an_unsortable_column_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin(db)
    response = search(client, "coins", admin_headers, sort="attributes")
    assert response.status_code == 422
    assert "Sortable" in response.json()["detail"]


def test_paging_reports_the_total_not_the_page(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    for n in range(5):
        coin(db, source_title=f"item {n}")

    body = search(client, "coins", admin_headers, limit=2).json()
    assert body["total"] >= 5
    assert len(body["rows"]) == 2

    second = search(client, "coins", admin_headers, limit=2, offset=2).json()
    assert {r["id"] for r in body["rows"]}.isdisjoint({r["id"] for r in second["rows"]})


def test_money_never_arrives_as_a_float(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Money crosses the API as a string, never a float.

    FastAPI's encoder turns a Decimal in a plain dict into a float, which is
    the one thing this schema is careful never to do.
    """
    coin(db, item_cost=Decimal("0.10"), shipping_cost=Decimal("0.20"))
    row = search(client, "coins", admin_headers).json()["rows"][0]

    assert isinstance(row["item_cost"], str)
    assert isinstance(row["total_cost"], str)
    assert row["item_cost"] == "0.10"


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------


def test_facets_count_what_is_actually_present(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Facets offer the values present, not the whole vocabulary.

    A search panel over thousands of items needs to offer the values that
    exist, not the fifty-odd grades the vocabulary defines.
    """
    coin(db, grade_id=code_id(db, Grade, "MS64"))
    coin(db, grade_id=code_id(db, Grade, "MS64"))
    coin(db, grade_id=code_id(db, Grade, "MS65"))

    body = search(client, "coins", admin_headers, facets=True).json()
    grades = {f["value"]: f["count"] for f in body["facets"]["grade"]}

    assert grades["MS64"] == 2
    assert grades["MS65"] == 1


def test_facets_reflect_the_current_filters(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Facet counts respect the filters already applied.

    The counts describe what narrowing further would do, so a filter that
    would return nothing is visibly empty before it is chosen.
    """
    coin(db, year_start=1881)
    coin(db, year_start=2020)

    filtered = search(client, "coins", admin_headers, facets=True, year_min=2000).json()
    total_in_facet = sum(f["count"] for f in filtered["facets"]["item_kind"])

    assert total_in_facet == filtered["total"]


def test_facets_are_opt_in(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin(db)
    assert search(client, "coins", admin_headers).json()["facets"] == {}


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def test_browsing_inventory_requires_an_administrator(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    """Browsing inventory is staff-only.

    These rows carry cost basis, storage quantity and local catalogue
    numbers -- none of which is customer-facing.
    """
    response = client.get("/api/inventory/coins/search", headers=customer_headers)
    assert response.status_code == 403

    assert client.get("/api/inventory/coins/search").status_code == 401


def test_a_split_lot_does_not_appear(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A lot that has been broken up no longer appears.

    The view excludes split lots, so browsing cannot show a lot beside the
    pieces it became.
    """
    parent = coin(
        db, source_title="Tube of four", piece_count=4, item_cost=Decimal("100.00")
    )
    client.post(
        f"/api/inventory/{parent.id}/split",
        json={"mode": "equal", "pieces": [{"source_title": f"P{n}"} for n in range(4)]},
        headers=admin_headers,
    )

    body = search(client, "coins", admin_headers).json()
    assert parent.id not in {r["id"] for r in body["rows"]}
