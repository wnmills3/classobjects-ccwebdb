# Overview

Last revised: 2026-10-21.

A short account of what this software is, the problem it solves, and how
it is built -- for someone deciding whether to use it, contribute to it, or
learn from it. The detail is in [architecture.md](architecture.md) and the
documents it links to.

## What it is

An inventory and sales system for a coin and paper-money business: one
person or a small shop holding several thousand items and selling them in
their own web store and on outside platforms (eBay, Whatnot, auction
houses). It has two parts:

- a **management console**, where the owner records purchases, receives
  and attributes items, photographs them, offers them for sale, records
  sales and runs reports; and
- a **shop**, where customers browse what is for sale, register and order.

It was written for a real collection being turned into a business, and is
published so that others in the same position can run it, adapt it, or
borrow its ideas.

## The problem

A collection kept in a spreadsheet answers "what do I have". A business has
to answer three harder questions, and every design decision here serves
one of them:

1. **What exactly is this item?** A numismatic buyer knows MS63 from AU58,
   a 1929 National from a 1929 Federal Reserve Bank Note, a proof from a
   business strike. Items are described in the hobby's standard terms --
   the US Mint's, then the grading services' -- and the system checks a
   description against the facts it can (the series a year allows, the
   signatures a note's series must carry, the metal a date was struck in).
2. **What did it cost?** Cost basis must survive for years and for tax:
   an item bought as one of twenty in a lot still traces to its share of
   that purchase, tax and shipping included, to the penny.
3. **Is it available?** An item is never sold twice or offered in two
   places at once. The database enforces it, however the item is offered:
   alone or in a lot, in the shop, on a platform, or in an auction.

Equally deliberate is what it does **not** do: it runs no bidding (the
auction platform does), takes no payments (orders are settled outside it),
and never redistributes a publisher's catalogue numbering or price guide
(see [reference-data.md](reference-data.md)).

## How it works, in brief

- **Purchases to stock.** A purchase is recorded with its items, each with
  a permanent code (`CC-000123`). When the parcel arrives the items are
  received into a storage location; a lot can be split into pieces whose
  costs add up exactly.
- **Description.** The item editor holds every change until Save, detects
  concurrent edits, and records each change in the item's History. Passes
  run from the command line fill blanks from facts (series, composition,
  signatures, weights) without overwriting anything a person typed.
- **Photographs** are stripped of all metadata (they can carry GPS) on the
  way in and served only as generated renditions.
- **Selling.** Items are offered in the shop or on a platform, alone or in
  sales lots; offering elsewhere pauses the shop listing; a sale records
  buyer, price and fees; an auction is assembled, consigned and settled
  here while the platform runs the bidding.
- **Reports** cover the collection, data quality, purchasing, selling and
  money, on screen or as a workbook.
- **Backups** export the whole database to one workbook every day, prove
  the copy by restoring it into a scratch database and comparing every
  row, and only then remove older copies.

## How it is built

A FastAPI and SQLAlchemy backend over PostgreSQL; two React applications
built by Vite and kept in separate bundles; Caddy in front in production.
It runs on Windows 11 and Ubuntu LTS, for development and for hosting, and
every operational script exists for both. The quality gate -- formatting,
lint, types, about 3,400 backend and 1,600 frontend tests, and a check that
the shop bundle carries none of the console -- is at zero findings, so any
new finding is a real one.

## Where to go next

- [README.md](../README.md) -- the developer's entry point: setup, layout,
  API.
- [project-purpose.md](project-purpose.md) -- the rules every design
  follows.
- [architecture.md](architecture.md) -- the components and how a request
  travels through them.
- [workflow-new-collection.md](workflow-new-collection.md) -- an item's
  path from purchase to sale in the console.
