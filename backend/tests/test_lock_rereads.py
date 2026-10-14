"""What a lock's re-read must not hide, and the rows each writer must hold.

`offering_writes.lock_for_sale` re-reads every row it locks, so after it the
session holds the current version and the version column can no longer see
a save that landed between a request's read and its lock. These tests land
that save at exactly that moment -- an UPDATE run inside the lock call,
before the real one. An edit that sent a token is asked again against it; a
receipt or a bulk edit sent none, and is not refused for a change it was
never shown (`test_buying_a_lot_races_marking_one_of_its_coins_missing`
needs exactly that of a receipt).
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from app import image_links, lot_writes, offering_writes
from app.lot_writes import LotRefused
from app.models import (
    Image,
    InventoryItem,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    SalesVenue,
)
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from tests.builders import code_id, patch_current
from tests.conftest import build_item


def _offered_item(db: Session) -> tuple[InventoryItem, Listing]:
    """A received item offered in the shop, with its claim, committed."""
    item = build_item(db)
    venue = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).first()
    assert venue is not None
    made = offering_writes.offer(
        db,
        item=item,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("50.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()
    return item, made


def _a_save_lands_while_locking(
    monkeypatch: pytest.MonkeyPatch, moved: Collection[int], **values: object
) -> None:
    """Have another save write `values` to these items just as the lock is taken.

    Once, at the request's first lock: the later passes `end_offer` takes
    re-lock rows already held, and landing there too would write the values
    back over whatever the request did with them.
    """
    real = offering_writes.lock_for_sale
    landed: list[bool] = []

    def landing(
        db: Session,
        *,
        listing_ids: Collection[int] = (),
        item_ids: Collection[int] = (),
        including_paused: bool = False,
    ) -> offering_writes.LockedForSale:
        """Write and move the versions, as a commit elsewhere would, then lock."""
        if not landed:
            landed.append(True)
            db.execute(
                update(InventoryItem)
                .where(InventoryItem.id.in_(moved))
                .values(version=InventoryItem.version + 1, **values)
                # Another session's commit: the instances here do not see it.
                .execution_options(synchronize_session=False)
            )
        return real(
            db,
            listing_ids=listing_ids,
            item_ids=item_ids,
            including_paused=including_paused,
        )

    monkeypatch.setattr(offering_writes, "lock_for_sale", landing)


def _listing_status(db: Session, listing_id: int) -> ListingStatus:
    """The listing's status as the database now holds it."""
    db.expire_all()
    listing = db.get(Listing, listing_id)
    assert listing is not None
    return listing.status


def _mark_missing(
    client: TestClient, headers: dict[str, str], item: InventoryItem, **token: object
) -> httpx.Response:
    """PATCH the item to `missing`, acknowledged, with whatever token is given."""
    return client.patch(
        f"/api/inventory/{item.id}",
        json={"status": "missing", "acknowledge_for_sale": True, **token},
        headers=headers,
    )


def test_an_edit_sent_with_a_version_is_refused_when_a_save_lands_while_it_locks(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The version passes against the item as loaded; the lock's re-read must not."""
    item, made = _offered_item(db)
    version = item.version
    _a_save_lands_while_locking(monkeypatch, [item.id])

    refused = _mark_missing(client, admin_headers, item, version=version)

    assert refused.status_code == 409, refused.text
    assert "changed while saving" in refused.json()["detail"]
    assert _listing_status(db, made.id) is ListingStatus.active


def test_an_edit_sent_with_a_base_is_refused_when_the_landed_save_took_its_field(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Someone else changed the status this edit changes: theirs must not be lost."""
    item, made = _offered_item(db)
    _a_save_lands_while_locking(
        monkeypatch, [item.id], status_id=code_id(db, ItemStatus, "returned")
    )

    refused = _mark_missing(client, admin_headers, item, base={"status": "received"})

    assert refused.status_code == 409, refused.text
    assert _listing_status(db, made.id) is ListingStatus.active


def test_an_edit_sent_with_a_base_keeps_a_landed_save_to_another_field(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A field-by-field merge: a change to another field is kept, not refused."""
    item, made = _offered_item(db)
    _a_save_lands_while_locking(
        monkeypatch, [item.id], description="Recorded at the other desk"
    )

    saved = _mark_missing(client, admin_headers, item, base={"status": "received"})

    assert saved.status_code == 200, saved.text
    assert _listing_status(db, made.id) is ListingStatus.ended
    kept = db.get(InventoryItem, item.id)
    assert kept is not None
    assert kept.description == "Recorded at the other desk"
    assert kept.status.code == "missing"


def test_the_console_s_edit_keeps_a_landed_save_to_another_field(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The item editor sends its version and a base together: the base decides."""
    item, made = _offered_item(db)
    version = item.version
    _a_save_lands_while_locking(
        monkeypatch, [item.id], description="Recorded at the other desk"
    )

    saved = _mark_missing(
        client, admin_headers, item, version=version, base={"status": "received"}
    )

    assert saved.status_code == 200, saved.text
    assert _listing_status(db, made.id) is ListingStatus.ended
    kept = db.get(InventoryItem, item.id)
    assert kept is not None
    assert kept.description == "Recorded at the other desk"
    assert kept.status.code == "missing"


def test_an_edit_keeps_the_no_date_a_landed_save_set(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What the edit works out from the item is worked out from it as it now is.

    The edit sends neither `no_date` nor a year, so it must leave both as
    the save that landed while it locked left them.
    """
    item, _ = _offered_item(db)
    assert item.no_date is False
    _a_save_lands_while_locking(
        monkeypatch, [item.id], no_date=True, year_start=None, year_end=None
    )

    saved = _mark_missing(client, admin_headers, item, base={"status": "received"})

    assert saved.status_code == 200, saved.text
    db.expire_all()
    kept = db.get_one(InventoryItem, item.id)
    assert (kept.no_date, kept.year_start, kept.year_end) == (True, None, None)


def test_a_bulk_edit_works_out_the_years_from_the_item_as_locked(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A year sent alone keeps a range's end: the range the landed save made."""
    item, made = _offered_item(db)
    assert (item.year_start, item.year_end) == (1881, None)  # a single year
    _a_save_lands_while_locking(monkeypatch, [item.id], year_end=1890)

    edited = client.post(
        "/api/inventory/bulk",
        json={
            "ids": [item.id],
            "changes": {
                "status": "missing",
                "year_start": 1885,
                "acknowledge_for_sale": True,
            },
        },
        headers=admin_headers,
    )

    assert edited.status_code == 200, edited.text
    assert _listing_status(db, made.id) is ListingStatus.ended
    db.expire_all()
    kept = db.get_one(InventoryItem, item.id)
    assert (kept.year_start, kept.year_end) == (1885, 1890)


def _statements_while(db: Session, act: Callable[[], object]) -> list[str]:
    """Every statement `act` sends on the session's connection, in order."""
    seen: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_: object) -> None:
        """Keep each statement."""
        seen.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", record)
    try:
        act()
    finally:
        event.remove(connection, "before_cursor_execute", record)
    return seen


def _holds_item(statement: str) -> bool:
    """Whether a statement locks or writes an `inventory_item` row."""
    return statement.startswith("UPDATE inventory_item ") or (
        "FOR UPDATE" in statement and "FROM inventory_item" in statement
    )


def test_replacing_an_item_s_errors_holds_the_item_before_its_errors(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """A merge of error types holds the items, then their errors: the same order.

    Taken the other way round, the replacement and the merge could each hold
    what the other waits for.
    """
    item = build_item(db)
    url = f"/api/inventory/{item.id}/errors"
    body = {"errors": [{"error_type": "doubled_die"}]}

    seen = _statements_while(
        db, lambda: client.put(url, json=body, headers=admin_headers)
    )

    held = [n for n, s in enumerate(seen) if _holds_item(s)]
    errors = [
        n
        for n, s in enumerate(seen)
        if s.startswith(("DELETE FROM item_error ", "INSERT INTO item_error "))
    ]
    assert held and errors, seen
    assert held[0] < errors[0], seen


def test_setting_only_an_item_s_attributes_holds_the_item_before_its_links(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    """An attributes-only edit has no item column to write before its links."""
    item = build_item(db)

    seen = _statements_while(
        db,
        lambda: patch_current(
            client,
            f"/api/inventory/{item.id}",
            json={"attributes": ["first_strike"]},
            headers=admin_headers,
        ),
    )

    held = [n for n, s in enumerate(seen) if _holds_item(s)]
    writes_link = ("INSERT INTO item_attribute_link ", "UPDATE item_attribute_link ")
    links = [n for n, s in enumerate(seen) if s.startswith(writes_link)]
    assert held and links, seen
    assert held[0] < links[0], seen


def test_a_receipt_is_not_refused_for_a_save_it_was_never_shown(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A receipt sends no version: a coin that went missing is recorded missing."""
    item, made = _offered_item(db)
    _a_save_lands_while_locking(monkeypatch, [item.id])

    marked = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "missing",
            "acknowledge_for_sale": True,
        },
        headers=admin_headers,
    )

    assert marked.status_code == 200, marked.text
    assert _listing_status(db, made.id) is ListingStatus.ended


def test_a_bulk_edit_is_not_refused_for_a_save_it_was_never_shown(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bulk edit sends no version either: it sets what it was asked to set."""
    item, made = _offered_item(db)
    _a_save_lands_while_locking(monkeypatch, [item.id])

    edited = client.post(
        "/api/inventory/bulk",
        json={
            "ids": [item.id],
            "changes": {"status": "missing", "acknowledge_for_sale": True},
        },
        headers=admin_headers,
    )

    assert edited.status_code == 200, edited.text
    assert _listing_status(db, made.id) is ListingStatus.ended


def test_a_batch_offer_takes_every_item_in_one_statement_first(
    client: TestClient,
    db: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Taken one at a time in the order typed, a batch can deadlock a checkout."""
    first = build_item(db)
    second = build_item(db)
    db.commit()
    taken: list[list[int]] = []
    real = offering_writes.lock_for_sale

    def watched(
        db: Session,
        *,
        listing_ids: Collection[int] = (),
        item_ids: Collection[int] = (),
        including_paused: bool = False,
    ) -> offering_writes.LockedForSale:
        """Record which items each lock pass takes."""
        taken.append(sorted(item_ids))
        return real(
            db,
            listing_ids=listing_ids,
            item_ids=item_ids,
            including_paused=including_paused,
        )

    monkeypatch.setattr(offering_writes, "lock_for_sale", watched)

    made = client.post(
        "/api/offers",
        json={
            "venue": "store",
            "format": "fixed_price",
            "items": [
                {"item_id": second.id, "price": "10.00"},
                {"item_id": first.id, "price": "10.00"},
            ],
        },
        headers=admin_headers,
    )

    assert made.status_code == 201, made.text
    assert taken[0] == sorted([first.id, second.id])


def test_an_item_deleted_since_it_was_read_does_not_join_a_lot(db: Session) -> None:
    """The delete committed after the caller loaded the item; the add must see it."""
    item = build_item(db)
    lot = lot_writes.create_lot(db, title="Two of a kind")
    db.execute(
        update(InventoryItem)
        .where(InventoryItem.id == item.id)
        .values(deleted_at=datetime(2026, 1, 1, tzinfo=UTC))
        # Another session's commit: the item loaded here does not see it.
        .execution_options(synchronize_session=False)
    )

    with pytest.raises(LotRefused, match="has been deleted"):
        lot_writes.add_member(db, lot, item)


def _items_locked_while(db: Session, act: Callable[[], object]) -> list[str]:
    """The `inventory_item ... FOR UPDATE` statements `act` sends."""
    seen: list[str] = []

    def record(_conn: object, _cursor: object, statement: str, *_: object) -> None:
        """Keep each statement that locks item rows."""
        if "FOR UPDATE" in statement and "FROM inventory_item" in statement:
            seen.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", record)
    try:
        act()
    finally:
        event.remove(connection, "before_cursor_execute", record)
    return seen


def test_filing_a_photograph_holds_the_item_while_it_picks_the_place(
    db: Session,
) -> None:
    """Two photographs filed at once would otherwise take one place and name."""
    item = build_item(db)
    image = Image(
        sha256="4c" * 32,
        storage_key=f"orig/{'4c' * 32}.jpg",
        media_type="image/jpeg",
        byte_size=10,
    )
    db.add(image)
    db.flush()

    assert _items_locked_while(db, lambda: image_links.next_position(db, item.id))
    assert _items_locked_while(
        db,
        lambda: image_links.attach(
            # A place given, so `next_position`'s own hold is not what is seen.
            db,
            image=image,
            item=item,
            role=None,
            is_primary=False,
            sort_order=5,
        ),
    )
