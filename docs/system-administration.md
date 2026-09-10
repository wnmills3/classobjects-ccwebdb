# System administration

How to run the system rather than how to build it: who may sign in and what
they may do, which settings exist and which must be changed before anyone
outside the machine can reach it, and every way an inventory item comes into
being, changes, or leaves.

`docs/runtime-operations.md` covers starting and stopping the services.
`docs/environment-setup.md` covers installing them. This document covers what
to do once they are running.

Everything below is administered through the **owner console** at `/owner`,
which is a separate application from the shop -- see
`docs/specs/owner-console-separation-design.md`. Nothing here is reachable
from the storefront.

## Accounts

### The two roles

`UserRole` has exactly two members, and the difference is not cosmetic:

| Role | Sees | Reaches |
|---|---|---|
| `customer` | the shop; their own orders | nothing under `/owner`, no cost basis |
| `admin` | everything | the whole console, including what each item cost |

There is no third role and no per-permission grid. The line the system
actually enforces is **cost basis and provenance are not customer-visible**,
and one boolean expresses it. A finer scheme would be more configuration to
keep correct without a second kind of person to apply it to.

Every administrative endpoint carries `require_admin` (`backend/app/deps.py`).
That dependency, not the console's separate bundle, is the access control --
the bundle split removes an information leak, it does not enforce anything.

### How accounts come to exist

- **Self-service registration** (`POST /api/auth/register`) always creates a
  `customer`. There is no way to register as an administrator.
- **Promotion** is the only route to `admin`: an existing administrator
  changes an account's role in the console under **People**.
- **The first administrator** is seeded by `python -m app.seed`, from
  `first_admin_email` / `first_admin_password` in the settings. Change the
  password immediately -- the default is published in this repository.

### What an administrator may change

On someone else's account (`PATCH /api/users/{id}`), exactly three fields:

| Field | Notes |
|---|---|
| `full_name` | display only |
| `role` | `admin` or `customer` |
| `is_active` | `false` suspends sign-in without deleting anything |

The schema is `extra="forbid"`, so a request naming any other field is
refused rather than silently ignored. **Email is not editable** -- it is the
account's identity, and the audit trail on `item_status_history`,
`location_history` and `item_field_review` points at the user id behind it.

### Passwords

`POST /api/users/{id}/password` sets a password directly, minimum eight
characters. There is no reset-by-email flow because there is no mail
configuration: an administrator sets the password and tells the person out of
band.

Changing a password **bumps `token_version`, which invalidates every token
already issued for that account.** This matters more than it sounds. Tokens
are stateless JWTs with no server-side store, so without that counter a reset
would change only what the person types next time -- every token issued
before the reset would keep working until it expired, which is useless for
the case a reset exists for.

### The last-administrator guard

Demoting or deactivating the final active administrator is refused with 409.

The check is deliberately about the **result, not the actor**: an
administrator demoting themselves is fine when somebody else can still
administer, and fatal when not. Without it, one click locks everyone out with
no way back through the application at all -- recovery would mean editing the
database by hand.

To hand over administration: promote the new person first, confirm they can
sign in, then demote the old account.

## Settings

Defaults live in `backend/app/config.py` and are overridden by environment
variables or a `.env` file. Names are the field names upper-cased
(`jwt_secret` -> `JWT_SECRET`).

### Must change before anything outside this machine can reach the system

| Setting | Default | Why |
|---|---|---|
| `JWT_SECRET` | `dev-only-insecure-secret-change-me` | signs every token; anyone who knows it can mint an admin session. The default is deliberately obvious so an unconfigured deployment is easy to spot |
| `FIRST_ADMIN_PASSWORD` | `adminpassword` | published in this repository |
| `DATABASE_URL` | local `ccwebdb` with `devpassword` | contains the database password |

### Worth reviewing

| Setting | Default | Meaning |
|---|---|---|
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | how long a session lasts before the refresh token is used |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `14` | how long someone can stay signed in without re-entering a password |
| `CORS_ORIGINS` | the two dev Vite URLs | must list the real origin once the frontend is served from anywhere else |
| `API_PREFIX` | `/api` | changing it moves every endpoint |

### Sales tax on acquisitions

| Setting | Default | Meaning |
|---|---|---|
| `SALES_TAX_RATE` | `0.0635` | the rate a newly recorded purchase is taxed at. A fraction, not a percentage: `0.0635` is 6.35%. A value outside 0-1 is refused when the API starts, so `6.35` typed for 6.35% cannot quietly multiply every cost by 7.35 |
| `SALES_TAX_INCLUDES_SHIPPING` | `true` | whether shipping is part of the taxed amount |

**Both are copied onto each item when it is created, and never read again for
that item.** Tax paid is a historical fact, so changing either setting governs
purchases recorded afterwards and rewrites none recorded before. Every item
keeps its own `tax_rate` and `tax_includes_shipping`; the database computes
`sales_tax` and `total_cost` from those, and neither can be written directly.

A purchase that was charged no tax is recorded by setting that item's
`tax_rate` to 0: the **No sales tax charged** box on the item edit form, or
`tax_rate` through `PATCH /api/inventory/{id}`, or `POST /api/inventory/bulk`
for a batch of orders. Unticking the box restores the rate the item was bought
at -- or, for an item recorded as untaxed, which has no rate of its own to go
back to, the configured one.

A rate cannot reproduce a marketplace's own rounding: Whatnot charged $0.35 on
one order where 6.35% of $5.40 is $0.34. Those pennies stay as computed.

Settings are read when the API starts. Change them in `.env` (the committed
template is `.env.example`), then restart the API.

### Image handling

| Setting | Default | Meaning |
|---|---|---|
| `MEDIA_ROOT` | `<repo>/media` | where image bytes live. **Bytes are never stored in the database** -- a collection's photographs run to gigabytes. Back this up separately |
| `THUMBNAIL_MAX_PX` | `320` | longest edge of the small rendition |
| `WEB_MAX_PX` | `1600` | longest edge of the large rendition. Originals are never served; public requests are answered only from these |
| `MAX_UPLOAD_BYTES` | 25 MB | refused before anything is decoded |
| `MAX_IMAGE_PIXELS` | 50,000,000 | a "decompression bomb" is a small file that expands to gigabytes of pixels, which is why both limits exist |

Changing the rendition sizes does not regenerate existing images.

## Inventory items

### How an item comes into being

Four paths create an `inventory_item`, and only two are routine.

| Path | When | Notes |
|---|---|---|
| **Spreadsheet import** (`app.importers`) | the normal path | creates the item, its purchase order and its vendor together, at the moment of purchase |
| **Splitting a lot** (`POST /api/inventory/{id}/split`) | a bought lot becomes individual pieces | children inherit the parent's claims and a share of its cost; the parent gets `split_at` and disappears from every view |
| **The shop's catalogue** (`POST /api/catalog`) | creating something to sell directly | creates the item *and* its listing together, defaulted to `received` / `listed` / `unverified` / `single`, `source = manual` |
| **`python -m app.seed`** | development only | never on real data |

All four record an opening `item_status_history` row, so every item has a
lifecycle from its first row -- see below.

**The item code is permanent.** It is drawn from a database sequence, assigned
once, never reused and never changed. It survives everything that happens to
the object -- listed, sold, returned by the buyer, relisted -- because a
returned item must resume its own history rather than start a new one.

### The canonical spreadsheet

The seeding import reads exactly one file:

```
C:\Users\wnmil\OneDrive\wnm3_coins.xlsx
```

**Seven files on this machine are named exactly `wnm3_coins.xlsx`** -- under
`OneDrive\Documents\`, `OneDrive\Documents\coins\`, `OldDocuments\`,
`OldDocuments\coins\`, `OneDrive\Imports\`, and a phone download under
`CrossDevice\`. They are stale snapshots, not alternates: they range from
2025-02 to 2026-09 and differ from the canonical file by hundreds of rows.
None of them is the one to import.

Two traps make picking the right file harder than it looks:

- **The nearest decoys are only days old.** The `OneDrive\Documents\` pair is
  from 2026-09-02, six days behind the canonical file -- recent enough to look
  current and to import without any obvious complaint.
- **Timestamped backups sit in the canonical directory.** `OneDrive\` also
  holds eight `wnm3_coins.backup-<date>-<time>.xlsx` files, so a glob like
  `wnm3_coins*.xlsx` in that folder matches nine files, only one of which is
  canonical. Match the exact name, never a prefix.

If the path above is ever in doubt, the canonical file is the most recently
modified of the seven -- but confirm the date rather than assuming, because
OneDrive sync can restat a file it did not change.

Two different things are called "the spreadsheet", and conflating them loses
data:

| | `wnm3_coins.xlsx` | The export/import round trip |
|---|---|---|
| Purpose | one-off seeding of an empty database | editing existing records outside the app |
| Lifetime | throwaway; the code that reads it is disposable | permanent, maintained code |
| Columns | only what the original hand-kept sheet held | every field, so a round trip loses nothing |
| Carries plate numbers or errors? | **no** | yes |

`wnm3_coins.xlsx` has no `plate_position`, no face or back plate number, and no
structured error types -- currency errors appear only as loose prose in its
`Rating` column. Anything imported from it is therefore incomplete by
construction, and a re-import after those fields are populated would discard
them. **Re-importing is safe only into a cleared database.**

**Its `Taxes`, `Total Cost`, `Profit` and `Profit %` columns are headers over
empty cells** -- roughly nineteen of 7,653 rows carry a value. The importer is
right to read only `Price` and `Shipping`. Treat those four columns as absent:
summing one yields a number that looks like a total and means nothing.

### How an item changes

| Endpoint | Use |
|---|---|
| `PATCH /api/inventory/{id}` | one item, any editable field |
| `POST /api/inventory/bulk` | the same change across many items, one transaction, all or nothing |
| `POST /api/inventory/receive` | record what arrived -- see below |
| `POST /api/inventory/{id}/reviewed` | mark fields as confirmed by a person looking at the object |

**Editable scalars:** `source_title`, `description`, `year_start`, `year_end`,
`fineness`, `gross_weight_ozt`, `fine_weight_ozt`, `piece_count`, `item_cost`,
`shipping_cost`, `tax_rate`, `tax_includes_shipping` -- the last two described
under *Sales tax on acquisitions* above. Those two are NOT NULL, and a null
sent for either is refused naming the field.

**Editable classifiers**, all set by code rather than id: `item_kind`,
`country`, `denomination`, `bullion_form`, `grade`, `grade_designation`,
`grading_service`, `metal`, `series`, `storage_form`, `authenticity`,
`status`, `disposition`. Five of those are NOT NULL (`item_kind`,
`storage_form`, `authenticity`, `status`, `disposition`) and refuse a null or
empty code rather than failing with an unhandled database error.

An unknown field name is **refused**, not ignored -- a typo in a bulk edit
must not silently do nothing.

### Status and location have one door

Everything that touches an item's lifecycle goes through
`backend/app/lifecycle_writes.py`, which holds exactly three functions:

| Function | Records |
|---|---|
| `record_initial_status` | the opening row, `from_status_id = NULL` -- an item has no status before it has one |
| `set_status` | a transition, carrying the previous status |
| `set_location` | a move, with its `location_history` row |

`status_id` and `storage_location_id` are written **only** through these.
Assign either column anywhere else and the history silently stops being true.

An opening row and a transition are separate functions because they are
separate facts, and because `set_status` no-ops when the status is unchanged
-- a newly created item already has one, so reusing it at creation would
record nothing at all.

Two consequences for an administrator:

- **Every item has a history from its first row.** All four creation paths
  record one, so an item's lifecycle is always recoverable from the table
  rather than inferred from its current state.
- **Correcting a status through the console is itself recorded**, including
  who did it and when. The history is not a log you can quietly edit around.

### Receiving

`POST /api/inventory/receive` records what a parcel actually contained. One
request covers one item or twenty, and is all-or-nothing.

Four outcomes: `received`, `missing`, `returned`, `canceled`. `missing` means
paid for, not cancelled, never arrived -- and a parcel written off as missing
can still be received later when it turns up.

- `arrived_on` is a **calendar date**, deliberately not a timestamp. A parcel
  that arrived on the 9th arrived on the 9th in every timezone. It is optional;
  omitted, the history row's `arrived_on` is NULL. A date more than one day
  ahead of UTC's today is refused.
- A storage location is recorded only for `received`, together with its
  `location_history` row.
- Receiving something already received is refused with 409, naming its current
  status and the date it arrived, so a double submission is distinguishable
  from the wrong row.

### Optimistic concurrency

`inventory_item` and `listing` carry a `version` column. An update sends the
version it read, and the write fails if somebody else changed the row first.

This is the protection PostgreSQL's MVCC does not give: two people open the
same item, both save, and without it the second silently overwrites the first
with values loaded before the change. Editing an item is a document edit --
conflicts are rare, take minutes of human thinking time, and a person can
resolve them.

### Removing an item

`DELETE /api/inventory/{id}` is a **soft delete**, and it means one specific
thing: *this row should never have existed*. A typo, a duplicate import. It is
not how an item leaves the collection -- something sold, lost or given away
changes its `disposition` or `status` and keeps its history.

Two cases are refused, because both would fail silently:

- **A lot with pieces.** It holds the cost basis its children were allocated
  from; deleting it would leave those coins descended from nothing.
- **An item that has been listed or sold.** Order history references it, and
  the reports exclude deleted rows -- so the order would point at a row that
  is not there.

Deleting twice is not an error.

`DELETE /api/inventory/{id}/parent` detaches a split child from its parent,
for when the lineage itself was wrong.

## Reference vocabularies

Classifiers -- grades, mints, denominations, metals and the rest -- are rows
in reference tables, not free text. Administrators add and rename values in
the console.

Every row records how it came to exist: `seeded` (shipped), `derived`
(inferred by the importer), or `manual` (typed by a person). A machine guess
must never be indistinguishable from a curated fact.

**Before adding reference data, check it is free to use.** The catalogue is
sold, so reference data shipped inside the product is redistributed with it.
Facts are safe -- who held an office and when, design series names and year
spans, mint specifications, legislated compositions, common collector
nicknames. A publisher's *arrangement* is not: Friedberg numbering, Pick
numbering, vendor price-guide values, or any catalogue's mapping of attributes
to its own numbers. See the Reference data section of `CLAUDE.md`.

## Things that are deliberately not configurable

Worth knowing so nobody goes looking for a setting that was never written:

- **Per-permission roles.** Two roles, one boundary; see above.
- **Password reset by email.** No mail configuration exists.
- **Editing an account's email.** It is the account's identity.
- **Deleting a user.** Deactivate instead -- history rows reference the user
  id, and `ON DELETE SET NULL` on those columns exists so that deactivating a
  member of staff never erases the record that the work was done.
- **A storage location that customers can see.** `models/lifecycle.py` calls
  this an authorisation boundary enforced by the `public_catalog` view and by
  tests, not a convention: a public listing that leaked the safe-deposit box
  holding an item would be a security failure, not a cosmetic one.
