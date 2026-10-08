"""Order numbers and listing ids for eBay purchases, from eBay's purchase history.

An eBay purchase recorded without an order number cannot be found or
checked against eBay. The purchase
history -- workbooks from the "eBay Purchase History Downloader" Chrome
extension, one per year, a row per line bought (OrderNumber, OrderDate,
ItemID, Seller, ItemName, ItemPrice, ...) -- supplies both the order number
and eBay's item id.

**The item id is the key, not the words.** Every one of those purchases links
its eBay listing (`https://www.ebay.com/itm/<item id>`), and so do most items
(`listing_url`); the history has the item id on every line. A stored number
that disagrees with the history's is listed for a person, never changed
here: it may be a typo, or name another order of the same day.

What the pass does, all in one transaction:

1. **`sellers_item_id`** on every item whose own link, or failing that its
   purchase's, names an eBay listing. Only where it is empty.
2. **Order numbers.** An eBay purchase with none takes the one order its items'
   ids appear in (a line of that date is preferred when an id was bought
   twice). No order, or more than one, is listed for a person.
3. **One purchase per eBay order.** An order of several listings was recorded
   as a purchase per listing; the database allows one purchase per vendor's
   order number, and an eBay order is one purchase. So the purchases an order
   was split into are merged: their items move to one (the purchase already
   holding the number, else the oldest), the emptied ones are deleted, and each
   item keeps its own listing's id. A merged purchase's link becomes the
   order's page on eBay; a deleted purchase's notes are appended to the
   survivor's, and its seller goes to a survivor that names none.

A vendor is eBay by its host name (`is_ebay`); with none recorded the pass
refuses rather than report nothing to do. Only live items are read.

Every item whose order number or listing id changes gets a row in its History
(`item_field_change`) under the person named by `--by`. Dry run by default;
`--commit` writes. `--review` writes a workbook of what was done and of
everything left for a person.

    python -m app.ebay_orders FILE... [--commit --by EMAIL] [--review OUT.xlsx]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import field_changes, pass_cli
from .cell_text import keep_text
from .database import SessionLocal
from .live import live_item
from .models import InventoryItem, PurchaseOrder, Seller, Vendor

__all__ = [
    "ITEM_ID",
    "Line",
    "Plan",
    "apply",
    "is_ebay",
    "item_id_of",
    "plan",
    "read_history",
    "vendor_key",
]

#: The name eBay purchases are usually recorded under. A vendor is eBay by
#: `is_ebay`, whatever it is called.
EBAY = "ebay.com"
#: eBay's own host name, whatever the country: `ebay.com`, `www.ebay.co.uk`.
#: The whole label, so a site whose name only contains the letters is not
#: eBay.
_EBAY_HOST = re.compile(r"(?:^|\.)ebay\.[a-z]{2,3}(?:\.[a-z]{2})?$")
#: eBay's listing link: `/itm/<id>`, or `/itm/<title>/<id>` in older links.
#: `app.listing_links` reads the same pattern.
ITEM_ID = re.compile(r"ebay\.[a-z.]+/itm/(?:[^/?#]*/)?(\d{9,15})", re.IGNORECASE)
#: An order's page on eBay, as a merged purchase links it.
ORDER_PAGE = "https://order.ebay.com/ord/show?orderId={}"
#: The history's columns this pass reads.
_COLUMNS = ("OrderNumber", "OrderDate", "ItemID", "Seller", "ItemName", "ItemPrice")


@dataclass(frozen=True)
class Line:
    """One line of eBay's purchase history: an item bought on an order."""

    order_number: str
    ordered_on: date
    item_id: str
    seller: str
    name: str
    price: Decimal | None


def item_id_of(url: str | None) -> str | None:
    """The eBay item id in a listing link, or None for any other link."""
    match = ITEM_ID.search(url or "")
    return match.group(1) if match else None


def vendor_key(vendor: Vendor) -> str:
    """What a vendor is recognised by: its host, else its name, in lower case."""
    return (vendor.host or vendor.name or "").strip().lower()


def is_ebay(vendor: Vendor) -> bool:
    """Whether the vendor is eBay: called `ebay`, or at one of eBay's hosts.

    The one rule this pass and `app.listing_links` share, so a vendor
    renamed in the console is eBay to both or to neither.
    """
    key = vendor_key(vendor)
    return key == "ebay" or _EBAY_HOST.search(key) is not None


def _ebay_vendor_ids(db: Session) -> list[int]:
    """The ids of the vendors that are eBay."""
    return [vendor.id for vendor in db.scalars(select(Vendor)) if is_ebay(vendor)]


def _price(value: object) -> Decimal | None:
    """A price as eBay writes it, commas dropped; None when empty or not a number."""
    try:
        return Decimal(str(value).replace(",", "")) if value not in (None, "") else None
    except InvalidOperation:
        return None


def read_history(paths: Iterable[Path]) -> list[Line]:
    """Every line of the purchase-history workbooks, first sheet of each."""
    lines: list[Line] = []
    for path in paths:
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            rows = book.worksheets[0].iter_rows(values_only=True)
            header = [str(h) if h is not None else "" for h in next(rows, ())]
            missing = [c for c in _COLUMNS if c not in header]
            if missing:
                raise ValueError(f"{path.name}: no column {', '.join(missing)}")
            at = {name: header.index(name) for name in _COLUMNS}
            for values in rows:
                if not values or not values[at["OrderNumber"]]:
                    continue
                lines.append(
                    Line(
                        order_number=str(values[at["OrderNumber"]]).strip(),
                        ordered_on=datetime.strptime(
                            str(values[at["OrderDate"]]).strip(), "%b %d, %Y"
                        ).date(),
                        item_id=str(values[at["ItemID"]]).strip(),
                        seller=str(values[at["Seller"]] or ""),
                        name=str(values[at["ItemName"]] or ""),
                        price=_price(values[at["ItemPrice"]]),
                    )
                )
        finally:
            book.close()
    return lines


@dataclass
class Plan:
    """What the pass would do, and what it leaves for a person."""

    #: item id -> listing id to set.
    listing_ids: dict[int, str] = field(default_factory=dict)
    #: order number -> (surviving purchase id, purchases merged into it).
    orders: dict[str, tuple[int, list[int]]] = field(default_factory=dict)
    #: order number -> its date in the history.
    order_dates: dict[str, date] = field(default_factory=dict)
    #: (purchase id, why) for purchases left without a number.
    unmatched: list[tuple[int, str]] = field(default_factory=list)
    #: (purchase id, stored number, the history's number) where they differ.
    disagreements: list[tuple[int, str, str]] = field(default_factory=list)


def _orders_for(
    ids: set[str], on: date | None, by_item: dict[str, list[Line]]
) -> set[str]:
    """The orders these listing ids were bought on; one of `on`'s date first."""
    lines = [line for i in ids for line in by_item.get(i, [])]
    dated = {line.order_number for line in lines if line.ordered_on == on}
    return dated or {line.order_number for line in lines}


def plan(db: Session, lines: Sequence[Line]) -> Plan:
    """Decide every change from the history, writing nothing."""
    by_item: dict[str, list[Line]] = defaultdict(list)
    for line in lines:
        by_item[line.item_id].append(line)
    out = Plan()
    for line in lines:
        out.order_dates.setdefault(line.order_number, line.ordered_on)

    vendor_ids = _ebay_vendor_ids(db)
    if not vendor_ids:
        return out
    purchases = db.scalars(
        select(PurchaseOrder).where(PurchaseOrder.vendor_id.in_(vendor_ids))
    ).all()
    items_of = _live_items_by_purchase(db, [p.id for p in purchases])

    holder = {p.order_number: p.id for p in purchases if p.order_number}
    groups: dict[str, list[int]] = defaultdict(list)
    for purchase in sorted(purchases, key=lambda p: p.id):
        ids = _listing_ids_of(purchase, items_of[purchase.id], out)
        found = _orders_for(ids, purchase.ordered_on, by_item) if ids else set()
        _place(purchase, ids, found, out, groups)

    for number, members in groups.items():
        survivor = holder.get(number, min(members))
        out.orders[number] = (survivor, sorted(m for m in members if m != survivor))
    return out


def _live_items_by_purchase(
    db: Session, purchase_ids: Sequence[int]
) -> dict[int, list[InventoryItem]]:
    """These purchases' live items, by purchase id.

    Live items only: a deleted row, or a lot replaced by its pieces, may
    link a listing of another order and says nothing about this one.
    """
    items_of: dict[int, list[InventoryItem]] = defaultdict(list)
    for item in db.scalars(
        select(InventoryItem).where(
            InventoryItem.purchase_order_id.in_(purchase_ids),
            live_item(),
        )
    ):
        if item.purchase_order_id is not None:
            items_of[item.purchase_order_id].append(item)
    return items_of


def _listing_ids_of(
    purchase: PurchaseOrder, items: Sequence[InventoryItem], out: Plan
) -> set[str]:
    """The eBay listing ids a purchase and its items name.

    An item with no seller's item id of its own is planned to take the one
    read from its link, or failing that its purchase's.
    """
    own = item_id_of(purchase.source_url)
    ids: set[str] = set()
    for item in items:
        listing = item.sellers_item_id or item_id_of(item.listing_url) or own
        if not listing:
            continue
        ids.add(listing)
        if item.sellers_item_id is None:
            out.listing_ids[item.id] = listing
    if own:
        ids.add(own)
    return ids


def _place(
    purchase: PurchaseOrder,
    ids: set[str],
    found: set[str],
    out: Plan,
    groups: dict[str, list[int]],
) -> None:
    """File a purchase by the orders its listings were found in.

    One with a number is only checked against the history's; one without
    joins the group of its one order, or is listed for a person with why.
    """
    if purchase.order_number:
        if len(found) == 1 and purchase.order_number not in found:
            out.disagreements.append(
                (purchase.id, purchase.order_number, next(iter(found)))
            )
        return
    if not ids:
        out.unmatched.append((purchase.id, "no eBay listing link"))
    elif not found:
        out.unmatched.append((purchase.id, "listing not in the purchase history"))
    elif len(found) > 1:
        out.unmatched.append(
            (purchase.id, "in several orders: " + ", ".join(sorted(found)))
        )
    else:
        groups[next(iter(found))].append(purchase.id)


def _carry(survivor: PurchaseOrder, gone: PurchaseOrder) -> None:
    """Keep on the survivor what only the purchase about to be deleted recorded.

    Its notes are appended. Its seller goes to a survivor that names none; a
    survivor with a seller of its own keeps it -- a purchase has one -- and
    the review workbook names the other.
    """
    if survivor.seller_id is None:
        survivor.seller_id = gone.seller_id
    notes = (gone.notes or "").strip()
    if notes and notes not in (survivor.notes or ""):
        survivor.notes = f"{survivor.notes}\n{notes}" if survivor.notes else notes


def _move_items(
    db: Session,
    number: str,
    survivor_id: int,
    merged: Sequence[int],
    log: Callable[[int, str, object, object], None],
) -> int:
    """Point an order's items at its surviving purchase; how many moved.

    Each live item whose purchase did not already hold `number` has the
    change logged through `log` before it moves.
    """
    moved = 0
    members = [survivor_id, *merged]
    for item in db.scalars(
        select(InventoryItem).where(InventoryItem.purchase_order_id.in_(members))
    ):
        old = db.get(PurchaseOrder, item.purchase_order_id)
        assert old is not None
        # Every row moves with its purchase, or it would be orphaned;
        # only a live one has a History anybody reads.
        live = item.deleted_at is None and item.split_at is None
        if live and old.order_number != number:
            log(item.id, "order_number", old.order_number, number)
        if item.purchase_order_id != survivor_id:
            item.purchase_order_id = survivor_id
            moved += 1
    return moved


def _delete_merged(db: Session, survivor: PurchaseOrder, merged: Sequence[int]) -> int:
    """Delete the purchases merged into `survivor`; how many went.

    What only they recorded is carried over first (`_carry`), and one that
    still holds an item is an error rather than a deletion.
    """
    deleted = 0
    for purchase_id in merged:
        gone = db.get(PurchaseOrder, purchase_id)
        assert gone is not None
        _carry(survivor, gone)
        # Expired first: a loaded `items` list would still hold the
        # moved items, and deleting the purchase would then null their
        # purchase -- SQLAlchemy's default for a parent's children.
        db.expire(gone)
        left = db.scalar(
            select(InventoryItem.id)
            .where(InventoryItem.purchase_order_id == purchase_id)
            .limit(1)
        )
        if left is not None:
            raise RuntimeError(f"purchase {purchase_id} still holds item {left}")
        db.delete(gone)
        deleted += 1
    return deleted


def apply(db: Session, todo: Plan, user_id: int) -> dict[str, int]:
    """Make the planned changes and log each item's; the caller commits.

    Each item's change goes into its history through `field_changes.record`,
    that table's one writer, every row stamped with this run's start.
    """
    now = datetime.now(UTC)
    logged = 0

    def log(item_id: int, field_name: str, old: object, new: object) -> None:
        """Record one field's change in the item's history, and count it."""
        nonlocal logged
        logged += field_changes.record(
            db,
            item_id,
            {field_name: old},
            {field_name: new},
            [field_name],
            user_id=user_id,
            at=now,
            # Both fields this pass logs are identifiers, compared as text.
            text_fields=[field_name],
        )

    for item_id, listing in todo.listing_ids.items():
        item = db.get(InventoryItem, item_id)
        assert item is not None
        item.sellers_item_id = listing
        log(item_id, "sellers_item_id", None, listing)

    moved = deleted = numbered = 0
    for number, (survivor_id, merged) in todo.orders.items():
        survivor = db.get(PurchaseOrder, survivor_id)
        assert survivor is not None
        moved += _move_items(db, number, survivor_id, merged, log)
        if survivor.order_number != number:
            survivor.order_number = number
            numbered += 1
        if survivor.ordered_on is None:
            survivor.ordered_on = todo.order_dates.get(number)
        if merged:
            survivor.source_url = ORDER_PAGE.format(number)
            db.flush()  # the items point at the survivor before the rest go
            deleted += _delete_merged(db, survivor, merged)
    db.flush()
    return {
        "listing ids set": len(todo.listing_ids),
        "purchases numbered": numbered,
        "items moved": moved,
        "purchases merged away": deleted,
        "history rows": logged,
    }


def _append(sheet: Worksheet, row: Sequence[object]) -> None:
    """Add `row` below the sheet's last, every string in it held as text.

    The titles, seller names and notes in a review are as a seller or a
    buyer wrote them, and one that begins with `=` would otherwise be a
    formula for whoever opens the workbook (`app.cell_text`).
    """
    sheet.append(list(row))
    last = sheet.max_row
    for cells in sheet.iter_rows(min_row=last, max_row=last):
        for cell in cells:
            if isinstance(cell, Cell):
                keep_text(cell)


def _purchase_row(db: Session, purchase_id: int) -> list[object]:
    """The review sheet's cells for one purchase: its date, address and items."""
    purchase = db.get(PurchaseOrder, purchase_id)
    items = db.scalars(
        select(InventoryItem)
        .where(InventoryItem.purchase_order_id == purchase_id)
        .order_by(InventoryItem.id)
    ).all()
    return [
        purchase_id,
        purchase.ordered_on if purchase else None,
        purchase.source_url if purchase else None,
        ", ".join(i.item_code for i in items),
        " | ".join(i.source_title for i in items)[:300],
    ]


def _merged_away(
    db: Session, survivor_id: int, merged: Sequence[int]
) -> tuple[list[str], list[str]]:
    """What the purchases merged into `survivor_id` hold that it does not.

    The names of their sellers other than the survivor's own -- one of
    which the merge can keep only where the survivor names none -- and
    their notes, which it appends.
    """
    kept = db.get(PurchaseOrder, survivor_id)
    sellers: list[str] = []
    notes: list[str] = []
    for purchase_id in merged:
        gone = db.get(PurchaseOrder, purchase_id)
        if gone is None:
            continue
        if gone.seller_id is not None and (
            kept is None or gone.seller_id != kept.seller_id
        ):
            name = db.get_one(Seller, gone.seller_id).name
            if name not in sellers:
                sellers.append(name)
        if gone.notes and gone.notes.strip():
            notes.append(gone.notes.strip())
    return sellers, notes


def write_review(db: Session, todo: Plan, lines: Sequence[Line], path: Path) -> None:
    """A workbook of what was done and of what is left for a person."""
    by_order: dict[str, list[Line]] = defaultdict(list)
    for line in lines:
        by_order[line.order_number].append(line)

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Numbered"
    _append(
        sheet,
        [
            "order number",
            "purchase",
            "merged from",
            "eBay date",
            "eBay items",
            "other sellers on the merged purchases",
            "notes carried from them",
        ],
    )
    for number, (survivor, merged) in sorted(todo.orders.items()):
        sellers, notes = _merged_away(db, survivor, merged)
        _append(
            sheet,
            [
                number,
                survivor,
                ", ".join(map(str, merged)),
                todo.order_dates.get(number),
                " | ".join(line.name for line in by_order[number])[:300],
                ", ".join(sellers),
                " | ".join(notes),
            ],
        )
    left = book.create_sheet("Needs you")
    _append(left, ["purchase", "date", "link", "items", "titles", "why"])
    for purchase_id, why in todo.unmatched:
        _append(left, [*_purchase_row(db, purchase_id), why])
    differ = book.create_sheet("Numbers that disagree")
    _append(
        differ,
        [
            "purchase",
            "date",
            "link",
            "items",
            "titles",
            "stored number",
            "eBay's number for its listing",
            "eBay's items on that order",
            "the stored number on eBay is",
        ],
    )
    for purchase_id, stored, found in todo.disagreements:
        elsewhere = by_order.get(stored)
        _append(
            differ,
            [
                *_purchase_row(db, purchase_id),
                stored,
                found,
                " | ".join(line.name for line in by_order[found])[:300],
                " | ".join(line.name for line in elsewhere)[:300]
                if elsewhere
                else "not in the history (a typo?)",
            ],
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)


def main(argv: Sequence[str] | None = None, *, db: Session | None = None) -> int:
    """Report, or with --commit apply, the order numbers and listing ids.

    `db` is the session to work in; run as a module, the application's own
    `SessionLocal` is opened. A caller that already has a session -- the
    tests, which must never let this open the live database -- passes it.
    """
    parser = argparse.ArgumentParser(prog="ebay_orders", description=__doc__)
    parser.add_argument(
        "files", nargs="+", type=Path, help="purchase-history workbooks"
    )
    pass_cli.add_commit_arguments(parser, "write the changes")
    parser.add_argument("--review", type=Path, help="write a review workbook here")
    args = pass_cli.parse_args(parser, argv)

    lines = read_history(args.files)
    if db is None:
        with SessionLocal() as own:
            return _run(own, args, lines)
    return _run(db, args, lines)


def _run(db: Session, args: argparse.Namespace, lines: Sequence[Line]) -> int:
    """The report, and the changes when asked for, in one session.

    Exit status: 0 for a dry run or a commit, 1 when no vendor is eBay, 2
    when `--by` names no account.
    """
    # Said, not left to read as a report of nothing to do: with no eBay
    # vendor every count below would be zero.
    if not _ebay_vendor_ids(db):
        print(
            "refused: no eBay vendor is recorded (one named ebay, or whose "
            "host or name is an eBay host such as ebay.com)",
            file=sys.stderr,
        )
        return 1
    todo = plan(db, lines)
    merged = sum(len(m) for _, m in todo.orders.values())
    orders = len({x.order_number for x in lines})
    print(f"history: {len(lines)} lines, {orders} orders")
    print(f"listing ids to set: {len(todo.listing_ids)}")
    print(f"orders to number: {len(todo.orders)}, merging {merged} purchases")
    print(f"left for a person: {len(todo.unmatched)}")
    print(f"stored numbers that disagree: {len(todo.disagreements)}")
    if args.review:
        write_review(db, todo, lines, args.review)
        print(f"review workbook: {args.review}")
    if not args.commit:
        print(pass_cli.DRY_RUN)
        return 0
    user_id = pass_cli.user_id_by_email(db, args.by)
    if user_id is None:
        print(f"refused: no account {args.by}", file=sys.stderr)
        return 2
    for name, count in apply(db, todo, user_id).items():
        print(f"  {name}: {count}")
    db.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
