# eBay order numbers and listing ids

2026-09-25. Purchases recorded without an eBay order number cannot be found by
one or checked against eBay; each item also carries eBay's own item id
(`sellers_item_id`).

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

## The key is the item id

Every eBay purchase links its listing (`https://www.ebay.com/itm/<item id>`),
and most items do too (`inventory_item.listing_url`). So the match is exact,
not by words, date or price. It was measured before being built on: for eBay
purchases that already had a number, the item id gives back the stored number
in nearly every case, and where the two disagree either could be the wrong
one, so a disagreement is reported and never changed.

## One purchase per eBay order

An eBay order of several listings can have been recorded as a purchase per
listing. `purchase_order` allows one purchase per vendor's order number
(`uq_purchase_order_vendor_number`), and an eBay order is one purchase, so
those purchases are **merged**: their items move to one -- the purchase
already holding the number, else the oldest -- and the emptied ones are
deleted. Nothing but `inventory_item` points at a purchase. Each item keeps its
own listing's id, which is what `sellers_item_id` is for; a merged purchase
links the order's page on eBay instead of one listing.

Deleting a purchase whose loaded `items` list still held the moved items would
null their purchase -- SQLAlchemy's default for a parent's children -- so the
pass expires the purchase first and confirms nothing still points at it. A test
loads that list, and fails with the expiry removed.

## `sellers_item_id`

`inventory_item.sellers_item_id` (text, indexed; migration `9a4c2e7f5b18`):
the seller's id for the listing the item was bought from. Filled from the
item's own listing link, else its purchase's; only where empty. A lot's pieces
share it. **It is not unique and does not name an order**: a seller lists many
of one coin under one id, and it is bought in several orders -- CC-000684 and
CC-000685 share `124766588249` across orders `16-11696-17632` and
`13-11699-25451`. So its index is plain, and the pass, meeting an id bought
more than once, takes the order dated as the purchase is and leaves a tie for
a person. The item editor shows it as a link to the listing, and both
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

## Numbers that disagree

Numbers that disagree are reported, never changed. To settle one: eBay's line
price includes tax, as our `total_cost` does, so the order whose price matches
ours to the cent is the purchase's; a live-show description's opening lot
number ("#134 - E - 05/17/26") names the lot, so a description naming another
lot is a row filed or copied onto the wrong purchase.
