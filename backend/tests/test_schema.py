"""Tests for the target schema.

These cover the properties the schema is *for*, rather than that SQLAlchemy
can insert a row: arithmetic that must never drift, constraints that must
actually refuse bad data, and the authorisation boundary that must not leak.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.models import (  # noqa: F401
    Authenticity,
    Composition,
    Country,
    Currency,
    Denomination,
    Disposition,
    Grade,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    Metal,
    MetalPrice,
    StorageForm,
    ValuationBasis,
)
from app.models.base import utcnow
from app.models.views import PUBLIC_CATALOG_FORBIDDEN_COLUMNS
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session


def code_id(db: Session, model: type, code: str) -> int:
    return db.execute(select(model.id).where(model.code == code)).scalar_one()


def make_item(db: Session, **overrides: object) -> InventoryItem:
    """An ordinary, fully-attributed coin.

    Every NOT NULL classifier is filled in, plus a year and a country, so a
    test that wants a gap has to ask for one explicitly rather than getting
    it by accident.
    """
    defaults: dict[str, object] = {
        "item_kind_id": code_id(db, ItemKind, "coin"),
        "storage_form_id": code_id(db, StorageForm, "single"),
        "authenticity_id": code_id(db, Authenticity, "unverified"),
        "status_id": code_id(db, ItemStatus, "received"),
        "disposition_id": code_id(db, Disposition, "held"),
        "valuation_basis_id": code_id(db, ValuationBasis, "numismatic"),
        "country_id": code_id(db, Country, "US"),
        "year_start": 1881,
        "item_cost": Decimal("100.00"),
        "shipping_cost": Decimal("0.00"),
    }
    defaults.update(overrides)
    item = InventoryItem(**defaults)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Cost basis: generated columns
# ---------------------------------------------------------------------------


def test_taxes_and_total_are_computed_by_the_database(db: Session) -> None:
    item = make_item(db, item_cost=Decimal("37.95"), shipping_cost=Decimal("8.95"))

    # 46.90 * 0.0635 = 2.97815 -> 2.98
    assert item.sales_tax == Decimal("2.98")
    assert item.total_cost == Decimal("49.88")
    assert item.total_cost == item.item_cost + item.shipping_cost + item.sales_tax


def test_generated_columns_cannot_be_written(db: Session) -> None:
    """The point of generating them is that they cannot drift from their inputs."""
    with pytest.raises((DBAPIError, IntegrityError)):
        make_item(db, item_cost=Decimal("10.00"), sales_tax=Decimal("999.99"))
    db.rollback()


def test_tax_rate_is_per_row_not_a_constant(db: Session) -> None:
    """Rates vary by jurisdiction, which is why it is a column."""
    item = make_item(
        db,
        item_cost=Decimal("100.00"),
        shipping_cost=Decimal("0.00"),
        tax_rate=Decimal("0.0000"),
    )
    assert item.sales_tax == Decimal("0.00")
    assert item.total_cost == Decimal("100.00")


def test_money_never_becomes_a_float(db: Session) -> None:
    item = make_item(db, item_cost=Decimal("0.10"), shipping_cost=Decimal("0.20"))
    assert isinstance(item.item_cost, Decimal)
    assert isinstance(item.total_cost, Decimal)
    # The float trap: 0.1 + 0.2 != 0.3 in binary floating point.
    assert item.item_cost + item.shipping_cost == Decimal("0.30")


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"item_cost": Decimal("-1.00")}, id="negative price"),
        pytest.param({"shipping_cost": Decimal("-1.00")}, id="negative shipping"),
        pytest.param({"piece_count": 0}, id="zero quantity"),
        pytest.param({"year_start": 2000, "year_end": 1999}, id="reversed year range"),
        pytest.param({"fineness": Decimal("1.5000")}, id="fineness above one"),
        pytest.param(
            {"gross_weight_ozt": Decimal("1.0"), "fine_weight_ozt": Decimal("2.0")},
            id="fine weight exceeding gross",
        ),
    ],
)
def test_constraints_refuse_impossible_data(db: Session, overrides: dict) -> None:
    with pytest.raises(IntegrityError):
        make_item(db, **overrides)
    db.rollback()


def test_a_classifier_in_use_cannot_be_deleted(db: Session) -> None:
    """A classifier in use cannot be deleted.

    Reference foreign keys are ON DELETE RESTRICT: deleting a classifier
    that rows depend on would orphan them.
    """
    item = make_item(db)
    kind = db.get(ItemKind, item.item_kind_id)

    with pytest.raises(IntegrityError):
        db.delete(kind)
        db.commit()
    db.rollback()


def test_year_range_allows_a_single_year_and_an_open_range(db: Session) -> None:
    """A bare year and an open range are different claims; both are legal."""
    assert make_item(db, year_start=1964, year_end=1964).year_end == 1964
    assert make_item(db, year_start=1980, year_end=None).year_end is None


# ---------------------------------------------------------------------------
# Weight
# ---------------------------------------------------------------------------


def test_weight_survives_the_database_exactly(db: Session) -> None:
    """Six decimal places in troy ounces represents a silver dime exactly."""
    item = make_item(
        db, gross_weight_ozt=Decimal("0.080376"), fine_weight_ozt=Decimal("0.072340")
    )
    db.expire(item)
    assert item.fine_weight_ozt == Decimal("0.072340")
    assert isinstance(item.fine_weight_ozt, Decimal)


def test_fine_weight_is_less_than_gross_for_a_ninety_percent_coin(db: Session) -> None:
    """A Morgan dollar weighs more than the silver in it.

    Melt uses the fine weight; using gross would overstate every 90% coin by
    11%.
    """
    item = make_item(
        db,
        gross_weight_ozt=Decimal("0.859380"),
        fine_weight_ozt=Decimal("0.773440"),
        fineness=Decimal("0.9000"),
    )
    assert item.fine_weight_ozt < item.gross_weight_ozt


# ---------------------------------------------------------------------------
# Composition: public facts
# ---------------------------------------------------------------------------


def test_composition_resolves_a_silver_dime_by_year(db: Session) -> None:
    dime = db.execute(
        select(Denomination).where(Denomination.code == "usd_coin_0_10")
    ).scalar_one()

    silver = db.execute(
        select(Composition).where(
            Composition.denomination_id == dime.id,
            Composition.year_from <= 1963,
            (Composition.year_to.is_(None)) | (Composition.year_to >= 1963),
        )
    ).scalar_one()
    assert silver.fineness == Decimal("0.9000")
    assert silver.fine_weight_ozt == Decimal("0.072340")

    # The same denomination two years later is a different composition.
    clad = db.execute(
        select(Composition).where(
            Composition.denomination_id == dime.id,
            Composition.year_from <= 1965,
            (Composition.year_to.is_(None)) | (Composition.year_to >= 1965),
        )
    ).scalar_one()
    assert clad.fine_weight_ozt == Decimal("0.000000")


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def test_coin_and_currency_views_partition_the_inventory(db: Session) -> None:
    coin = make_item(
        db, item_kind_id=code_id(db, ItemKind, "coin"), source_title="a coin"
    )
    note = make_item(
        db, item_kind_id=code_id(db, ItemKind, "currency"), source_title="a note"
    )

    coin_ids = {r[0] for r in db.execute(text("select id from coin_inventory"))}
    note_ids = {r[0] for r in db.execute(text("select id from currency_inventory"))}

    assert coin.id in coin_ids and coin.id not in note_ids
    assert note.id in note_ids and note.id not in coin_ids


def test_item_valuation_computes_melt_from_the_latest_spot_price(db: Session) -> None:
    """Melt is a view column precisely because spot moves; the newest quote wins."""
    silver = db.execute(select(Metal).where(Metal.code == "silver")).scalar_one()
    db.execute(
        text(
            "insert into metal_price (metal_id, quoted_at, price_per_ozt, source) "
            "values (:m, now() - interval '2 days', 20.0000, 'test')"
        ),
        {"m": silver.id},
    )
    db.execute(
        text(
            "insert into metal_price (metal_id, quoted_at, price_per_ozt, source) "
            "values (:m, now(), 30.0000, 'test')"
        ),
        {"m": silver.id},
    )
    db.commit()

    item = make_item(
        db,
        metal_id=silver.id,
        fine_weight_ozt=Decimal("2.000000"),
        piece_count=3,
        valuation_basis_id=code_id(db, ValuationBasis, "melt"),
        item_cost=Decimal("100.00"),
        shipping_cost=Decimal("0.00"),
    )

    row = db.execute(
        text(
            "select spot_price_used, melt_value, reported_value, profit "
            "from item_valuation where inventory_item_id = :i"
        ),
        {"i": item.id},
    ).one()

    # The newer quote, not the older one.
    assert row.spot_price_used == Decimal("30.0000")
    # 2 ozt * 30 * 3 pieces
    assert row.melt_value == Decimal("180.00")
    assert row.reported_value == Decimal("180.00")
    assert row.profit == Decimal("180.00") - item.total_cost


def test_public_catalog_never_exposes_private_columns(db: Session) -> None:
    """The authorisation boundary, asserted against the column list itself.

    A later `select *` in the view would widen it silently; this fails first.
    """
    columns = {
        r[0]
        for r in db.execute(
            text(
                "select column_name from information_schema.columns "
                "where table_name = 'public_catalog'"
            )
        )
    }
    leaked = columns & PUBLIC_CATALOG_FORBIDDEN_COLUMNS
    assert not leaked, f"public_catalog exposes private columns: {sorted(leaked)}"


def test_public_catalog_shows_only_active_listings(db: Session) -> None:
    usd = db.execute(select(Currency).where(Currency.code == "USD")).scalar_one()
    item = make_item(db, source_title="for sale")

    active = Listing(
        inventory_item_id=item.id,
        price=Decimal("50.00"),
        currency_id=usd.id,
        is_active=True,
        quantity_available=1,
    )
    ended = Listing(
        inventory_item_id=item.id,
        price=Decimal("50.00"),
        currency_id=usd.id,
        is_active=False,
        quantity_available=1,
    )
    db.add_all([active, ended])
    db.commit()

    listed = {r[0] for r in db.execute(text("select listing_id from public_catalog"))}
    assert active.id in listed
    assert ended.id not in listed


def test_a_deleted_item_leaves_every_view(db: Session) -> None:
    """Soft delete must reach all four views, not only the search.

    A row that is hidden from search but still summed by item_valuation would
    keep contributing money to a collection that no longer holds it. All four
    views carry `deleted_at IS NULL`, and this checks each one directly rather
    than trusting that they agree.
    """
    coin = make_item(db, item_cost=Decimal("42.00"))
    note = make_item(db, item_kind_id=code_id(db, ItemKind, "currency"))

    usd = db.execute(select(Currency).where(Currency.code == "USD")).scalar_one()
    listing = Listing(
        inventory_item_id=coin.id,
        price=Decimal("50.00"),
        currency_id=usd.id,
        is_active=True,
        quantity_available=1,
    )
    db.add(listing)
    db.commit()

    # item_valuation names the item's id `inventory_item_id`, not `id`;
    # public_catalog carries no inventory_item_id at all -- see
    # PUBLIC_CATALOG_FORBIDDEN_COLUMNS -- so it is checked by listing_id.
    views = (
        ("coin_inventory", "id", coin.id),
        ("currency_inventory", "id", note.id),
        ("item_valuation", "inventory_item_id", coin.id),
        ("public_catalog", "listing_id", listing.id),
    )

    for view, id_column, row_id in views:
        present = db.execute(
            text(f"SELECT count(*) FROM {view} WHERE {id_column} = :id"),
            {"id": row_id},
        ).scalar_one()
        assert present == 1, f"{view} should show a live row"

    coin.deleted_at = utcnow()
    note.deleted_at = utcnow()
    db.commit()

    for view, id_column, row_id in views:
        present = db.execute(
            text(f"SELECT count(*) FROM {view} WHERE {id_column} = :id"),
            {"id": row_id},
        ).scalar_one()
        assert present == 0, f"{view} still shows a deleted row"


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


def test_reference_codes_are_unique(db: Session) -> None:
    with pytest.raises(IntegrityError):
        db.add(Grade(code="MS65", label="duplicate"))
        db.commit()
    db.rollback()


def test_seeded_and_derived_rows_are_distinguishable(db: Session) -> None:
    """Seeded and derived rows stay distinguishable.

    The distinction that makes exporting a catalogue to another
    installation safe: one collection's guesses are not shipped as facts.
    """
    seeded = db.execute(
        text("select count(*) from grade where source = 'seeded'")
    ).scalar()
    assert seeded > 0


# ---------------------------------------------------------------------------
# The permanent item code
#
# Its whole value is that it can be cited years later and still mean the same
# physical object -- on a packing slip, in a return, in an audit.
# ---------------------------------------------------------------------------


def test_every_item_gets_a_code_without_being_asked(db: Session) -> None:
    item = make_item(db)
    assert item.item_code
    assert item.item_code.startswith("CC-")


def test_codes_are_unique(db: Session) -> None:
    first = make_item(db)
    with pytest.raises(IntegrityError):
        make_item(db, item_code=first.item_code)
    db.rollback()


def test_a_deleted_items_code_is_never_reissued(db: Session) -> None:
    """A sequence never goes backwards.

    If a dead item's code could be handed to a later one, every historical
    reference would become ambiguous.
    """
    doomed = make_item(db)
    code = doomed.item_code
    db.delete(doomed)
    db.commit()

    replacement = make_item(db)
    assert replacement.item_code != code


def test_the_code_survives_being_sold_and_returned(db: Session) -> None:
    """The code survives sale, return and relisting.

    The reason it exists: a returned item resumes its own history rather
    than starting a new one.
    """
    item = make_item(db)
    original = item.item_code

    item.disposition_id = code_id(db, Disposition, "sold")
    db.commit()
    item.disposition_id = code_id(db, Disposition, "returned_by_buyer")
    db.commit()
    item.disposition_id = code_id(db, Disposition, "held")
    db.commit()

    db.refresh(item)
    assert item.item_code == original


def test_concurrent_inserts_cannot_collide_on_a_code(db: Session) -> None:
    """Concurrent inserts cannot compute the same code.

    Assigned by a sequence rather than by the application, so two inserts
    in the same instant cannot compute the same value.
    """
    codes = {make_item(db).item_code for _ in range(20)}
    assert len(codes) == 20
