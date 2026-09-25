# eBay order numbers and listing ids

2026-09-25. The owner's request: 1,234 eBay purchases were recorded without an
order number, so they could not be found by one or checked against eBay; and
each item should carry eBay's own item id (`sellers_item_id`).

## Source

eBay's purchase history, saved by the Chrome extension **eBay Purchase History
Downloader** (README, *Useful tools*): one workbook per year
(`Ebay_Purchase_History_<year>.xlsx`), first sheet, a row per item bought:

| Column | Used for |
|---|---|
| `OrderNumber` | the order number filled in |
| `OrderDate` | `Mon DD, YYYY`; breaks a tie when one listing was bought twice |
| `ItemID` | **the key**: eBay's item number for the listing |
| `Seller`, `ItemName`, `ItemPrice` | shown in the review workbook |

2024-2026 held 3,704 lines, 2,990 orders and 3,700 item ids; 287 orders had
more than one line.

## The key is the item id

Every eBay purchase links its listing (`https://www.ebay.com/itm/<item id>`),
and most items do too (`inventory_item.listing_url`). So the match is exact,
not by words, date or price. **Measured** before building on it, against the
eBay purchases that already had a number: the item id gives back the stored
number for 1,680 of 1,738. Of the 58 that disagree, 8 are typos in the stored
number (`18-12643-5468` for `18-12643-54680`, a stray `:`) and the rest name
another order of the same day -- either could be the one that is wrong, so
those are reported and never changed.

Checked a second way, by money, on a restored copy: for 818 of the 969 orders
the pass numbers, our items' total cost is within 2% of eBay's `OrderTotal`,
82 more within 10%; the rest differ by the sales tax recorded, with every
listing of the order accounted for.

## One purchase per eBay order

An eBay order of several listings had been recorded as a purchase per listing
(132 orders, 395 purchases, in the history). `purchase_order` allows one
purchase per vendor's order number (`uq_purchase_order_vendor_number`), and an
eBay order is one purchase, so those purchases are **merged**: their items move
to one -- the purchase already holding the number, else the oldest -- and the
emptied ones are deleted. Nothing but `inventory_item` points at a purchase.
Each item keeps its own listing's id, which is what `sellers_item_id` is for; a
merged purchase links the order's page on eBay instead of one listing.

Deleting a purchase whose loaded `items` list still held the moved items would
null their purchase -- SQLAlchemy's default for a parent's children -- so the
pass expires the purchase first and confirms nothing still points at it. A test
loads that list, and fails with the expiry removed.

## `sellers_item_id`

`inventory_item.sellers_item_id` (text, indexed; migration `9a4c2e7f5b18`):
the seller's id for the listing the item was bought from. Filled from the
item's own listing link, else its purchase's; only where empty. A lot's pieces
share it. **It is not unique and does not name an order**: a seller lists many
of one coin under one id, and it is bought in several orders -- 4 ids in the
2024-2026 history were, and CC-000684 and CC-000685 share `124766588249` across
orders `16-11696-17632` and `13-11699-25451`. So its index is plain, and the
pass, meeting an id bought more than once, takes the order dated as the
purchase is and leaves a tie for a person. The item editor shows it as a link to the listing, and both
inventory searches take `sellers_item_id`.

## The pass

`python -m app.ebay_orders FILE... [--review OUT.xlsx] [--commit --by EMAIL]`
(`backend/app/ebay_orders.py`):

1. `plan` decides everything, writing nothing: listing ids to set, the order for
   each unnumbered eBay purchase (one order among its listings'; a line of the
   purchase's date preferred), the purchases each order merges, and what is
   left -- no listing link, a listing in no order, or in several.
2. `apply` makes those changes and adds an `item_field_change` row for each
   item's `order_number` and `sellers_item_id`, under `--by`, so each shows in
   the item's History.
3. The review workbook has three sheets: **Numbered**, **Needs you**, and
   **Numbers that disagree** (the stored number, eBay's number for its listing,
   and what each order held).

Run again after a new download: it fills only what is still empty.

## Applied to live, 2026-09-25

From the 2024-2026 workbooks, after a backup verified by restore
(`ccwebdb_pre_ebay_orders_20260925.dump`) and a rehearsal on a restored copy
that gave the same counts:

| | before | after |
|---|---|---|
| eBay purchases without an order number | 1,234 | 2 |
| purchases | 3,922 | 3,658 (264 merged into their orders) |
| items with a listing id | 0 | 5,567 |
| items, total cost | 7,661, $536,222.82 | unchanged |

968 purchases were numbered and 300 items moved; each changed item has its
History rows (7,421). A second run finds nothing to fill.

## Numbers that disagree

Most disagreements were not a wrong number but a **link copied from another
purchase**: live-show lots bought one after another share a seller and a title,
and a purchase's link had been pasted from its neighbor's -- its listing's order
already held by that neighbor, or by nobody while four purchases shared the one
link. The money tells which record is wrong: eBay's line price includes tax, as
our items' `total_cost` does, so the order whose price is ours to the cent is
the purchase's. Settled that way, with the owner's corrections: 27 purchases
relinked to their stored order's one listing (purchase link, items' listing
link and `sellers_item_id`), 2 renumbered, 1 pair of swapped numbers
exchanged, and several same-day swaps the owner named. For 5 of the relinked
purchases the stored order's price differs from ours by $0.12 to $6.38, so
the recorded cost may be wrong, not the order: CC-004156, CC-004217, CC-005060,
CC-005366, CC-006165.

Left for a person, in `logs\ebay_orders_review_20260925_6.xlsx`: 8 purchases
where neither order's price is ours, or both are.
