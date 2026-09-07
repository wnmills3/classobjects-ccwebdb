# What this project is for

Written 2026-09-07, after the purpose was stated explicitly for the first
time. Everything else in `docs/` describes *how* something works; this
describes *why*, and it is the document to read first when a design decision
looks arbitrary.

## The goal

Turn a personal coin and currency collection into a running business.

The collection is real and already loaded: **7,591 items, $534,177.89 cost
basis, 2,358 troy ounces of fine metal**. It was kept in a spreadsheet for
years. The spreadsheet could record what was bought; it could not support
selling from it, and it could not answer the questions a tax return asks.

Three things have to be true before the first sale:

1. **The catalogue is accurate.** An item offered for sale must be described
   correctly, because a numismatic buyer knows the difference between AU58 and
   MS63 and a misdescription is a refund and a reputation.
2. **Cost basis is defensible.** Every item traces to what was actually paid
   for it, including when twenty coins arrived as one purchase. This is a tax
   requirement, not a nicety.
3. **Availability is unambiguous.** An item cannot be sold twice, and cannot be
   offered in two channels at once.

## Two sales channels

**A web store.** Buyers search and view the catalogue, add to a cart, pay by
card and give a delivery address. They may register as returning customers or
check out as guests. This is the everyday channel and it runs continuously.

**Scheduled auctions.** Periodically a set of items is chosen in advance and
sold to the highest bidder. Items are presented in **auction lots** of one or
more items. The auction itself runs on someone else's platform -- eBay Live or
Whatnot when run directly, Heritage or HiBid when run through an agent -- and
that platform identifies the buyer, the price offered and the timing that
selects the winner.

### This system does not run bidding

That is the single most important scoping fact on the sales side. There is no
bid table, no proxy bidding, no soft close, no outbid notification, and none is
planned. The venue does all of it. What this system does is:

- assemble the auction: choose items, group them into auction lots, set order
- block those items from the store while the auction is pending
- record the outcome afterwards: what sold, to whom, for how much, and what
  came back unsold

The difference matters enormously in effort. A bidding engine is a
concurrency-critical, money-handling, dispute-generating subsystem that could
not run before authentication, payments and email delivery all existed. An
auction *assembly and settlement* feature is a catalogue, a status rule and an
import.

### The rule that connects the two channels

**An item committed to a pending auction cannot be purchased from the store.**

A customer who wants it may register interest, and is notified if the item
becomes purchasable again -- which is exactly what happens to every lot that
does not sell. Unsold items return to the store automatically when the auction
is settled.

This is derived state, not a flag someone maintains: an item is blocked
*because* it belongs to an auction lot in an auction that has not settled. When
the auction settles, the block lifts by itself. A stored `is_blocked` column
would be one missed update away from selling something twice.

## Two meanings of "lot", and why they are kept apart

The word is correct in both uses and they are not the same relationship. The
codebase says which one it means, always.

| Term | Direction | Relationship | Example |
|---|---|---|---|
| **Purchase lot** | how it came in | `inventory_item.parent_item_id` | a roll of 20 Morgan dollars bought as one item, split into 20 |
| **Auction lot** | how it goes out | auction lot membership | five coins bought years apart, offered together as lot 47 |

A purchase lot is decomposed. An auction lot is assembled. An item can belong
to one of each at the same time and they say nothing about each other.

## What has to happen first

Selling cannot start until the catalogue is trustworthy, and it is not yet.
Half the collection arrived as flattened purchase lots -- 769 groups covering
3,777 items, each group being rows the spreadsheet repeated because it had no
way to say "twenty of these". Those rows share a description and an order
number and nothing else; the individual coins have never been described.

So the order of work is forced:

```
catalogue  ->  clean up  ->  list  ->  sell
   done         current      next     after
```

`docs/workflow-import-and-cleanup.md` covers getting the imported collection
into a trustworthy state. `docs/workflow-new-collection.md` covers the same
ground for someone starting from an empty database, which is the path every
future acquisition takes.

## Constraints that apply everywhere

- **Money is exact.** `NUMERIC`, never a float, and divisions reconcile to the
  penny or report the discrepancy rather than absorbing it.
- **Provenance survives.** A sold item's cost basis must still be answerable
  years later, including for a coin that arrived inside a lot of fifty.
- **An item has one identity for life.** `item_code` (`CC-000123`) is issued
  once, never changed and never reused, so a returned item resumes its own
  history and an audit reference is never ambiguous.
- **Nothing is deleted.** Rows are withdrawn, split, superseded or soft
  deleted; the record of what happened stays.
- **The owner is not a customer.** Staff screens show cost basis and margin.
  The public catalogue must not, and that is enforced in the view, not in the
  template.
