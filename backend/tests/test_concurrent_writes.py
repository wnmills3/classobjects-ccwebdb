"""Multiple people writing to the same collection at once.

Two different concurrency problems live here, and they need different tools:

**Editing an item** is a document edit. Two people load the same record,
think for a while, and save. Conflicts are rare, and when one happens a person
can look at both versions and decide. So it is optimistic: no locks, a version
column, and a 409 if someone got there first. Holding a row lock for the life
of an edit form would block every reader of that row for minutes.

**Decrementing stock** is not a document edit. Conflicts are the normal case
at checkout, the transaction is milliseconds long, and there is nothing for a
human to merge -- so it is pessimistic, with SELECT ... FOR UPDATE. That is
covered in test_concurrency.py.

As there, these tests do not go through TestClient for the racing part:
starlette funnels its requests through a single portal, so two "concurrent"
client calls run one after another and would pass with every protection
removed.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from app.models import (
    Authenticity,
    Disposition,
    InventoryItem,
    ItemKind,
    ItemStatus,
    StorageForm,
    ValuationBasis,
)
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError


def _code(session: Session, model: type, code: str) -> int:
    return session.execute(select(model.id).where(model.code == code)).scalar_one()


@pytest.fixture
def committed(engine: Engine) -> None:
    """Real, committing sessions. Cleans up what it makes."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    with factory() as cleanup:
        cleanup.execute(
            text(
                "DELETE FROM item_status_history WHERE inventory_item_id IN "
                "(SELECT id FROM inventory_item WHERE source_title LIKE 'WRITE-RACE%')"
            )
        )
        cleanup.execute(
            text(
                "DELETE FROM coin_detail WHERE inventory_item_id IN "
                "(SELECT id FROM inventory_item WHERE source_title LIKE 'WRITE-RACE%')"
            )
        )
        cleanup.execute(
            text(
                "DELETE FROM listing WHERE inventory_item_id IN "
                "(SELECT id FROM inventory_item WHERE source_title LIKE 'WRITE-RACE%')"
            )
        )
        cleanup.execute(
            text(
                "UPDATE inventory_item SET parent_item_id = NULL "
                "WHERE source_title LIKE 'WRITE-RACE%'"
            )
        )
        cleanup.execute(
            text("DELETE FROM inventory_item WHERE source_title LIKE 'WRITE-RACE%'")
        )
        cleanup.commit()


def make_lot(
    factory: sessionmaker[Session], *, quantity: int = 4, price: str = "100.00"
) -> int:
    with factory() as session:
        item = InventoryItem(
            source_title="WRITE-RACE lot",
            piece_count=quantity,
            item_cost=Decimal(price),
            item_kind_id=_code(session, ItemKind, "coin"),
            storage_form_id=_code(session, StorageForm, "single"),
            authenticity_id=_code(session, Authenticity, "unverified"),
            status_id=_code(session, ItemStatus, "received"),
            disposition_id=_code(session, Disposition, "held"),
            valuation_basis_id=_code(session, ValuationBasis, "numismatic"),
        )
        session.add(item)
        session.commit()
        return item.id


# ---------------------------------------------------------------------------
# Lost updates
# ---------------------------------------------------------------------------


def test_a_second_writer_is_refused_rather_than_silently_winning(
    committed: sessionmaker[Session],
) -> None:
    """The failure this exists to prevent.

    Both writers loaded the same row. Without the version column the second
    commit overwrites the first with values read before it happened, and
    nobody is told.
    """
    item_id = make_lot(committed)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def edit(new_title: str) -> str:
        with committed() as session:
            row = session.get(InventoryItem, item_id)
            _ = row.version  # both read the same version
            barrier.wait(timeout=10)
            row.source_title = new_title
            try:
                session.commit()
                return "committed"
            except StaleDataError:
                session.rollback()
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(edit, ["WRITE-RACE A", "WRITE-RACE B"]))

    assert outcomes == ["committed", "refused"], (
        f"expected one writer to be refused, got {outcomes}"
    )


def test_the_version_advances_on_every_write(committed: sessionmaker[Session]) -> None:
    item_id = make_lot(committed)
    with committed() as session:
        row = session.get(InventoryItem, item_id)
        first = row.version
        row.source_title = "WRITE-RACE renamed"
        session.commit()
        assert row.version == first + 1


def test_reads_are_never_blocked_by_a_write(committed: sessionmaker[Session]) -> None:
    """A reader is never made to wait for a writer.

    MVCC's half of the guarantee: a reader sees the previous committed
    value immediately rather than waiting for the writer to finish.
    """
    item_id = make_lot(committed)
    writing = threading.Event()
    may_finish = threading.Event()
    read_value: list[str] = []

    def slow_writer() -> None:
        with committed() as session:
            row = session.get(InventoryItem, item_id)
            row.source_title = "WRITE-RACE mid-flight"
            session.flush()  # holds the row lock, uncommitted
            writing.set()
            may_finish.wait(timeout=10)
            session.commit()

    def reader() -> None:
        writing.wait(timeout=10)
        with committed() as session:
            # Must return at once, with the pre-write value.
            row = session.get(InventoryItem, item_id)
            read_value.append(row.source_title)
        may_finish.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda f: f(), [slow_writer, reader]))

    assert read_value == ["WRITE-RACE lot"], (
        "the reader should have seen the committed value without waiting"
    )


# ---------------------------------------------------------------------------
# Splitting the same lot twice
# ---------------------------------------------------------------------------


def test_two_people_cannot_split_the_same_lot_at_once(
    committed: sessionmaker[Session],
) -> None:
    """Check-then-act on one row, so the lot is locked before the decision.

    Without the lock both callers read split_at as NULL, both proceed, and the
    lot is broken up twice -- eight pieces from four, with its cost basis
    allocated twice over.
    """
    from app.splitting import SplitError, SplitPiece, split_item

    item_id = make_lot(committed, quantity=4)
    barrier = threading.Barrier(2)

    def attempt(tag: str) -> str:
        with committed() as session:
            parent = session.get(InventoryItem, item_id)
            barrier.wait(timeout=10)
            try:
                split_item(
                    session,
                    parent,
                    [SplitPiece(source_title=f"WRITE-RACE {tag}{n}") for n in range(4)],
                )
                session.commit()
                return "split"
            except SplitError:
                session.rollback()
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(attempt, ["A", "B"]))

    assert outcomes == ["refused", "split"], f"got {outcomes}"

    with committed() as session:
        children = (
            session.query(InventoryItem)
            .filter(InventoryItem.parent_item_id == item_id)
            .count()
        )
    assert children == 4, f"the lot was split twice: {children} pieces"


# The optimistic-locking behaviour this file used to check "through the API"
# was `PATCH /api/catalog/{id}` -- retired along with the rest of the
# catalogue's write endpoints (see `test_catalog.py`). Its replacement,
# `PATCH /api/listings/{id}` (`app.routers.offers`), has its own version
# field and its own conflict test: `test_offers_api.py::
# test_editing_an_offer_and_a_stale_version`. It has nothing analogous to
# `test_the_token_covers_the_item_row_not_just_the_listing`'s compound
# listing-plus-item token, because it never touches the item row -- editing
# the item is `PATCH /api/inventory/{id}`, versioned on its own.
