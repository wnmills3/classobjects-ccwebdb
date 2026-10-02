# Acquiring, cataloging and selling an item

How an item goes from a purchase to a sale in the management console
(`http://127.0.0.1:5173/management`). This is the path **every acquisition
takes**, whether it is the first item in a new installation or one more
purchase added to an established collection. It is written for the person
running the business: each step says what it is for, then which screen does
it.

Every step works on the `ccwebdb` database directly -- it is the record, and
there is no separate file or workbook to keep in step with it. Read
[project-purpose.md](project-purpose.md) first for why any of this matters,
and [system-administration.md](system-administration.md) for the full
operating detail of each screen.

## The starting state

A new installation is not empty. `python -m app.seeding load` fills the
reference vocabularies from versioned JSON in `backend/data/reference/`:
grades, denominations, issuers and mints, design series, note issues,
signatures, compositions, attributes, error types and the rest. It does not
create inventory.

That split is deliberate. Vocabularies are knowledge about numismatics and are
shared between installations; inventory is what *you* own. Foreign keys travel
between installations as codes, not ids. Only facts are shipped: a
publisher's numbering (Friedberg, Pick) or a price guide is never seeded, so
the Friedberg numbers in the system are ones the owner records.

**Vocabularies grow with use.** A value missing from a picker is added from the
picker ("+ Add a new value...") and marked `manual`, which keeps one
installation's additions out of what is shipped. The **Vocabularies** page
(`/management/vocabularies`) renames, retires, merges and adds aliases; it
does not create values. The **Lists** page (`/management/lists`) corrects and
prunes the owner-kept lists that are not vocabularies: Friedberg numbers,
sellers, vendors and storage locations.

## The rhythm

```
purchase -> receive -> [split] -> attribute -> photograph -> offer -> sell
```

Only a purchase lot you mean to break up takes the bracketed step.

### 1. Enter the purchase

*Purpose: every item's cost basis traces to what was actually paid, on a
known order from a known vendor.*

**Purchases** (`/management/purchases`) either adds to an existing purchase
or starts a new one: vendor, order number (left blank, the next
`Order-0001`-style number is issued), order date, web address, seller and
notes. A vendor is where the purchase was made (ebay.com, a dealer); a seller
is the account on it. Both can be added inline from their pickers.

Then add the purchase's items with the **New item** form beneath it, one
after another ("Save and add another" keeps what items on one order share).
No item is entered outside a purchase: a single buy is a purchase holding one
item. Each item carries its own item cost and shipping.

Each item is entered `ordered`, or `received` if it is already in hand. The
sales-tax rate is stamped onto each item when it is created: the configured
default (`SALES_TAX_RATE` in `.env`), a rate typed in **Tax rate** on the
Purchases page, or zero when **No sales tax charged** is ticked. **Tax on
shipping** chooses whether shipping is taxed. A later change to the setting
rewrites nothing already recorded.

Facts that follow from what you entered are filled in as *suggested* values
(`GET /api/defaults/note`, `/api/defaults/coin`) and never replace a value
you chose: for a note, its type, seal colour, signature combination, Federal
Reserve district and design series from its denomination, series and serial;
for a coin, its metal and design series from its denomination, country and
year.

Two shapes of purchase:

- **A standalone item** -- one thing you can describe now: a graded 1881-S
  Morgan in a PCGS slab. Most purchases are this. Do not create a lot of one.
- **A purchase lot** -- a container bought for one price: a roll of 20 Morgan
  dollars, a dealer's box. Enter it as one item with **Pieces** above 1. It can
  stay that way (a tube of identical rounds is fine as one row with a piece
  count; weight and value multiply by it), or be split later.

### 2. Receive it

*Purpose: know what actually arrived and where it is kept -- only a received
item can be offered.*

**Receive** (`/management/receiving`) finds what has not arrived, by any part
of an order number or by what the item is, and records one of **Receive**,
**Missing**, **Returned** or **Cancelled** for the line clicked -- or, with
**Receive all N still ordered** on a purchase's link, for every line of it
still ordered -- with an arrival date and a storage location, in one
transaction (`POST /api/inventory/receive`). A purchase's "Receive these" link opens it
already searched for that order.

The receipt dialog opens with the facts that identify the piece -- a note's
series, denomination, serial and plate numbers, a coin's year, mint and
denomination -- saved with Receive, and says what they decide. It also takes
a note, field reviews and, for a single item, photographs (chosen,
dropped or pasted). For a banknote it records the Friedberg number: the owner
reads it off the note or looks it up (the dialog can open a Google search
with the note's facts in a window beside it), and types it in; nothing is
fetched or stored automatically. Receipt is the best moment to record what
only the object shows -- seal, signatures, district, plate numbers, errors --
but nothing there is required.

### 3. Split a lot (optional)

*Purpose: give each piece of a lot its own record and its own share of the
cost, so each can be sold and its gain reported separately.*

**Split into pieces...** in the item editor (`POST /api/inventory/{id}/split`,
`app/splitting.py`) divides a lot into one child per piece, dividing cost
`equal`ly for identical pieces -- nine proof sets, a tube of rounds -- or
`relative` to a value per piece when they differ (a mint set's cent and half
dollar). The dialog starts with a row per piece the lot records; each row may
carry its own description and year, and shows its estimated share. Every
piece keeps the lot's seller's title, purchase, listing link and seller's
item id.

It splits the saved record, so unsaved edits must be saved first. It is not
offered for a lot already split or a piece of one, and it is refused for a
lot that appears in an order or sits in an offered sales lot.

`item_cost` and `shipping_cost` reconcile to the penny; `sales_tax` is
generated per row, so the pieces' rounded tax can differ from the lot's by a
cent or two, and that difference is reported, not absorbed. The lot is kept,
marked `split_at`, and excluded from every count, so a lot and its pieces are
never both counted; its editor names the pieces it became.

### 4. Attribute

*Purpose: establish what each item actually is -- year, mint mark, grade,
variety, serial -- because a misdescribed coin is a refund and a reputation.*

The inventory pages (**Coins**, `/management/inventory/coins`, and
**Currency**, `/management/inventory/currency`) are the tools:

- **Search and diagnostics.** Named diagnostics (`app/issues.py`: no year, no
  grade, no country, no denomination, zero cost, `Mixed` marker, unreviewed,
  and for notes star mismatch, malformed serial, repeated identity and
  others) are filters with counts and row badges.
- **Bulk edit.** Select rows and set what they share in one action.
- **Review.** Walk a result one item at a time. The queue is frozen at entry,
  so fixing an item does not shift the positions and skip the next one.
- **Field reviews** record that a person confirmed a field against the object.
- **The item editor** also records errors (mint and printing errors), offers
  **Suggest description** (a description composed from the record), and
  shows the item's history: its edits, status moves and location moves in
  one timeline.

Bulk-set what a run of similar items shares, then review the exceptions.

### 5. Photograph

*Purpose: a buyer sees the item, and the owner has a record of the object
itself.*

Photographs can be added at any time after receipt:

- in the item editor's photographs panel -- from a file (chosen, dropped or
  pasted) or from a web address the server fetches -- where one is also made
  primary, given a role, moved to another item or detached;
- on **Photos** (`/management/photos`), which lists photographs not yet filed
  to an item and files each one;
- in bulk with `python -m app.photo_import [--root PATH] [--commit]`, which
  files a directory by the `CC-000412_01.jpg` naming convention and leaves
  any file it cannot place unattached, for **Photos**.

Metadata, including GPS, is stripped on the way in, and only generated
renditions are served. The shop shows only an item's primary photograph.

### 6. Offer and sell

*Purpose: sell each item in exactly one place at a time, and record every
sale with its buyer, price and fees.*

An item must be `received` to be offered. Offer it on a platform (the store,
eBay, Whatnot, an auction house) at a price with **Offer for sale...**, from
the Coins or Currency page's bulk bar for a selection or from the item
editor's Offers panel. **Listings** (`/management/listings`) shows every
offer, edits or ends it, and records a sale made on an outside platform.
Group items into a **sales lot** on **Lots** (`/management/lots`); assemble,
schedule, consign, close and settle auctions on **Auctions**
(`/management/auctions`). The platforms themselves, with their default fees,
are kept on **Platforms** (`/management/platforms`).

Store sales arrive as orders, worked on **Sales** (`/management/sales`),
where the owner can also place an order on a customer's behalf. An item has
at most one active offer at a time.
[specs/selling-design.md](specs/selling-design.md) has the full model.

Changing an item that is on offer warns first, since the buyer sees what was
listed ([specs/for-sale-guards-design.md](specs/for-sale-guards-design.md)).

**Reports** (`/management/reports`) answers questions over the whole
collection, grouped as Collection, Data quality, Purchasing and receiving,
Selling and Money, and exports any report as a workbook ([specs/reporting-design.md](specs/reporting-design.md)).

## What does not happen here

- **No bidding.** Auction platforms run the bidding; this system assembles the
  auction and records the result.
- **No deletion of history.** A row entered by mistake is soft deleted and
  leaves search. An item that has ever been offered cannot be deleted; a lot
  with pieces, or an item in a sales lot, cannot be deleted until the pieces
  are detached or the item is taken out of the lot.
- **No grouping tool.** Existing separate items cannot be gathered under a
  new parent in the console.
