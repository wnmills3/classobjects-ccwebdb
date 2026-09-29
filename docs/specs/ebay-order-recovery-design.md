# eBay order numbers and listing ids

*2026-09-29.* An eBay purchase is found, and checked against eBay, by its
order number; an item bought on eBay is traced to the listing it came from
by eBay's item id. Purchases recorded without an order number cannot be
found either way. `python -m app.ebay_orders` fills both in from eBay's own
purchase history: a command-line pass the owner or an administrator runs
from `backend\` after downloading that history. It fills only what is
empty, so it can be run again after each new download.

The result shows in the management console: the item editor's
**Seller's item id** field (editable) with a link to the listing on eBay,
the `sellers_item_id` filter in both inventory searches, and each change in
the item's History.

## Source

eBay's purchase history, saved by the Chrome extension **eBay Purchase History
Downloader** (README, *Useful tools*): one workbook per year
(`Ebay_Purchase_History_<year>.xlsx`), first sheet, a row per item bought:

| Column | Used for |
|---|---|
| `OrderNumber` | the order number filled in |
| `OrderDate` | `Mon DD, YYYY`; breaks a tie when one listing was bought twice, and fills an empty order date |
| `ItemID` | **the key**: eBay's item number for the listing |
| `Seller`, `ItemName`, `ItemPrice` | shown in the review workbook |

A workbook missing any of these columns is refused by name.

## The key is the item id

Every eBay purchase links its listing (`https://www.ebay.com/itm/<item id>`),
and most items do too (`inventory_item.listing_url`), so the match is exact,
not by words, date or price. Measured against eBay purchases that already
had a number, the item id gave back the stored number for 1,680 of 1,738;
of the 58 that disagree, some are typos in the stored number and the rest
name another order of the same day. Either side could be wrong, so a
disagreement is reported and never changed.

## One purchase per eBay order

An eBay order of several listings may have been recorded as a purchase per
listing. `purchase_order` allows one purchase per vendor's order number
(`uq_purchase_order_vendor_number`), and an eBay order is one purchase, so
those purchases are **merged**: their items move to one -- the purchase
already holding the number, else the oldest (lowest id) -- and the emptied
ones are deleted. Nothing but `inventory_item` points at a purchase. Each
item keeps its own listing's id in `sellers_item_id`; the merged purchase's
link becomes the order's page on eBay.

Deleting a purchase whose loaded `items` list still held the moved items
would null their purchase -- SQLAlchemy's default for a parent's children --
so the pass expires each purchase before deleting it and raises if any item
still points at it. A test loads that list, and fails with the expiry
removed.

## `sellers_item_id`

`inventory_item.sellers_item_id` (text, indexed; migration `9a4c2e7f5b18`):
the seller's id for the listing the item was bought from. The pass fills it
from the item's own listing link, else its purchase's, only where empty; a
lot's pieces share it. **It is not unique and does not name an order**: a
seller lists many of one coin under one id, and it is bought in several
orders -- CC-000684 and CC-000685 share `124766588249` across orders
`16-11696-17632` and `13-11699-25451`. So its index is plain, and the pass,
meeting an id bought more than once, prefers the order dated as the purchase
is and leaves any remaining tie for a person.

## The pass

```cmd
cd backend
python -m app.ebay_orders FILE... [--review OUT.xlsx] [--commit --by EMAIL]
```

**Dry run by default**: it prints what it would do and writes nothing.
`--commit` writes, in one transaction, and requires `--by`, the email of the
account the History rows name (an unknown account is refused, exit 2).

1. `plan` decides everything, writing nothing: listing ids to set, the order
   for each unnumbered eBay purchase (the one order its listings appear in,
   a line of the purchase's own date preferred), the purchases each order
   merges, and what is left for a person -- no eBay listing link, a listing
   not in the history, or listings in several orders.
2. `apply` makes those changes, fills an empty order date from the history,
   and records each item's `order_number` and `sellers_item_id` change
   through `field_changes.record`, so each shows in the item's History.
3. `--review` writes a workbook with three sheets: **Numbered**, **Needs
   you**, and **Numbers that disagree** (the stored number, eBay's number for
   its listing, and what each order held -- or "not in the history (a
   typo?)").

## Settling a number that disagrees

eBay's line price includes tax, as our `total_cost` does, so the order whose
price matches ours to the cent is the purchase's. A live-show description's
opening lot number ("#134 - E - 05/17/26") names the lot, so a description
naming another lot is a row filed or copied onto the wrong purchase.
