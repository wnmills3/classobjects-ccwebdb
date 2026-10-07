"""The small helpers several callers share: name validators and the row lock."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app import offering_writes
from app.models import InventoryItem, SalesLot
from app.schemas import (
    PurchaseOrderCreate,
    PurchaseOrderUpdate,
    SellerCreate,
    SellerUpdate,
    VendorCreate,
    VendorUpdate,
)
from pydantic import BaseModel, ValidationError
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from tests.builders import build_bare_item
from tests.conftest import build_lot

# --------------------------------------------------------------------------
# A name is trimmed, and one with nothing left is refused
# --------------------------------------------------------------------------

NAMED = [SellerCreate, SellerUpdate, VendorCreate, VendorUpdate]


@pytest.mark.parametrize("schema", NAMED)
def test_a_name_loses_its_surrounding_space(schema: type[BaseModel]) -> None:
    assert schema.model_validate({"name": "  Apmex \t"}).model_dump()["name"] == "Apmex"


@pytest.mark.parametrize("schema", NAMED)
def test_a_name_of_spaces_alone_is_refused_as_blank(schema: type[BaseModel]) -> None:
    # Three spaces pass `min_length=1`: only the validator can refuse them.
    with pytest.raises(ValidationError) as refused:
        schema.model_validate({"name": "   "})
    errors = refused.value.errors()
    assert [e["loc"] for e in errors] == [("name",)]
    assert "name must not be blank" in errors[0]["msg"]


@pytest.mark.parametrize("schema", [SellerUpdate, VendorUpdate])
def test_a_change_that_sends_no_name_leaves_it_alone(schema: type[BaseModel]) -> None:
    assert schema.model_validate({}).model_dump(exclude_unset=True) == {}
    assert schema.model_validate({"name": None}).model_dump()["name"] is None


# --------------------------------------------------------------------------
# A blank is no value
# --------------------------------------------------------------------------


def test_a_blank_order_number_is_none_and_a_typed_one_is_trimmed() -> None:
    blank = PurchaseOrderCreate.model_validate({"vendor_id": 1, "order_number": "  "})
    typed = PurchaseOrderCreate.model_validate(
        {"vendor_id": 1, "order_number": " 12-345 "}
    )
    assert blank.order_number is None
    assert typed.order_number == "12-345"


def test_a_purchase_change_blanks_its_number_and_its_notes_alike() -> None:
    change = PurchaseOrderUpdate.model_validate({"order_number": "", "notes": " \n"})
    kept = PurchaseOrderUpdate.model_validate({"order_number": " A-1", "notes": " x "})
    assert (change.order_number, change.notes) == (None, None)
    assert (kept.order_number, kept.notes) == ("A-1", "x")


# --------------------------------------------------------------------------
# offering_writes._lock_rows
# --------------------------------------------------------------------------


@pytest.fixture
def locks(db: Session) -> Iterator[list[str]]:
    """Each `FOR UPDATE` statement `db`'s connection runs while the test holds this.

    Only those: a commit's savepoint and the reload of an expired row are the
    session's own housekeeping, and say nothing about what was locked.
    """
    seen: list[str] = []

    def record(*args: object) -> None:
        """Keep the text of one execution when it takes a row lock."""
        statement = str(args[2])
        if "FOR UPDATE" in statement:
            seen.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", record)
    yield seen
    event.remove(connection, "before_cursor_execute", record)


def test_locking_no_rows_takes_no_lock(db: Session, locks: list[str]) -> None:
    offering_writes._lock_items(db, [])
    offering_writes._lock_lots(db, set())
    assert locks == []


def test_items_are_locked_in_one_statement_in_id_order(
    db: Session, locks: list[str]
) -> None:
    ids = [build_bare_item(db).id for _ in range(3)]
    db.commit()

    # Handed over highest first and with a repeat: one statement, sorted.
    offering_writes._lock_items(db, [ids[2], ids[0], ids[1], ids[2]])

    assert len(locks) == 1
    assert "FROM inventory_item" in locks[0]
    assert "ORDER BY inventory_item.id" in locks[0]
    assert locks[0].rstrip().endswith("FOR UPDATE")


def test_a_lot_is_locked_from_its_own_table(db: Session, locks: list[str]) -> None:
    lot_id = build_lot(db, [build_bare_item(db), build_bare_item(db)]).id
    db.commit()
    locks.clear()

    offering_writes._lock_lots(db, [lot_id])

    assert len(locks) == 1
    assert "FROM sales_lot" in locks[0]
    assert "ORDER BY sales_lot.id" in locks[0]
    assert locks[0].rstrip().endswith("FOR UPDATE")


def test_the_lock_re_reads_a_row_the_session_already_holds(db: Session) -> None:
    """A value read before the lock is replaced by the row as it now stands."""
    item = build_bare_item(db, source_title="As first read")
    lot = build_lot(db, [item, build_bare_item(db)], title="As first read")
    db.commit()
    assert (item.source_title, lot.title) == ("As first read", "As first read")

    # Changed underneath the session, as another request's commit would.
    db.execute(
        text("UPDATE inventory_item SET source_title = 'Changed since' WHERE id = :i"),
        {"i": item.id},
    )
    db.execute(
        text("UPDATE sales_lot SET title = 'Changed since' WHERE id = :i"),
        {"i": lot.id},
    )
    assert (item.source_title, lot.title) == ("As first read", "As first read")

    offering_writes._lock_items(db, [item.id])
    offering_writes._lock_lots(db, [lot.id])

    held_item = db.get(InventoryItem, item.id)
    held_lot = db.get(SalesLot, lot.id)
    assert held_item is item and held_lot is lot
    assert (item.source_title, lot.title) == ("Changed since", "Changed since")
