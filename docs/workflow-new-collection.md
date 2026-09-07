# Starting a collection from an empty database

How someone with a fresh installation goes from nothing to a catalogue they can
sell from. This is also the path **every future acquisition takes**, once the
imported collection has been cleaned up -- so it is the normal workflow, not an
onboarding special case.

Read `docs/project-purpose.md` first for why any of this matters.
`docs/workflow-import-and-cleanup.md` covers the one-off job of getting an
existing spreadsheet in.

Status: the schema and the import engine exist. **The entry panels described
here do not yet.** This document specifies what they need to do; the attribution
design in `docs/superpowers/specs/` covers the first slice of building them.

## The starting state

A new installation is not empty. `python -m app.seeding load` populates the
reference tables from versioned JSON in `backend/data/reference/` -- **29
vocabularies across 7 themed files**: `issuer.json` carries currency, country,
denomination and mint; `condition.json` carries the grade scale, grades,
designations and grading services; and so on. What it does not populate is
inventory.

```
reference data     seeded, ~29 vocabularies
inventory_item     0
listing            0
customer           0
```

That split is deliberate. Vocabularies are knowledge about numismatics and are
worth sharing between installations; inventory is what *you* own and is not.
Foreign keys travel between installations as codes rather than ids, so one
installation's catalogue can be inherited by another.

**Vocabularies grow with use.** A grade or a mint missing from a picker is
added from the picker and marked `manual`, which keeps your additions out of a
catalogue shared with someone else. You are never blocked by a missing
vocabulary entry, and you never have to leave the form to add one.

## Two ways an item enters

Everything acquired arrives as one of two shapes, and the difference is
decided by what you bought, not by how you feel about it.

### A purchase lot, decomposed

You bought a container: a roll of 20 Morgan dollars, a dealer's box, an auction
lot of mixed silver. One transaction, one price, many coins, and the details of
the individual coins are unknown at purchase.

```
1. Create the lot
     what it is, what it cost, shipping, tax rate, vendor,
     purchase order, how many pieces it holds

2. Receive it
     status: ordered -> received

3. Decompose it
     split into N pieces; cost divides `equal` for identical
     pieces, or `relative` when they differ in value

4. Attribute each piece
     year, mint mark, grade, variety -- the work that could
     not be done before the coins were in hand
```

The lot row is kept after splitting, not deleted. It holds the purchase order,
the price actually paid and the item code an invoice refers to. It is marked
`split_at` and excluded from every inventory and valuation view, so a lot and
its pieces are never both counted.

Cost division is exact. `price` and `shipping` reconcile to the penny by
largest-remainder allocation; `taxes` is a generated column per row, so the
pieces' rounded taxes can total a cent or two away from the lot's, and that
difference is **reported rather than absorbed**.

### A standalone item

You bought one thing and you know what it is: a graded 1881-S Morgan in a PCGS
slab. There is no parent, no decomposition, and nothing to attribute later.

```
1. Create the item
     kind, denomination, country, year, mint, grade,
     grading service, cost, vendor
2. Receive it
```

Most single purchases are this. Do not create a purchase lot of one -- it adds
a row that must then be excluded from everything, for no benefit.

## Panels needed

| Panel | Does | Notes |
|---|---|---|
| **New purchase** | vendor, order number, date, shipping, tax rate | one purchase may contain many items |
| **New item** | the full item form, standalone or inside a purchase | vocabularies are pickers that can add |
| **New lot** | as above plus piece count | the lot is an item with `storage_quantity > 1` |
| **Split** | divide a lot into pieces, `equal` or `relative` | endpoint exists; the panel does not |
| **Attribute** | walk the pieces one at a time, fill in details | see the attribution design |
| **Group** | assemble existing items under a new parent | cleanup tool, mainly for imported data |

## The rhythm

```
acquire -> receive -> [split] -> attribute -> list -> sell
```

Only purchase lots take the bracketed step. A standalone item goes from
received to listable immediately.

**Listing is gated on the catalogue being trustworthy.** An item whose year and
grade are still the lot's guess is not ready to be described to a buyer, and
the store is where a misdescription becomes a refund. The attribution design
makes that gate a query rather than a checkbox: an item still carrying its
parent's values has not been looked at.

## What does not happen here

- **No bidding.** Auctions run on eBay Live, Whatnot, Heritage or HiBid. This
  system assembles the auction and records the result. See
  `docs/project-purpose.md`.
- **No deletion.** A mistaken row is soft deleted and leaves search; it is not
  removed. A row that ever appeared in an order cannot be deleted at all.
- **No photographs yet.** Image ingest, EXIF stripping and derivative
  generation are built and tested; the panel for attaching a photograph to an
  item while you have it in hand is not.
