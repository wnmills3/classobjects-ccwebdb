"""The public title an offer starts from (`app.offer_titles`).

The offer dialog used to pre-fill a listing's title with `source_title`, the
seller's wording from the purchase -- often "1" or "$1 Bill", sometimes a
Whatnot line in capitals -- which went onto eBay unless the operator
rewrote it. These tests pin the composed title for the shapes the collection
actually has, and the fallback for an item with nothing to compose from.
"""

from __future__ import annotations

from app.models import (
    CoinDetail,
    CurrencyDetail,
    Denomination,
    GradingService,
    Mint,
    NoteType,
    ReferenceMixin,
    Series,
    SetForm,
)
from app.offer_titles import suggested_title
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item


def _id(db: Session, model: type[ReferenceMixin], code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def test_a_graded_coin_is_titled_as_a_collector_writes_it(db: Session) -> None:
    """Year and mint mark, series, service and grade -- not the seller's text."""
    item = build_item(
        db,
        title="1",
        year_start=1921,
        series_id=_id(db, Series, "morgan_dollar"),
        denomination_id=_id(db, Denomination, "usd_coin_1_00"),
        grading_service_id=_id(db, GradingService, "PCGS"),
    )
    db.add(CoinDetail(inventory_item_id=item.id, mint_id=_id(db, Mint, "S")))
    db.flush()

    assert suggested_title(db, item) == "1921-S Morgan Dollar PCGS MS64"


def test_a_span_of_years_carries_no_mint_mark(db: Session) -> None:
    """A mint mark on "1878-1904" would claim every year came from one mint."""
    item = build_item(
        db,
        year_start=1878,
        year_end=1904,
        series_id=_id(db, Series, "morgan_dollar"),
    )
    db.add(CoinDetail(inventory_item_id=item.id, mint_id=_id(db, Mint, "S")))
    db.flush()

    assert suggested_title(db, item) == "1878-1904 Morgan Dollar MS64"


def test_the_denomination_names_a_coin_with_no_series(db: Session) -> None:
    item = build_item(
        db,
        year_start=1964,
        denomination_id=_id(db, Denomination, "usd_coin_0_50"),
        grade_id=None,
        strike_type_id=None,
    )

    assert suggested_title(db, item) == "1964 Half Dollar"


def test_a_self_graded_coin_does_not_name_the_owner_as_a_service(
    db: Session,
) -> None:
    """`SELF` is the owner's own opinion, not a service a buyer recognises."""
    item = build_item(
        db,
        year_start=1921,
        series_id=_id(db, Series, "morgan_dollar"),
        grading_service_id=_id(db, GradingService, "SELF"),
    )

    assert suggested_title(db, item) == "1921 Morgan Dollar MS64"


def test_a_set_is_named_by_its_form(db: Session) -> None:
    item = build_item(
        db,
        kind="set",
        title="Mint Set",
        year_start=1988,
        set_form_id=_id(db, SetForm, "mint_set"),
        grade_id=None,
        strike_type_id=None,
    )

    assert suggested_title(db, item) == "1988 Mint Set"


def test_a_note_is_titled_by_series_face_value_and_type(db: Session) -> None:
    """``$1 Bill`` becomes ``$1`` beside the note type, as it is said."""
    item = build_item(
        db,
        kind="currency",
        title="$1 Bill",
        year_start=1935,
        denomination_id=_id(db, Denomination, "usd_note_1"),
        grade_id=None,
        strike_type_id=None,
        grading_service_id=_id(db, GradingService, "PMG"),
    )
    db.add(
        CurrencyDetail(
            inventory_item_id=item.id,
            series_year=1935,
            series_letter="A",
            note_type_id=_id(db, NoteType, "silver_certificate"),
        )
    )
    db.flush()

    assert suggested_title(db, item) == "Series 1935A $1 Silver Certificate PMG"


def test_an_unclassified_item_keeps_the_seller_wording(db: Session) -> None:
    """Nothing to compose from but a year: the old default is the fallback."""
    item = build_item(
        db,
        title="Copper Round 1oz",
        year_start=2021,
        grade_id=None,
        strike_type_id=None,
    )

    assert suggested_title(db, item) == "Copper Round 1oz"


def test_a_date_and_mint_mark_alone_do_not_make_a_title(db: Session) -> None:
    """A date, mint mark and grade name nothing a buyer searches for."""
    item = build_item(db, title="Silver dollar, S mint", year_start=1921)
    db.add(CoinDetail(inventory_item_id=item.id, mint_id=_id(db, Mint, "S")))
    db.flush()

    assert suggested_title(db, item) == "Silver dollar, S mint"


def test_a_note_known_only_by_its_years_keeps_the_wording(db: Session) -> None:
    item = build_item(
        db,
        kind="currency",
        title="Old note",
        year_start=1935,
        year_end=1940,
        grade_id=None,
        strike_type_id=None,
    )

    assert suggested_title(db, item) == "Old note"


def test_the_endpoint_answers_per_item_and_skips_unknown_ids(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    item = build_item(db, year_start=1921, series_id=_id(db, Series, "morgan_dollar"))

    response = client.get(
        "/api/offers/titles",
        params={"item_ids": [item.id, 999_999_999]},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"titles": {str(item.id): "1921 Morgan Dollar MS64"}}


def test_the_endpoint_is_for_the_owner_only(client: TestClient) -> None:
    response = client.get("/api/offers/titles", params={"item_ids": [1]})
    assert response.status_code == 401
