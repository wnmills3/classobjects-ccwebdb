# Acquiring, cataloguing and selling an item

How an item goes from a purchase to a sale in the owner console
(`http://127.0.0.1:5173/owner`). This is the path **every acquisition
takes**, and the path a new installation starts on from an empty inventory.

Read [project-purpose.md](project-purpose.md) first for why any of this
matters, and [system-administration.md](system-administration.md) for the
full operating detail of each screen.

## The starting state

A new installation is not empty. `python -m app.seeding load` fills the
reference vocabularies from versioned JSON in `backend/data/reference/`:
grades, denominations, mints, series, note facts, compositions and the rest.
It does not create inventory.

That split is deliberate. Vocabularies are knowledge about numismatics and are
shared between installations; inventory is what *you* own. Foreign keys travel
between installations as codes, not ids.

**Vocabularies grow with use.** A value missing from a picker is added from the
picker ("Add a new value") and marked `manual`, which keeps one owner's
additions out of what is shipped. The **Vocabularies** page renames, retires,
merges and adds aliases; it does not create values.

## The rhythm

```
purchase -> receive -> [split] -> attribute -> photograph -> offer -> sell
```

Only a purchase lot you mean to break up takes the bracketed step.

### 1. Enter the purchase

**New purchase** (`/owner/purchases/new`): vendor, order number, date,
shipping and tax. Then add its items with the **New item** form, one after
another ("Save and add another" keeps what items on one order share). No item
is entered outside a purchase.

Each item is `ordered`, or `received` if it is already in hand. The sales-tax
rate is stamped onto each item when it is created: the configured default,
a rate typed on the purchase, or zero when **No sales tax charged** is ticked.
A later change to the setting rewrites nothing already recorded. Facts that follow from what
you entered -- a note's class, seal and signatures from its denomination and
series, a coin's composition and metal from its denomination and year -- are
filled in as *suggested* values, and never replace a value you typed.

Two shapes of purchase:

- **A standalone item** -- one thing you can describe now: a graded 1881-S
  Morgan in a PCGS slab. Most purchases are this. Do not create a lot of one.
- **A purchase lot** -- a container bought for one price: a roll of 20 Morgan
  dollars, a dealer's box. Enter it as one item with **Pieces** above 1. It can
  stay that way (a tube of identical rounds is fine as one row with a piece
  count; weight and value multiply by it), or be split later.

### 2. Receive it

**Receive** (`/owner/receiving`) finds what has not arrived, by any part of an
order number or by what the item is, and records one of received, missing,
returned or canceled, with an arrival date and a storage location. The
receipt dialog also takes a note, photographs, field reviews and, for a
banknote, its Friedberg number (the owner's own, read off the note; nothing is
fetched). Receipt is the best moment to record what only the object shows --
seal, signatures, district, plate numbers, errors -- but nothing there is
required.

### 3. Split a lot (optional)

`POST /api/inventory/{id}/split` divides a lot into one child per piece,
dividing cost `equal`ly for identical pieces or `relative` to a value per piece
when they differ (a mint set's cent and half dollar). `item_cost` and
`shipping_cost` reconcile to the penny; `sales_tax` is generated per row, so
the pieces' rounded tax can differ from the lot's by a cent or two, and that
difference is reported, not absorbed. The lot is kept, marked `split_at`, and
excluded from every count, so a lot and its pieces are never both counted.

**Not built:** a Split panel in the console. The API works; the screen does
not exist.

### 4. Attribute

Establish what each item actually is: year, mint mark, grade, variety,
serial. The inventory pages (**Coins**, **Currency**) are the tools:

- **Search and diagnostics.** Named diagnostics (no year, no grade, no
  country, `Mixed` marker, unreviewed, and others) are filters with counts and
  row badges.
- **Bulk edit.** Select rows and set what they share in one action.
- **Review.** Walk a result one item at a time. The queue is frozen at entry,
  so fixing an item does not shift the positions and skip the next one.
- **Field reviews** record that a person confirmed a field against the object.

Bulk-set what a run of similar items shares, then review the exceptions.

### 5. Photograph

Photographs can be added at any time after receipt: in the item editor's
photos panel, from **Photos** (`/owner/photos`, for photographs not yet filed
to an item), or in bulk with `python -m app.photo_import` using the
`CC-000412_01.jpg` naming convention. Metadata, including GPS, is stripped on
the way in. The shop shows only an item's primary photograph.

### 6. Offer and sell

An item must be `received` to be offered. From the item editor or
**Listings**, offer it on a platform (the store, eBay, Whatnot, an auction
house) at a price; group items into a **sales lot** on **Lots**; assemble and
settle auctions on **Auctions**. Store sales arrive as orders; a sale on an
outside platform is recorded against its listing. An item has at most one
active offer at a time. [specs/selling-design.md](specs/selling-design.md)
has the full model.

Changing an item that is on offer warns first, since the buyer sees what was
listed ([specs/for-sale-guards-design.md](specs/for-sale-guards-design.md)).

## What does not happen here

- **No bidding.** Auction platforms run the bidding; this system assembles the
  auction and records the result.
- **No deletion of history.** A row entered by mistake is soft deleted and
  leaves search. An item that has ever been offered, or a lot with pieces,
  cannot be deleted.
- **No grouping tool.** Existing separate items cannot be gathered under a
  new parent in the console.
- **No creating storage locations** in the console; the receiving picker
  lists existing ones.
