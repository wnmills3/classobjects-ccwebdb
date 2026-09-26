"""An item's years: one year, or a range of them.

A single year is stored as year_start == year_end -- nearly every item's
shape. A range is for a multi-year set or a coin whose date is only known to
an era; a handful of items have one.

Setting year_start on its own, on a single year, would either break it apart
or, when the new start was later than the old year, hit
ck_inventory_item_year_range as an unhandled IntegrityError: bulk-editing Year
on such an item to a later year would be a 500.
"""

from __future__ import annotations

from app.models import InventoryItem
from app.seed import SAMPLE_CATALOG, _build
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item


def _years(db: Session, item: InventoryItem) -> tuple[int | None, int | None]:
    db.expire_all()
    fresh = db.get(InventoryItem, item.id)
    assert fresh is not None
    return fresh.year_start, fresh.year_end


def _patch(
    client: TestClient, headers: dict[str, str], item: InventoryItem, **body: object
) -> Response:
    return client.patch(f"/api/inventory/{item.id}", json=body, headers=headers)


def test_a_single_year_stays_single_when_its_year_changes(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db, year_start=1878, year_end=1878)

    response = _patch(client, admin_headers, item, year_start=1964)

    assert response.status_code == 200
    assert _years(db, item) == (1964, 1964)


def test_a_start_year_with_no_end_becomes_a_single_year(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The second shape a single year was stored in, left by the demo seed."""
    item = build_bare_item(db, year_start=1881, year_end=None)

    assert _patch(client, admin_headers, item, year_start=1882).status_code == 200
    assert _years(db, item) == (1882, 1882)


def test_a_range_keeps_its_end_when_only_the_start_changes(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db, year_start=1999, year_end=2008)

    assert _patch(client, admin_headers, item, year_start=2000).status_code == 200
    assert _years(db, item) == (2000, 2008)


def test_both_years_sent_are_taken_as_given(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db, year_start=1999, year_end=2008)

    response = _patch(client, admin_headers, item, year_start=1990, year_end=1990)

    assert response.status_code == 200
    assert _years(db, item) == (1990, 1990)


def test_clearing_a_single_year_clears_both_ends(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db, year_start=1878, year_end=1878)

    assert _patch(client, admin_headers, item, year_start=None).status_code == 200
    assert _years(db, item) == (None, None)


def test_a_range_that_would_end_before_it_starts_is_a_422(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = build_bare_item(db, year_start=1999, year_end=2008)

    response = _patch(client, admin_headers, item, year_start=2010)

    assert response.status_code == 422
    assert "2008" in response.json()["detail"]
    assert _years(db, item) == (1999, 2008)


def test_a_bulk_year_moves_single_years_and_keeps_range_ends(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    single = build_bare_item(db, year_start=1878, year_end=1878)
    start_only = build_bare_item(db, year_start=1881, year_end=None)
    ranged = build_bare_item(db, year_start=1950, year_end=2008)

    response = client.post(
        "/api/inventory/bulk",
        json={
            "ids": [single.id, start_only.id, ranged.id],
            "changes": {"year_start": 1964},
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert _years(db, single) == (1964, 1964)
    assert _years(db, start_only) == (1964, 1964)
    assert _years(db, ranged) == (1964, 2008)


def test_a_bulk_year_that_breaks_one_range_changes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    single = build_bare_item(db, year_start=1878, year_end=1878)
    ranged = build_bare_item(db, year_start=1999, year_end=2008)

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [single.id, ranged.id], "changes": {"year_start": 2010}},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert ranged.item_code in response.json()["detail"]
    assert _years(db, single) == (1878, 1878)
    assert _years(db, ranged) == (1999, 2008)


def test_every_demo_item_is_seeded_as_a_single_year(db: Session) -> None:
    for row in SAMPLE_CATALOG:
        _build(db, row)
    db.flush()

    titles = [row["title"] for row in SAMPLE_CATALOG]
    seeded = db.scalars(
        select(InventoryItem).where(InventoryItem.source_title.in_(titles))
    ).all()
    assert len(seeded) >= len(SAMPLE_CATALOG)
    for item in seeded:
        assert item.year_start is not None
        assert item.year_end == item.year_start, item.source_title
