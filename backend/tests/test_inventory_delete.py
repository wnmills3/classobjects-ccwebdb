"""Deleting a row that should never have existed.

Guarded, because the two ways it goes wrong are both silent: deleting a lot
whose pieces then reference nothing, and deleting something a customer has
already bought.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import build_listing
from tests.test_schema import make_item
from tests.test_split import TUBE, do_split, lot


def test_a_deleted_item_leaves_search(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db, source_title="MISTAKE")

    assert (
        client.delete(f"/api/inventory/{item.id}", headers=admin_headers).status_code
        == 204
    )

    rows = client.get(
        "/api/inventory/coins/search?q=MISTAKE", headers=admin_headers
    ).json()["rows"]
    assert rows == []


def test_a_deleted_item_is_findable_when_asked_for(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Otherwise a mistaken delete is unrecoverable through the UI."""
    item = make_item(db, source_title="MISTAKE")
    client.delete(f"/api/inventory/{item.id}", headers=admin_headers)

    only = client.get(
        "/api/inventory/coins/search?q=MISTAKE&deleted=only", headers=admin_headers
    ).json()
    assert [r["id"] for r in only["rows"]] == [item.id]

    both = client.get(
        "/api/inventory/coins/search?q=MISTAKE&deleted=any", headers=admin_headers
    ).json()
    assert both["total"] == 1


def test_an_unrecognised_deleted_mode_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Consistent with every other filter: never silently ignored."""
    response = client.get(
        "/api/inventory/coins/search?deleted=maybe", headers=admin_headers
    )
    assert response.status_code == 422


def test_the_lot_filter_finds_a_lot_s_pieces(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """“Show me everything from that tube” is the review queue's entry point.

    By item code rather than id: the code is what is printed on the flip and
    what a person has in front of them.
    """
    parent = lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]
    make_item(db, source_title="unrelated")

    body = client.get(
        f"/api/inventory/coins/search?lot={parent.item_code}", headers=admin_headers
    ).json()

    assert sorted(r["id"] for r in body["rows"]) == sorted(p["id"] for p in pieces)


def test_an_unknown_lot_code_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Returning nothing would read as 'that lot has no pieces'.

    That is a different and much more alarming answer than 'no such lot'.
    """
    response = client.get(
        "/api/inventory/coins/search?lot=CC-999999", headers=admin_headers
    )
    assert response.status_code == 422
    assert "CC-999999" in response.json()["detail"]


def test_a_lot_with_pieces_cannot_be_deleted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Its pieces hold cost basis allocated from it and would be orphaned."""
    parent = lot(db)
    do_split(client, admin_headers, parent.id, TUBE)

    response = client.delete(f"/api/inventory/{parent.id}", headers=admin_headers)
    assert response.status_code == 409
    assert "piece" in response.json()["detail"].lower()


def test_a_listed_item_cannot_be_deleted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A listing is what an order line points at, through to the sale.

    Guarding on the listing rather than on the order is the wider net and the
    cheaper query: an item cannot reach an order without one.
    """
    listing = build_listing(db)
    response = client.delete(
        f"/api/inventory/{listing.inventory_item_id}", headers=admin_headers
    )
    assert response.status_code == 409
    assert "listing" in response.json()["detail"].lower()
