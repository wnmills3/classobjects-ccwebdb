# What this project is for

Everything else in `docs/` describes *how* something works; this describes
*why*. Read it first when a design decision looks arbitrary.

## The goal

Turn a personal coin and currency collection into a running business.

The collection is real and loaded -- several thousand items, kept for years in
a spreadsheet. The spreadsheet could record what was bought; it could not
support selling from it, and it could not answer the questions a tax return
asks. (For current totals, query the database; the figures move with every
entry and pass. [data-import-plan.md](data-import-plan.md) §13 gives the
queries.)

Three things have to be true for a sale:

1. **The catalogue is accurate.** An item offered for sale must be described
   correctly, because a numismatic buyer knows the difference between AU58 and
   MS63 and a misdescription is a refund and a reputation.
2. **Cost basis is defensible.** Every item traces to what was actually paid
   for it, including when twenty coins arrived as one purchase. This is a tax
   requirement, not a nicety.
3. **Availability is unambiguous.** An item cannot be sold twice, and cannot be
   offered in two places at once.

## Where items are sold

**The web store** (the shop). Buyers browse the catalogue, add to a cart and
order, as registered customers.

**Outside platforms** -- eBay, Whatnot, auction houses such as Heritage and
HiBid. The owner lists there by hand; this system records the offer, and
afterwards the sale: buyer, price, fees.

**Scheduled auctions.** A set of items is chosen in advance and offered in
**auction lots** of one or more items, on someone else's platform. That
platform identifies the buyer, the price and the timing that selects the
winner.

### This system does not run bidding

The single most important scoping fact on the sales side. There is no bid
table, no proxy bidding, no soft close, no outbid notification, and none is
planned. The venue does all of it. What this system does is:

- assemble the auction: choose items, group them into lots, number them
- keep those items out of every other channel while the auction is pending
- record consignment when an auction house takes custody
- settle it afterwards: what sold, to whom, for how much, and what came back

A bidding engine is a concurrency-critical, money-handling, dispute-generating
subsystem. Auction *assembly and settlement* is a catalogue, a status rule and
a record of results.

### The rule that connects the channels

**An item has at most one active offer.** The database enforces it (a partial
unique index on `offer_claim`), so it holds however the item is offered:
alone or in a lot, in the store, on a platform or in an auction. Offering an
item elsewhere pauses its store listing; an item in a pending auction cannot
be bought from the store. When an auction settles, unsold items go back to the
store at their old price if they were there before, and otherwise to `held`.

The block is derived from the offers themselves, not a flag someone
maintains: a stored `is_blocked` column would be one missed update away from
selling something twice. [specs/selling-design.md](specs/selling-design.md)
has the full model.

Not built: customers registering interest in an item held by an auction, which
needs email delivery.

## Three meanings of "lot", kept apart

The word is correct in each use and they are not the same relationship. The
code says which one it means, always.

| Term | Direction | Relationship | Example |
|---|---|---|---|
| **Purchase lot** | how it came in | `inventory_item.parent_item_id`, after a split | a roll of 20 Morgan dollars bought as one item, split into 20 |
| **Sales lot** | how it goes out | `sales_lot_item`; dissolves if it does not sell | five coins bought years apart, offered together |
| **Auction lot** | a sales lot in an auction | `auction_lot` | that sales lot, as lot 47 in a Heritage sale |

A purchase lot is decomposed; a sales lot is assembled. An item can belong to
one of each at once and they say nothing about each other.

## The order of work

```
catalogue  ->  clean up  ->  list  ->  sell
```

Cataloguing (the import) is done. Clean-up -- attribution: establishing what
each item actually is -- is ongoing and has no shortcut. Listing and selling
are built: offers, sales lots, auctions and recorded sales. An item is ready to
list when it is described well enough to sell; the console's diagnostics show
what is still missing.

[workflow-import-and-cleanup.md](workflow-import-and-cleanup.md) covers
getting an imported collection into that state.
[workflow-new-collection.md](workflow-new-collection.md) covers every
acquisition after it, which is the normal path from now on.

## The database is the system of record

Since 2026-09-16 the `ccwebdb` database is the record. The spreadsheet is a
historic source. Corrections, derived classifications, receipts, photographs
and sales exist only in the database, so it is what gets backed up, and data
is fixed with passes over stored items or in the console -- never by
re-importing. [data-import-plan.md](data-import-plan.md) §1 has the
consequences.

## Constraints that apply everywhere

- **Money is exact.** `NUMERIC`, never a float, and divisions reconcile to the
  penny or report the discrepancy rather than absorbing it.
- **Provenance survives.** A sold item's cost basis must still be answerable
  years later, including for a coin that arrived inside a lot of fifty.
- **An item has one identity for life.** `item_code` (`CC-000123`) is issued
  once, never changed and never reused, so a returned item resumes its own
  history and an audit reference is never ambiguous.
- **Nothing is deleted.** Rows are withdrawn, split, ended, retired, merged or
  soft deleted; the record of what happened stays.
- **Only facts are shipped as reference data.** The catalogue is sold, so
  anything seeded is redistributed with it: design series, office holders and
  mint specifications may be; a publisher's numbering or price guide may not.
- **The owner is not a customer.** The console shows cost basis, margin,
  storage location and inventory photographs. The shop must not, and that is
  enforced where each public response is built (`routers/catalog.py`), field
  by field, and tested.
