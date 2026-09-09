"""Split the fabricated purchase orders back into real ones.

`loader.purchase_order_id` keys on `(vendor_id, order_number or "")`, so every
row whose order number was blank collapsed into a single order per vendor. The
result is twelve orders that never existed, holding 2,644 items and $224,372.20
-- 42% of the cost basis. `purchase_order` 114 alone holds 1,922 eBay purchases
spanning two years, dated by whichever row loaded first.

The bug is treating *unknown* as a value: twelve "I do not know the order
number" became twelve assertions that these were all one order.

Most of those rows do carry a real identifier, in the vendor's own URL:

    hibid.com/lot/226778844/...              the auction house's lot id
    liveauctioneers.com/item/194045321_...   the lot id, before the underscore
    proxibid.com/lotinformation/91896928/... the lot id
    etsy.com/your/purchases/3458000561       a purchase receipt id

Each identifies one transaction with that vendor, which is what an order
number is for, so each becomes its own `purchase_order`.

**eBay is deliberately different.** `ebay.com/itm/306947694169` is an *item*
number: it names a listing, not a purchase, and two separate purchases of the
same listing share it. Putting it in `order_number` would invite someone to
look it up as an order and find something else. Those rows get their own order
row with `order_number` left null and the link in `source_url`, which is what
that column is for.

Anything with no usable identifier keeps no order at all. A row that cannot
say which purchase it belonged to should not claim one, and grouping by date
would repeat the original mistake in a smaller way.

    python -m app.order_repair            report, touching nothing
    python -m app.order_repair --commit   apply
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .importers.models import ImportRow
from .models import InventoryItem, PurchaseOrder


@dataclass(frozen=True)
class Source:
    """How one venue writes its transaction id into a URL."""

    pattern: re.Pattern[str]
    #: False where the id names a listing rather than a purchase, so it must
    #: not masquerade as an order number.
    is_order_number: bool = True


SOURCES: tuple[Source, ...] = (
    Source(re.compile(r"/lot/(\d{6,})")),
    Source(re.compile(r"/item/(\d+)_")),
    Source(re.compile(r"/lotinformation/(\d+)")),
    Source(re.compile(r"/your/purchases/(\d+)")),
    # eBay: a listing, not a purchase. Kept for grouping, never as a number.
    Source(re.compile(r"/itm/(\d{9,})"), is_order_number=False),
)


def identify(link: str | None) -> tuple[str, bool] | None:
    """The venue's own id for this transaction, and whether it is an order."""
    if not link:
        return None
    for source in SOURCES:
        found = source.pattern.search(link)
        if found:
            return found.group(1), source.is_order_number
    return None


def run(db: Session, *, commit: bool) -> Counter:
    """Move every row off a fabricated order onto one that reflects reality."""
    fabricated = {
        order_id
        for (order_id,) in db.execute(
            select(PurchaseOrder.id).where(
                (PurchaseOrder.order_number.is_(None))
                | (PurchaseOrder.order_number == "")
            )
        )
    }
    if not fabricated:
        return Counter()

    rows = db.execute(
        select(
            InventoryItem.id,
            InventoryItem.purchase_order_id,
            PurchaseOrder.vendor_id,
            ImportRow.raw,
        )
        .join(PurchaseOrder, PurchaseOrder.id == InventoryItem.purchase_order_id)
        .join(ImportRow, ImportRow.inventory_item_id == InventoryItem.id)
        .where(InventoryItem.purchase_order_id.in_(fabricated))
    ).all()

    stats: Counter = Counter()
    # (vendor, identifier) -> the order row standing for that transaction.
    created: dict[tuple[int | None, str], int] = {}
    moves: list[tuple[int, int | None]] = []

    for item_id, _order_id, vendor_id, raw in rows:
        link = (raw or {}).get("Link") if isinstance(raw, dict) else None
        found = identify(link)
        if found is None:
            stats["no_identifier"] += 1
            moves.append((item_id, None))
            continue

        identifier, is_order_number = found
        key = (vendor_id, identifier)
        stats["order_number" if is_order_number else "listing_only"] += 1

        if key not in created:
            if commit:
                order = PurchaseOrder(
                    vendor_id=vendor_id,
                    order_number=identifier if is_order_number else None,
                    source_url=link,
                )
                db.add(order)
                db.flush()
                created[key] = order.id
            else:
                created[key] = -len(created) - 1
        moves.append((item_id, created[key]))

    stats["orders_created"] = len(created)

    if commit:
        for item_id, order_id in moves:
            item = db.get(InventoryItem, item_id)
            if item is not None:
                item.purchase_order_id = order_id
        db.flush()
        # The fabricated rows are now empty and must not survive: leaving them
        # would keep twelve orders that never existed in every vendor report.
        for order_id in fabricated:
            remaining = db.scalar(
                select(InventoryItem.id)
                .where(InventoryItem.purchase_order_id == order_id)
                .limit(1)
            )
            if remaining is None:
                order = db.get(PurchaseOrder, order_id)
                if order is not None:
                    db.delete(order)
                    stats["fabricated_removed"] += 1
        db.commit()

    return stats


def main(argv: list[str] | None = None) -> None:
    """Report or apply the repair."""
    parser = argparse.ArgumentParser(prog="order_repair", description=__doc__)
    parser.add_argument("--commit", action="store_true", help="apply the repair")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        stats = run(db, commit=args.commit)

    if not stats:
        print("no fabricated orders found")
        return
    print(f"{'rows with a vendor order id':<34}{stats['order_number']:>7}")
    print(f"{'rows with a listing id only (eBay)':<34}{stats['listing_only']:>7}")
    print(f"{'rows with no identifier -> no order':<34}{stats['no_identifier']:>7}")
    print(f"{'real orders created':<34}{stats['orders_created']:>7}")
    if stats.get("fabricated_removed"):
        print(f"{'fabricated orders removed':<34}{stats['fabricated_removed']:>7}")
    if not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")


if __name__ == "__main__":
    main()
