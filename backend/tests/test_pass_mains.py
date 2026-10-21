"""Each pass's command line, run through `main` in the test's own session.

Every `main` here is given `db=`: left to itself it opens the application's
`SessionLocal`, which is the application database, not the test one. Each
pass is run twice -- a dry run that must write nothing, then `--commit`
(with `--by` where the pass logs History) that must write.

Setup is committed, not flushed: under the `db` fixture a commit releases a
savepoint, so a pass that rolls back its dry run discards only its own work.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app import (
    bullion_weights,
    classifier_defaults,
    listing_links,
    series_classify,
    series_match,
    vendor_cleanup,
)
from app.models import (
    BullionForm,
    Denomination,
    InventoryItem,
    Metal,
    PurchaseOrder,
    Series,
    User,
    Vendor,
)
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, build_purchase_order, code_id


def _fresh(db: Session, item: InventoryItem) -> InventoryItem:
    """The item as the database now holds it."""
    db.expire_all()
    return db.get_one(InventoryItem, item.id)


def _series(db: Session, item: InventoryItem) -> str | None:
    """The code of the series the item now holds, or None."""
    series_id = _fresh(db, item).series_id
    return None if series_id is None else db.get_one(Series, series_id).code


def test_bullion_weights_main(
    db: Session,
    make_item: ItemFactory,
    admin_user: User,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bar = make_item(kind="bullion", year_start=None, title="5 oz. .999 Fine Silver Bar")
    bar.bullion_form_id = code_id(db, BullionForm, "bar")
    bar.metal_id = code_id(db, Metal, "silver")
    db.commit()

    assert bullion_weights.main([], db=db) == 0
    assert "dry run" in capsys.readouterr().out
    assert _fresh(db, bar).gross_weight_ozt is None

    by = ["--commit", "--by", admin_user.email]
    assert bullion_weights.main(by, db=db) == 0
    assert _fresh(db, bar).gross_weight_ozt == Decimal("5.000000")


def test_listing_links_main(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    order = build_purchase_order(db, vendor_name="ebay.com")
    item = make_item(purchase_order_id=order.id, sellers_item_id="100000000001")
    db.commit()

    assert listing_links.main([], db=db) == 0
    assert _fresh(db, item).listing_url is None

    by = ["--commit", "--by", admin_user.email]
    assert listing_links.main(by, db=db) == 0
    assert _fresh(db, item).listing_url == "https://www.ebay.com/itm/100000000001"


def test_a_pass_refuses_a_by_that_names_no_account(
    db: Session, make_item: ItemFactory
) -> None:
    """`pass_cli.commit_or_report`'s exit status for an unknown `--by` is 1."""
    order = build_purchase_order(db, vendor_name="ebay.com")
    item = make_item(purchase_order_id=order.id, sellers_item_id="100000000002")
    db.commit()

    by = ["--commit", "--by", "nobody@example.com"]
    assert listing_links.main(by, db=db) == 1
    assert _fresh(db, item).listing_url is None


def test_classifier_defaults_main(db: Session, make_item: ItemFactory) -> None:
    dime = make_item(
        title="Plain coin",
        denomination_id=code_id(db, Denomination, "usd_coin_0_10"),
        year_start=1964,
    )
    db.commit()

    assert classifier_defaults.main([], db=db) == 0
    assert _fresh(db, dime).composition_id is None

    assert classifier_defaults.main(["--commit"], db=db) == 0
    assert _fresh(db, dime).composition_id is not None


def test_series_classify_main(db: Session, make_item: ItemFactory) -> None:
    # Nothing in the text: a 1942 dime's denomination and year are enough.
    dime = make_item(
        title="Plain coin",
        description="10C MS65",
        denomination_id=code_id(db, Denomination, "usd_coin_0_10"),
        year_start=1942,
    )
    db.commit()

    assert series_classify.main([], db=db) == 0
    assert _series(db, dime) is None

    assert series_classify.main(["--commit"], db=db) == 0
    assert _series(db, dime) == "winged_liberty_head_dime"


def test_series_match_main(db: Session, make_item: ItemFactory) -> None:
    item = make_item(title="1881-S Morgan Silver Dollar", description="Nice strike.")
    db.commit()

    assert series_match.main([], db=db) == 0
    assert _series(db, item) is None

    assert series_match.main(["--commit"], db=db) == 0
    assert _series(db, item) == "morgan_dollar"


def test_vendor_cleanup_main(db: Session) -> None:
    typo = Vendor(name="exmaple-coins.com")
    real = Vendor(name="example-coins.com")
    db.add_all([typo, real])
    db.flush()
    order = PurchaseOrder(vendor_id=typo.id)
    db.add(order)
    db.commit()
    merge = ["--merge", f"{typo.id}:{real.id}"]

    assert vendor_cleanup.main(merge, db=db) == 0
    db.expire_all()
    assert db.get_one(PurchaseOrder, order.id).vendor_id == typo.id

    assert vendor_cleanup.main([*merge, "--commit"], db=db) == 0
    db.expire_all()
    assert db.get_one(PurchaseOrder, order.id).vendor_id == real.id
    assert db.get(Vendor, typo.id) is None
