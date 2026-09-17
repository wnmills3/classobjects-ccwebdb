"""The purchase-source clean-up pass (selling design, phase 0)."""

from __future__ import annotations

import pytest
from app.models import PurchaseOrder, Vendor, VendorKind
from app.vendor_cleanup import CleanupError, run
from sqlalchemy import select
from sqlalchemy.orm import Session

# Setup is committed, not flushed. Under the `db` fixture a commit releases a
# savepoint, so the rollback `run` does on a dry run or a refusal discards
# only what `run` did -- not the vendors the test created.


def _vendor(db: Session, name: str) -> Vendor:
    vendor = Vendor(name=name)
    db.add(vendor)
    db.commit()
    return vendor


def _order(db: Session, vendor: Vendor, number: str | None) -> PurchaseOrder:
    order = PurchaseOrder(vendor_id=vendor.id, order_number=number)
    db.add(order)
    db.commit()
    return order


def test_a_dry_run_reports_and_writes_nothing(db: Session) -> None:
    typo = _vendor(db, "builionsharks.com")
    real = _vendor(db, "bullionshark.com")
    order = _order(db, typo, "A1")

    report = run(db, merges=[(typo.id, real.id)], kinds=[], deletes=[], commit=False)

    assert report.merged == [("builionsharks.com", "bullionshark.com", 1)]
    db.expire_all()
    assert db.get(PurchaseOrder, order.id).vendor_id == typo.id
    assert db.get(Vendor, typo.id) is not None


def test_a_merge_moves_orders_and_removes_the_duplicate(db: Session) -> None:
    typo = _vendor(db, "hibid.co")
    real = _vendor(db, "hibid.com")
    moved = _order(db, typo, None)
    kept = _order(db, real, "X9")

    run(db, merges=[(typo.id, real.id)], kinds=[], deletes=[], commit=True)

    db.expire_all()
    assert db.get(PurchaseOrder, moved.id).vendor_id == real.id
    assert db.get(PurchaseOrder, kept.id).vendor_id == real.id
    assert db.get(Vendor, typo.id) is None


def test_a_merge_that_would_duplicate_an_order_number_is_refused(db: Session) -> None:
    left = _vendor(db, "a.example")
    right = _vendor(db, "b.example")
    _order(db, left, "SAME")
    _order(db, right, "SAME")

    with pytest.raises(CleanupError, match="SAME"):
        run(db, merges=[(left.id, right.id)], kinds=[], deletes=[], commit=True)


def test_delete_refuses_a_vendor_with_orders(db: Session) -> None:
    used = _vendor(db, "used.example")
    _order(db, used, "1")

    with pytest.raises(CleanupError, match=r"used\.example"):
        run(db, merges=[], kinds=[], deletes=[used.id], commit=True)


def test_delete_removes_an_unused_vendor(db: Session) -> None:
    typo = _vendor(db, "usming.gov")

    report = run(db, merges=[], kinds=[], deletes=[typo.id], commit=True)

    assert report.deleted == ["usming.gov"]
    db.expire_all()
    assert db.get(Vendor, typo.id) is None


def test_kind_is_set_by_code(db: Session) -> None:
    vendor = _vendor(db, "whatnot.example")

    run(db, merges=[], kinds=[(vendor.id, "marketplace")], deletes=[], commit=True)

    db.expire_all()
    marketplace = db.scalar(select(VendorKind).where(VendorKind.code == "marketplace"))
    assert db.get(Vendor, vendor.id).vendor_kind_id == marketplace.id


def test_an_unknown_kind_is_an_error(db: Session) -> None:
    vendor = _vendor(db, "x.example")

    with pytest.raises(CleanupError, match="nonsense"):
        run(db, merges=[], kinds=[(vendor.id, "nonsense")], deletes=[], commit=True)


def test_an_unknown_vendor_id_is_an_error(db: Session) -> None:
    with pytest.raises(CleanupError, match="999999"):
        run(db, merges=[], kinds=[(999999, "dealer")], deletes=[], commit=False)


def test_refusal_writes_nothing(db: Session) -> None:
    """A later refusal in the same call rolls back an earlier write in it.

    No merges are involved here (Controller ruling R1): a merge clash is
    raised before any kind is set, so a test built on a merge clash proves
    nothing about rollback. Two kind assignments do: the first (a real code)
    succeeds and is flushed, the second (an unknown code) then raises, and
    the assertion is that the first's write did not survive.
    """
    other = _vendor(db, "e.example")
    other2 = _vendor(db, "f.example")

    with pytest.raises(CleanupError, match="nonsense"):
        run(
            db,
            merges=[],
            kinds=[(other.id, "dealer"), (other2.id, "nonsense")],
            deletes=[],
            commit=True,
        )

    db.expire_all()
    assert db.get(Vendor, other.id).vendor_kind_id is None
