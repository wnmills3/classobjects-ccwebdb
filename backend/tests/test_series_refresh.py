"""The design series kept up to date on every save (`series_classify.refresh_series`).

Through the API, as a person's save reaches it: a single edit, a bulk edit,
a note whose seal is evidence. See docs/specs/identify-first-entry-design.md.
"""

from __future__ import annotations

import pytest
from app import series_classify
from app.field_sources import SERIES_MATCH, record_derived
from app.models import CurrencyDetail, Denomination, InventoryItem, Series
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, code_id

DIME = "usd_coin_0_10"


def _dime(
    db: Session, make_item: ItemFactory, year: int, **extra: object
) -> InventoryItem:
    """A dime of this single year, its title naming no design."""
    return make_item(
        title="Plain dime",
        denomination_id=code_id(db, Denomination, DIME),
        year_start=year,
        year_end=year,
        **extra,
    )


def _series(
    client: TestClient, headers: dict[str, str], item: InventoryItem
) -> str | None:
    """The series the item's detail route shows."""
    body = client.get(f"/api/inventory/{item.id}", headers=headers).json()
    series: str | None = body["series"]
    return series


def _patch(
    client: TestClient, headers: dict[str, str], item: InventoryItem, **changes: object
) -> None:
    """Save these changes on the item, which must be accepted."""
    response = client.patch(f"/api/inventory/{item.id}", json=changes, headers=headers)
    assert response.status_code == 200, response.text


def test_an_edit_that_moves_the_year_moves_the_derived_series(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    dime = _dime(db, make_item, 1942)
    _patch(client, admin_headers, dime, variety="FB")
    assert _series(client, admin_headers, dime) == "winged_liberty_head_dime"

    _patch(client, admin_headers, dime, year_start=1950, year_end=1950)

    assert _series(client, admin_headers, dime) == "roosevelt_dime"


def test_a_seal_saved_on_a_note_is_evidence_for_its_design(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    note = make_item(
        kind="currency",
        title="Plain note",
        denomination_id=code_id(db, Denomination, "usd_note_5"),
        year_start=1934,
    )
    db.add(
        CurrencyDetail(inventory_item_id=note.id, series_year=1934, series_letter="A")
    )
    db.commit()
    _patch(client, admin_headers, note, serial_number="L12345678A")
    assert _series(client, admin_headers, note) is None  # an ordinary 1934A

    _patch(client, admin_headers, note, seal_color="brown")

    assert _series(client, admin_headers, note) == "hawaii"


def test_a_series_read_from_the_text_survives_a_year_change(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    # Only this pass's own guesses are taken back; a text match is reported by
    # the batch's disagreement check, not cleared by a save.
    wlh = code_id(db, Series, "winged_liberty_head_dime")
    dime = _dime(db, make_item, 1942, series_id=wlh)
    record_derived(db, dime.id, ["series_id"], SERIES_MATCH)
    db.commit()

    _patch(client, admin_headers, dime, year_start=1950, year_end=1950)

    assert _series(client, admin_headers, dime) == "winged_liberty_head_dime"


def test_a_bulk_edit_refreshes_every_item_it_touches(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    make_item: ItemFactory,
) -> None:
    dimes = [_dime(db, make_item, 1942), _dime(db, make_item, 1950)]
    untouched = _dime(db, make_item, 1942)

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [d.id for d in dimes], "changes": {"metal": "silver"}},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert [_series(client, admin_headers, d) for d in dimes] == [
        "winged_liberty_head_dime",
        "roosevelt_dime",
    ]
    assert _series(client, admin_headers, untouched) is None


def test_a_refresh_reads_the_designs_once(
    db: Session, make_item: ItemFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A bulk edit of thousands is one refresh; reading the vocabulary per step
    # would make it slow.
    dimes = [_dime(db, make_item, 1942) for _ in range(3)]
    real = series_classify.load_designs
    calls: list[int] = []

    def counted(session: Session) -> list[series_classify.Design]:
        """Read the designs as usual, counting the read."""
        calls.append(1)
        return real(session)

    monkeypatch.setattr(series_classify, "load_designs", counted)

    series_classify.refresh_series(db, [d.id for d in dimes])

    assert len(calls) == 1
