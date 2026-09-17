"""Tidy the purchase-source list before sales platforms link to it.

The importer made one vendor per spelling it met, so the list holds typos
(`builionsharks.com`, `usming.gov`), two HiBid hosts, a vendor named `.`, and
no kind on anything. Sales platforms link to a vendor (selling design), so the
list is cleaned first.

Every change is named on the command line -- which vendors are the same
business is the owner's call, not something to guess -- and nothing is
written without --commit:

    python -m app.vendor_cleanup --merge 15:12 --merge 21:9 --delete 6
        --kind 1:marketplace --kind 19:marketplace [--commit]

A merge moves the purchase orders and removes the duplicate. Vendor rows hold
no history of their own; the orders carry it, and they are kept.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import PurchaseOrder, Vendor, VendorKind


class CleanupError(Exception):
    """A requested change that cannot be made; nothing was written."""


@dataclass
class Report:
    """What the pass did, or would do."""

    #: (from name, into name, orders moved)
    merged: list[tuple[str, str, int]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    #: (vendor name, kind code)
    kinds: list[tuple[str, str]] = field(default_factory=list)


def _vendor(db: Session, vendor_id: int) -> Vendor:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise CleanupError(f"No vendor with id {vendor_id}")
    return vendor


def _order_count(db: Session, vendor_id: int) -> int:
    """Count of purchase orders currently attached to a vendor."""
    return (
        db.scalar(select(func.count()).where(PurchaseOrder.vendor_id == vendor_id)) or 0
    )


def _clashing_numbers(db: Session, left: int, right: int) -> list[str]:
    """Order numbers both vendors use, which a merge would duplicate."""
    left_numbers = select(PurchaseOrder.order_number).where(
        PurchaseOrder.vendor_id == left, PurchaseOrder.order_number.is_not(None)
    )
    # The IN-subquery restricts matches to left's non-null numbers, so no row
    # here can actually have a null order_number -- but the column itself is
    # nullable, so the filter is spelled out for the type checker too.
    right_numbers = db.scalars(
        select(PurchaseOrder.order_number).where(
            PurchaseOrder.vendor_id == right,
            PurchaseOrder.order_number.in_(left_numbers),
        )
    )
    return sorted(number for number in right_numbers if number is not None)


def run(
    db: Session,
    merges: Sequence[tuple[int, int]],
    kinds: Sequence[tuple[int, str]],
    deletes: Sequence[int],
    commit: bool,
) -> Report:
    """Apply the named merges, deletions and kinds; roll back unless `commit`."""
    report = Report()
    try:
        for source_id, target_id in merges:
            source, target = _vendor(db, source_id), _vendor(db, target_id)
            if source_id == target_id:
                raise CleanupError(f"Cannot merge {source.name} into itself")
            clashes = _clashing_numbers(db, source_id, target_id)
            if clashes:
                raise CleanupError(
                    f"{source.name} and {target.name} both have order number(s) "
                    f"{', '.join(clashes)}; resolve those first"
                )
            moved = _order_count(db, source_id)
            db.execute(
                update(PurchaseOrder)
                .where(PurchaseOrder.vendor_id == source_id)
                .values(vendor_id=target_id)
            )
            report.merged.append((source.name, target.name, moved))
            db.delete(source)

        for vendor_id in deletes:
            vendor = _vendor(db, vendor_id)
            if _order_count(db, vendor_id):
                raise CleanupError(
                    f"{vendor.name} still has purchase orders; merge it instead"
                )
            report.deleted.append(vendor.name)
            db.delete(vendor)

        for vendor_id, code in kinds:
            vendor = _vendor(db, vendor_id)
            kind_id = db.scalar(select(VendorKind.id).where(VendorKind.code == code))
            if kind_id is None:
                raise CleanupError(f"Unknown vendor_kind {code!r}")
            vendor.vendor_kind_id = kind_id
            report.kinds.append((vendor.name, code))

        db.flush()
    except CleanupError:
        db.rollback()
        raise
    if commit:
        db.commit()
    else:
        db.rollback()
    return report


def _pair(text: str) -> tuple[int, int]:
    left, _, right = text.partition(":")
    return int(left), int(right)


def _kind(text: str) -> tuple[int, str]:
    left, _, right = text.partition(":")
    return int(left), right


def main(argv: list[str] | None = None) -> int:
    """Report or apply the clean-up."""
    parser = argparse.ArgumentParser(prog="vendor_cleanup", description=__doc__)
    parser.add_argument("--merge", type=_pair, action="append", default=[])
    parser.add_argument("--kind", type=_kind, action="append", default=[])
    parser.add_argument("--delete", type=int, action="append", default=[])
    parser.add_argument("--commit", action="store_true", help="write the changes")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        try:
            report = run(db, args.merge, args.kind, args.delete, commit=args.commit)
        except CleanupError as exc:
            print(f"REFUSED: {exc}")
            return 1
    for source, target, moved in report.merged:
        print(f"merge  {source} -> {target}  ({moved} orders)")
    for name in report.deleted:
        print(f"delete {name}")
    for name, code in report.kinds:
        print(f"kind   {name} = {code}")
    if not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
