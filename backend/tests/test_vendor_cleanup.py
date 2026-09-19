"""The purchase-source clean-up pass (selling design, phase 0)."""

from __future__ import annotations

import pytest
from app.models import PurchaseOrder, SalesVenue, SalesVenueKind, Vendor, VendorKind
from app.vendor_cleanup import CleanupError, run
from sqlalchemy import func, select
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


def _platform(db: Session, code: str, vendor: Vendor) -> SalesVenue:
    kind_id = db.scalar(
        select(SalesVenueKind.id).where(SalesVenueKind.code == "marketplace")
    )
    venue = SalesVenue(
        code=code, name=code.title(), sales_venue_kind_id=kind_id, vendor_id=vendor.id
    )
    db.add(venue)
    db.commit()
    return venue


def test_a_dry_run_reports_and_writes_nothing(db: Session) -> None:
    typo = _vendor(db, "builionsharks.com")
    real = _vendor(db, "bullionshark.com")
    order = _order(db, typo, "A1")

    report = run(db, merges=[(typo.id, real.id)], kinds=[], deletes=[], commit=False)

    assert report.merged == [("builionsharks.com", "bullionshark.com", 1)]
    db.expire_all()
    assert db.get_one(PurchaseOrder, order.id).vendor_id == typo.id
    assert db.get(Vendor, typo.id) is not None


def test_a_merge_moves_orders_and_removes_the_duplicate(db: Session) -> None:
    typo = _vendor(db, "hibid.co")
    real = _vendor(db, "hibid.com")
    moved = _order(db, typo, None)
    kept = _order(db, real, "X9")

    run(db, merges=[(typo.id, real.id)], kinds=[], deletes=[], commit=True)

    db.expire_all()
    assert db.get_one(PurchaseOrder, moved.id).vendor_id == real.id
    assert db.get_one(PurchaseOrder, kept.id).vendor_id == real.id
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


def test_delete_refuses_a_vendor_a_platform_sells_through(db: Session) -> None:
    """The clean-up refuses, naming the platform, rather than the database.

    `sales_venue.vendor_id` is ON DELETE RESTRICT, so without the check the
    DELETE reaches PostgreSQL and comes back as a raw IntegrityError -- an
    operator traceback where a REFUSED line belongs.
    """
    linked = _vendor(db, "ebay.example")
    _platform(db, "ebay", linked)

    with pytest.raises(CleanupError, match="ebay"):
        run(db, merges=[], kinds=[], deletes=[linked.id], commit=True)

    db.expire_all()
    assert db.get(Vendor, linked.id) is not None


def test_merge_refuses_a_vendor_a_platform_sells_through(db: Session) -> None:
    """A merge deletes its source, so a linked source is refused the same way."""
    linked = _vendor(db, "whatnot.example")
    target = _vendor(db, "whatnot.com")
    _platform(db, "whatnot", linked)
    moved = _order(db, linked, "W1")

    with pytest.raises(CleanupError, match="whatnot"):
        run(db, merges=[(linked.id, target.id)], kinds=[], deletes=[], commit=True)

    db.expire_all()
    assert db.get(Vendor, linked.id) is not None
    assert db.get_one(PurchaseOrder, moved.id).vendor_id == linked.id


def test_a_platform_linked_elsewhere_does_not_block_a_merge(db: Session) -> None:
    """Only the vendor being removed is checked; another's platform is no bar."""
    typo = _vendor(db, "hibid.co")
    real = _vendor(db, "hibid.com")
    _platform(db, "hibid", real)
    moved = _order(db, typo, "H1")

    run(db, merges=[(typo.id, real.id)], kinds=[], deletes=[], commit=True)

    db.expire_all()
    assert db.get(Vendor, typo.id) is None
    assert db.get_one(PurchaseOrder, moved.id).vendor_id == real.id


def test_kind_is_set_by_code(db: Session) -> None:
    vendor = _vendor(db, "whatnot.example")

    run(db, merges=[], kinds=[(vendor.id, "marketplace")], deletes=[], commit=True)

    db.expire_all()
    marketplace = db.scalars(
        select(VendorKind).where(VendorKind.code == "marketplace")
    ).one()
    assert db.get_one(Vendor, vendor.id).vendor_kind_id == marketplace.id


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
    assert db.get_one(Vendor, other.id).vendor_kind_id is None


def test_a_vendor_can_be_renamed(db: Session) -> None:
    """The survivor of a merge often needs the spelling neither row had."""
    vendor = _vendor(db, "builionsharks.com")

    report = run(
        db,
        merges=[],
        kinds=[],
        deletes=[],
        renames=[(vendor.id, "bullionsharks.com")],
        commit=True,
    )

    assert report.renamed == [("builionsharks.com", "bullionsharks.com")]
    db.expire_all()
    assert db.get_one(Vendor, vendor.id).name == "bullionsharks.com"


def test_a_rename_carries_the_host_and_url_with_it(db: Session) -> None:
    """A misspelt source misspells its host too, and the importer matches on host.

    `ampex.com` was really apmex.com: the row's host and URL repeated the
    typo, so a later purchase from the real site would have made a second
    vendor rather than matching this one.
    """
    vendor = Vendor(name="ampex.com", host="www.ampex.com", url="https://www.ampex.com")
    db.add(vendor)
    db.commit()

    run(
        db,
        merges=[],
        kinds=[],
        deletes=[],
        renames=[(vendor.id, "apmex.com")],
        commit=True,
    )

    db.expire_all()
    renamed = db.get_one(Vendor, vendor.id)
    assert renamed.name == "apmex.com"
    assert renamed.host == "www.apmex.com"
    assert renamed.url == "https://www.apmex.com"


def test_a_rename_leaves_an_unrelated_host_and_url_alone(db: Session) -> None:
    """Only text that repeated the old name is rewritten."""
    vendor = Vendor(
        name="the-coin-shop",
        host="shop.example.com",
        url="https://shop.example.com/store",
    )
    db.add(vendor)
    db.commit()

    run(
        db,
        merges=[],
        kinds=[],
        deletes=[],
        renames=[(vendor.id, "coin-shop")],
        commit=True,
    )

    db.expire_all()
    renamed = db.get_one(Vendor, vendor.id)
    assert renamed.name == "coin-shop"
    assert renamed.host == "shop.example.com"
    assert renamed.url == "https://shop.example.com/store"


def test_a_rename_to_an_existing_name_is_refused(db: Session) -> None:
    """`uq_vendor_name` would fail at flush; the refusal names both."""
    vendor = _vendor(db, "one.example")
    _vendor(db, "Two.Example")

    with pytest.raises(CleanupError, match=r"Two\.Example"):
        run(
            db,
            merges=[],
            kinds=[],
            deletes=[],
            renames=[(vendor.id, "two.example")],
            commit=True,
        )

    db.expire_all()
    assert db.get_one(Vendor, vendor.id).name == "one.example"


def test_a_rename_happens_after_a_merge(db: Session) -> None:
    """The merged-away row's name is free to reuse in the same call."""
    typo = _vendor(db, "bullionshark.com")
    keep = _vendor(db, "builionsharks.com")
    _order(db, typo, "B1")

    run(
        db,
        merges=[(typo.id, keep.id)],
        kinds=[],
        deletes=[],
        renames=[(keep.id, "bullionsharks.com")],
        commit=True,
    )

    db.expire_all()
    assert db.get(Vendor, typo.id) is None
    assert db.get_one(Vendor, keep.id).name == "bullionsharks.com"
    assert _order_count_for(db, keep.id) == 1


def _order_count_for(db: Session, vendor_id: int) -> int:
    """Orders attached to a vendor, read straight from the database."""
    return db.execute(
        select(func.count()).where(PurchaseOrder.vendor_id == vendor_id)
    ).scalar_one()
