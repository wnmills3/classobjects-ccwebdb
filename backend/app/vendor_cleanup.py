"""Tidy the purchase-source list before sales platforms link to it.

The importer made one vendor per spelling it met, so the list holds typos
(`builionsharks.com`, `usming.gov`), two HiBid hosts, a vendor named `.`, and
no kind on anything. Sales platforms link to a vendor (selling design), so the
list is cleaned first.

Every change is named on the command line -- which vendors are the same
business is the owner's call, not something to guess -- and nothing is
written without --commit:

    python -m app.vendor_cleanup --merge 15:12 --merge 21:9 --delete 6
        --kind 1:marketplace --kind 19:marketplace
        --rename 12:bullionsharks.com [--commit]

A merge moves the purchase orders and removes the duplicate. Vendor rows hold
no history of their own; the orders carry it, and they are kept.

Renames run last, after the merges, so the survivor of a merge can take a
spelling neither row had -- or one the merged-away row was using.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import PurchaseOrder, SalesVenue, Vendor, VendorKind


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
    #: (old name, new name)
    renamed: list[tuple[str, str]] = field(default_factory=list)


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


def _refuse_if_a_platform_sells_through(db: Session, vendor: Vendor) -> None:
    """Refuse to remove a vendor a sales platform is linked to.

    `sales_venue.vendor_id` is ON DELETE RESTRICT and Vendor has no
    relationship back to SalesVenue, so without this check the DELETE reaches
    PostgreSQL and comes back as a raw IntegrityError -- a traceback where the
    operator should see a REFUSED line naming what to unlink.
    """
    code = db.scalar(select(SalesVenue.code).where(SalesVenue.vendor_id == vendor.id))
    if code is not None:
        raise CleanupError(
            f"{vendor.name} is the purchase source for platform {code}; unlink it first"
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
    *,
    commit: bool,
    renames: Sequence[tuple[int, str]] = (),
) -> Report:
    """Apply the named merges, deletions, kinds and renames.

    Renames run last, so a merge in the same call frees the name it removes:
    the two Bullion Shark spellings become one row named for the website the
    owner actually bought from. Rolls back unless `commit`.

    `commit` is keyword-only, as it is on every other pass here. Positional,
    it sat between two sequences, where passing `renames` one argument early
    made it the commit flag -- a non-empty list being truthy, that is a
    silent write instead of a refusal.
    """
    report = Report()
    try:
        for source_id, target_id in merges:
            source, target = _vendor(db, source_id), _vendor(db, target_id)
            if source_id == target_id:
                raise CleanupError(f"Cannot merge {source.name} into itself")
            # The source is deleted at the end of this merge, so it faces the
            # same FK as a plain --delete does. Checked before anything moves.
            _refuse_if_a_platform_sells_through(db, source)
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
            _refuse_if_a_platform_sells_through(db, vendor)
            report.deleted.append(vendor.name)
            db.delete(vendor)

        for vendor_id, code in kinds:
            vendor = _vendor(db, vendor_id)
            kind_id = db.scalar(select(VendorKind.id).where(VendorKind.code == code))
            if kind_id is None:
                raise CleanupError(f"Unknown vendor_kind {code!r}")
            vendor.vendor_kind_id = kind_id
            report.kinds.append((vendor.name, code))

        for vendor_id, new_name in renames:
            vendor = _vendor(db, vendor_id)
            # `uq_vendor_name` is case-sensitive, but two vendors differing
            # only in case are the same source to a person, so the check here
            # is case-insensitive -- the same rule the vendors API applies.
            taken = db.scalar(
                select(Vendor).where(
                    func.lower(Vendor.name) == new_name.casefold(),
                    Vendor.id != vendor_id,
                )
            )
            if taken is not None:
                raise CleanupError(
                    f"Cannot rename {vendor.name} to {new_name}: "
                    f"{taken.name} already has that name"
                )
            old_name = vendor.name
            report.renamed.append((old_name, new_name))
            vendor.name = new_name
            # A misspelt source misspells its web address too (`ampex.com` for
            # apmex.com), and the importer matches a vendor by `host`, so a
            # name fixed on its own would let the real site arrive as a second
            # vendor. Only text that repeated the old name is rewritten;
            # anything else is the source's address, not the typo.
            if vendor.host:
                vendor.host = vendor.host.replace(old_name, new_name)
            if vendor.url:
                vendor.url = vendor.url.replace(old_name, new_name)

        db.flush()
    except Exception:
        # Every exception, not only CleanupError. A database constraint this
        # pass has not learned to check for surfaces as an IntegrityError at
        # flush, and leaving that session un-rolled-back would strand the
        # caller in a failed transaction while earlier writes look applied.
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


def _rename(text: str) -> tuple[int, str]:
    left, _, right = text.partition(":")
    return int(left), right.strip()


def main(argv: list[str] | None = None) -> int:
    """Report or apply the clean-up."""
    parser = argparse.ArgumentParser(prog="vendor_cleanup", description=__doc__)
    parser.add_argument("--merge", type=_pair, action="append", default=[])
    parser.add_argument("--kind", type=_kind, action="append", default=[])
    parser.add_argument("--delete", type=int, action="append", default=[])
    parser.add_argument(
        "--rename",
        type=_rename,
        action="append",
        default=[],
        help="ID:NEW_NAME, applied after the merges",
    )
    parser.add_argument("--commit", action="store_true", help="write the changes")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        try:
            report = run(
                db,
                args.merge,
                args.kind,
                args.delete,
                commit=args.commit,
                renames=args.rename,
            )
        except CleanupError as exc:
            print(f"REFUSED: {exc}")
            return 1
    for source, target, moved in report.merged:
        print(f"merge  {source} -> {target}  ({moved} orders)")
    for name in report.deleted:
        print(f"delete {name}")
    for name, code in report.kinds:
        print(f"kind   {name} = {code}")
    for old, new in report.renamed:
        print(f"rename {old} -> {new}")
    if not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
