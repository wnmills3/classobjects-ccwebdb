"""`app.seller_titles`: the seller's words to the title, the record to the description.

`main` is always given the test's own `db`: left to itself it opens the
application's `SessionLocal`, which on this machine is the live database.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.models import (
    Denomination,
    InventoryItem,
    ItemFieldChange,
    PurchaseOrder,
    Series,
    User,
    Vendor,
)
from app.seller_titles import LONG_DESCRIPTION, SHORT_TITLE, apply, main, plan
from app.series_classify import classify, run
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, code_id

SELLER = "1881-S Morgan Silver Dollar BU from an old collection #412"


def _dollar(db: Session, **extra: object) -> InventoryItem:
    extra.setdefault("source_title", "1")
    extra.setdefault("description", SELLER)
    return build_bare_item(
        db,
        denomination_id=code_id(db, Denomination, "usd_coin_1_00"),
        series_id=code_id(db, Series, "morgan_dollar"),
        year_start=1881,
        year_end=1881,
        **extra,
    )


def _planned(db: Session) -> dict[str, tuple[str, str]]:
    return {c.item_code: (c.description, c.new_description) for c in plan(db).changes}


def test_a_face_value_title_gives_way_to_the_sellers_words(
    db: Session, admin_user: User
) -> None:
    item = _dollar(db)

    todo = plan(db)
    assert [c.item_code for c in todo.changes] == [item.item_code]
    written = todo.changes[0].new_description
    # Written from the record: it names what the item is, not the listing.
    assert "Morgan Dollar" in written
    assert "#412" not in written

    assert apply(db, todo, admin_user.id) == 1
    db.commit()
    db.refresh(item)
    assert item.source_title == SELLER
    assert item.description == written

    logged = db.execute(
        select(
            ItemFieldChange.field_name,
            ItemFieldChange.old_value,
            ItemFieldChange.new_value,
            ItemFieldChange.changed_by_id,
        ).where(ItemFieldChange.inventory_item_id == item.id)
    ).all()
    assert sorted(logged) == sorted(
        [
            ("source_title", "1", SELLER, admin_user.id),
            ("description", SELLER, written, admin_user.id),
        ]
    )


def test_a_second_run_changes_nothing(db: Session, admin_user: User) -> None:
    _dollar(db)
    apply(db, plan(db), admin_user.id)
    db.commit()

    again = plan(db)

    assert again.changes == []
    assert sum(again.left.values()) == 0


def test_what_is_left_alone_and_why(db: Session) -> None:
    long_title = _dollar(db, source_title="1881-S Morgan Dollar PGA MS63")
    at_the_edge = _dollar(db, source_title="x" * SHORT_TITLE)
    past_the_edge = _dollar(db, source_title="x" * (SHORT_TITLE + 1))
    short = _dollar(db, description="Item as shown #39")
    just_long = _dollar(db, description="y" * LONG_DESCRIPTION)
    too_long = _dollar(db, description="z" * 501)
    gone = _dollar(db)
    gone.deleted_at = gone.created_at
    db.commit()
    # Nothing recorded to describe it by: no year, denomination, series or grade.
    bare = build_bare_item(db, source_title="1", description=SELLER, year_start=None)

    todo = plan(db)

    changed = {c.item_code for c in todo.changes}
    assert changed == {at_the_edge.item_code, just_long.item_code}
    assert long_title.item_code not in changed
    assert past_the_edge.item_code not in changed
    assert gone.item_code not in changed
    assert todo.left == {
        "description too short to be a title": 1,
        "description too long to be a title": 1,
        "nothing in the record to describe it by": 1,
    }
    assert short.item_code not in changed
    assert too_long.item_code not in changed
    assert bare.item_code not in changed


def test_a_description_already_written_from_the_record_is_left(
    db: Session, admin_user: User
) -> None:
    item = _dollar(db)
    written = plan(db).changes[0].new_description
    # Someone has already pressed Suggest, but the title is still the face value.
    item.description = written
    db.commit()

    todo = plan(db)

    # Left alone, whichever rule says so: a short written description is
    # also too short to be a title.
    assert todo.changes == []
    assert sum(todo.left.values()) == 1


def test_a_dry_run_writes_nothing_and_a_commit_needs_a_person(
    db: Session, admin_user: User, capsys: pytest.CaptureFixture[str]
) -> None:
    item = _dollar(db, description=SELLER + " \U0001fa99")

    assert main([], db=db) == 0
    out = capsys.readouterr().out
    assert "items to change: 1" in out
    assert "dry run: nothing written" in out
    db.refresh(item)
    assert item.source_title == "1"

    with pytest.raises(SystemExit):
        main(["--commit"], db=db)
    assert main(["--commit", "--by", "nobody@example.com"], db=db) == 1
    db.refresh(item)
    assert item.source_title == "1"

    assert main(["--commit", "--by", admin_user.email.upper()], db=db) == 0
    db.refresh(item)
    assert item.source_title == SELLER + " \U0001fa99"


def test_a_lots_listing_is_still_lot_text_once_it_is_the_title(
    db: Session, admin_user: User
) -> None:
    """Moved into the title, a lot's listing is evidence about no single piece.

    Each piece then has its own written description, so the pieces no longer
    share title *and* description -- but the listing is no less the lot's.
    """
    vendor = Vendor(name="Lot Seller")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(
        vendor_id=vendor.id, order_number="LOT-9", ordered_on=date(2024, 12, 1)
    )
    db.add(order)
    db.flush()
    lot = "Coin Collection: Large Cents, Morgan dollars and Mercury dimes"
    cent, nickel = (
        build_bare_item(
            db,
            denomination_id=code_id(db, Denomination, denomination),
            year_start=1943,
            year_end=1943,
            source_title=title,
            description=lot,
            purchase_order_id=order.id,
        )
        for denomination, title in (
            ("usd_coin_0_01", "0.01"),
            ("usd_coin_0_05", "0.05"),
        )
    )

    apply(db, plan(db), admin_user.id)
    db.commit()
    db.refresh(cent)
    db.refresh(nickel)
    assert cent.source_title == nickel.source_title == lot
    assert cent.description != nickel.description

    report = run(db, commit=True)

    # Decided by the facts, as before the move: not sent to review as a 1943
    # cent that "says" Large Cent and Morgan Dollar.
    db.refresh(cent)
    db.refresh(nickel)
    assert db.get_one(Series, cent.series_id).code == "lincoln_cent"
    assert db.get_one(Series, nickel.series_id).code == "jefferson_nickel"
    assert [case for case in report.review if case.reason == "conflict"] == []
    assert classify(db).review == []
