# System administration

How to run the system rather than how to build it: who may sign in and what
they may do, which settings exist, how the collection record is kept correct,
every way an inventory item comes into being, changes or leaves, and how
backups and schema releases are done.

`docs/runtime-operations.md` covers starting and stopping the services;
`docs/environment-setup.md` covers installing them;
`docs/data-import-plan.md` covers where the collection's data came from and
how the record is structured.

Everything below is administered through the **owner console** at `/owner`, a
separate application from the shop (`docs/specs/owner-console-separation-design.md`).
Nothing here is reachable from the storefront.

Command-line passes run from `backend\` with the `ccwebdb` conda environment
active. Every pass reports and touches nothing unless given `--commit`.

## Accounts

### The two roles

| Role | Sees | Reaches |
|---|---|---|
| `customer` | the shop; their own orders | nothing under `/owner`, no cost basis |
| `admin` | everything | the whole console, including what each item cost |

There is no third role and no per-permission grid. The line the system
enforces is **cost basis and provenance are not customer-visible**, and one
boolean expresses it.

Every administrative endpoint carries `require_admin` (`backend/app/deps.py`).
That dependency, not the console's separate bundle, is the access control; the
bundle split removes an information leak, it does not enforce anything.

### How accounts come to exist

- **Self-service registration** (`POST /api/auth/register`, the shop's
  **Register** page) always creates a `customer`.
- **An administrator creates it** (`POST /api/users`, **People → Accounts →
  New account**): email, name, role and an initial password. The role has no
  default in the API, so an administrator is never created by omission. An
  email already in use is refused with 409. There is no mail configuration,
  so pass the password on out of band.
- **Promotion**: an administrator changes an account's role under **People**.
- **The first administrator** of a new database is created by
  `python -m app.seed`, from `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD`.
  That module also creates five demo items with listings, so it is never run
  against a database holding a real collection. Change the password
  immediately -- the default is published in this repository.

### What an administrator may change

On someone else's account (`PATCH /api/users/{id}`), exactly three fields:

| Field | Notes |
|---|---|
| `full_name` | display only |
| `role` | `admin` or `customer` |
| `is_active` | `false` suspends sign-in without deleting anything |

The schema is `extra="forbid"`, so a request naming any other field is
refused. **Email is not editable** -- it is the account's identity, and the
audit trail on `item_status_history`, `location_history` and
`item_field_review` points at the user id behind it.

`POST /api/users/{id}/customer` returns the customer record behind an account,
creating it if the account has never bought anything, so an order can be
placed for them.

### Passwords

`POST /api/users/{id}/password` sets a password directly, minimum eight
characters. There is no reset-by-email flow.

Changing a password **bumps `token_version`, which invalidates every token
already issued for that account.** Tokens are stateless JWTs, so without that
counter every token issued before a reset would keep working until it expired.

### The last-administrator guard

Demoting or deactivating the final active administrator is refused with 409.
The check is about the **result, not the actor**: demoting yourself is fine
while somebody else can still administer. Without it one click locks everyone
out, and recovery means editing the database by hand.

To hand over administration: promote the new person, confirm they can sign
in, then demote the old account.

## Settings

Defaults live in `backend/app/config.py` and are overridden by environment
variables or the repo-root `.env` (template: `.env.example`). Names are the
field names upper-cased (`jwt_secret` -> `JWT_SECRET`). Settings are read when
the API starts; change them, then restart the API.

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
| `SALES_TAX_RATE` | `0.0635` | the rate a newly recorded purchase is taxed at. A fraction, not a percentage. A value outside 0-1 is refused when the API starts, so `6.35` typed for 6.35% cannot multiply every cost by 7.35 |
| `SALES_TAX_INCLUDES_SHIPPING` | `true` | whether shipping is part of the taxed amount |

**Both are copied onto each item when it is created, and never read again for
that item.** Tax paid is a historical fact, so a change governs purchases
recorded afterwards and rewrites none before. Every item keeps its own
`tax_rate` and `tax_includes_shipping`; the database computes `sales_tax` and
`total_cost` from them, and neither can be written directly.

A purchase charged no tax has `tax_rate` 0: the **No sales tax charged** box
on the item edit form, `tax_rate` through `PATCH /api/inventory/{id}`, or
`POST /api/inventory/bulk` for a batch. Unticking the box restores the rate
the item was bought at, or, for an item recorded as untaxed, the configured
one. A rate cannot reproduce a marketplace's own rounding (Whatnot charged
$0.35 where 6.35% of $5.40 is $0.34); those pennies stay as computed.

### Images and the photograph library

| Setting | Default | Meaning |
|---|---|---|
| `MEDIA_ROOT` | `<repo>\media` | where image bytes live. **Bytes are never stored in the database**, so no database backup includes them. Back this up separately |
| `THUMBNAIL_MAX_PX` | `320` | longest edge of the small rendition |
| `WEB_MAX_PX` | `1600` | longest edge of the large rendition. Originals are never served |
| `MAX_UPLOAD_BYTES` | 25 MB | refused before anything is decoded |
| `MAX_IMAGE_PIXELS` | 50,000,000 | guards against a small file that decodes to gigabytes of pixels |
| `PHOTO_LIBRARY_ROOT` | `<repo>\photos` (git-ignored) | where `python -m app.photo_import` looks by default |

Changing the rendition sizes does not regenerate existing images.

## The collection record

**The `ccwebdb` database is the record.** The workbook it was first loaded
from is a historic reference and is not imported again. Data is improved in
the console or by the passes below, which fill what is empty and never
overwrite what a person set. A rebuild from the workbook would discard every
correction, receipt, photograph and sale made since, so it is never used to
fix live data; recovery from a data problem is a restore from a verified
backup (*Backing up and restoring*).

`scripts\ccweb_rebuild.cmd` exists for loading a **new** collection -- see
*A new collection* at the end.

### The passes over stored items

| Pass | Does |
|---|---|
| `python -m app.classifier_defaults` | fills note class, seal, signatures, Reserve Bank, composition and No Motto from the facts |
| `python -m app.series_match` | assigns a coin's series from the design its title or description names |
| `python -m app.series_classify` | assigns series from denomination and year, for coins the text left and all notes |
| `python -m app.serial_patterns` | derives star, radar, repeater and similar designations from a note's serial |
| `python -m app.rating_pass` | reads stored ratings again with the current rules |
| `python -m app.photo_import` | links photographs to items by filename |
| `python -m app.vendor_cleanup` | merges, renames, re-kinds or deletes purchase sources, by explicit instruction |

Run `classifier_defaults` before `series_classify`: note class is evidence for
series. Before running any pass with `--commit` on the live database, take a
verified backup and read the dry-run counts.

### Series

A nickname finds an item through its **series**, so an unclassified item is
found only if its own text happens to contain the word. Neither series pass
touches an item that already has a series -- a hand correction always stands.

`series_classify` assigns a design when the facts allow only one: a 1942 dime
is a Winged Liberty Head, a $1 Series 1963B a Barr Note. Its report lists by
item code what it leaves for a person:

- **boundary** -- a year two designs share (a 1916 dime is Barber or Mercury;
  a 1921 dollar Morgan or Peace), unless the text names one;
- **conflict** -- the text names only designs the facts rule out, such as a
  note rated "funnyback" but recorded as Series 1923. Only the item in hand can
  say which is wrong;
- **disagrees** -- a series already set that the facts rule out (a Kennedy
  half recorded as $1). Nothing there is changed; fix it by hand.

Designs that share a face value and years with a far commoner one -- Hawaii
and North Africa notes, commemorative halves and dollars, gold dollars,
American Innovation dollars -- are assigned only on evidence: text naming them
(for a note, also a brown or yellow seal). A piece that says nothing is taken
for the common design.

**Series 1929 National Bank Notes** share their series and brown seal with the
Series 1929 Federal Reserve Bank Notes. A note is filed as one when its text
names a national bank ("National", "Natl") or its class is National Bank Note
-- never when its class is Federal Reserve Bank Note. The class is filled
first, which is why `classifier_defaults` runs before `series_classify`.

A lot's pieces carry the lot's listing text, so a title and description shared
with another piece of the same order are not read as evidence about the
piece; only its rating is. If the lot's text names an evidence-only design,
the piece goes to the review list.

Designs, year ranges and nicknames are seeded from
`backend/data/reference/series.json`; `docs/specs/series-classification-design.md`
has the facts and their sources.

### Classifier defaults

Most classifiers follow from a few facts: a $1 note of Series 1957 is a Silver
Certificate with a blue seal, signed Priest and Anderson; a Federal Reserve
Note's serial names its Reserve Bank; a 1964 dime is 90% silver
(`docs/specs/classifier-defaults-design.md`).

| Filled | From | Facts |
|---|---|---|
| note class, seal, signatures | denomination, series year and letter | `note_issue.json` (Series 1928-2021) |
| Reserve Bank | a Federal Reserve Note's serial | BEP's serial rules |
| composition, metal, fineness, weights | denomination, country and year | `composition.json` |
| the No Motto attribute | a $1 Silver Certificate's series | `app/attribute_rules.py` |

**No Motto** ("Godless"): every $1 Silver Certificate of Series 1928 through
1935F lacks "In God We Trust", 1935G was printed both ways, and 1935H on
carries it. The first get the attribute (marked *read*); a 1935G is listed as
**needs evidence** unless its rating or a person already says No Motto; a
later note marked No Motto is listed as **disagrees**.

**A person always wins.** A filled-in value shows a *suggested* mark in the
item editor, with a tooltip saying where it came from. Change the field and
save, and the value is yours: nothing fills that field again. A value a person
or the import recorded is never replaced, but it narrows the facts: a $1
Series 1928 note recorded with a red seal is a United States Note.

Defaults are brought up to date as an item is created or saved (correct a
note's series year and its class follows), and the **New item** form suggests
them as you type. For everything already recorded, run the pass. Its report
lists by item code:

- **ambiguous** -- the series was issued in more than one class and nothing
  recorded says which;
- **disagrees** -- a recorded class, seal, signature, Reserve Bank or
  composition the facts rule out;
- **unknown issue** -- a series the facts table has no such note for, usually
  a mistyped series year;
- **serial prefix** -- a $5-or-higher note from Series 1996 on whose serial
  does not start with its series' letter.

A filled-in value the facts no longer support is withdrawn.

Note classes use BEP's names: United States Note ("Legal Tender Note" is an
alias), National Bank Note, Federal Reserve Note, Federal Reserve Bank Note,
Silver and Gold Certificate, Fractional Currency, Demand Note, Treasury Note.

### The rating pass

The rating -- the owner's own text, `grade_raw` -- often says more than was
first read. `app.rating_pass` reads it again with the current rules
(`app/importers/rating.py`, which the importer uses too):

| The rating says | Recorded |
|---|---|
| `69 PCGS`, `70DCAM PCGS` -- a number with no prefix | the grade, once something settles the strike: a P or SP prefix, a cameo (proof) or prooflike (business) designation, the same grade written in the description, or the description saying proof or uncirculated and not both |
| `SP68PCGS`, `P70DCAM`, `PR70DCAMPCGS` | a specimen; P70 as proof; a designation and grader run together |
| `UCAM`, `Ultra Cameo`, `DPL`, `FT`, `6FS` | DCAM, DMPL, Full Torch, six full steps |
| `Reverse PF70`, `Rev Proof` | strike type reverse proof |
| `First Strike`, `Early Release`, `First Release`, `FDI`/`FDOI` | release attributes |
| `CAC` | the CAC attribute; `CAC Gold` the gold one |
| `CACG`, or CAC with `First Delivery` | grader CACG, and First Delivery |
| `Genuine` | authenticity genuine, and the Genuine attribute |
| `No Motto`, `No God`, `Godless` | No Motto |

`FS` is Full Steps only on a Jefferson nickel.

**It fills what is empty.** A grade, strike, designation or grader already
recorded stays, as does anything confirmed in the editor or emptied on
purpose. What it fills carries the *suggested* mark ("Read from the rating"),
and an attribute it adds shows *read*. Two things it corrects rather than
fills, each listed: a strike the rating names outright (a stored proof the
rating calls a reverse proof), and FS on anything but a Jefferson nickel.

The report also lists **bare numbers with nothing to settle the strike** (a 67
is not MS67 by default) and **attributes for the other kind of item** (a note
rated FDOI). Every proposal is written to `rating_pass.csv` in the log
directory (`CCWEB_LOG_DIR`, default `logs`); `--csv` writes it elsewhere.

### Filing photographs: the photo import pass

`python -m app.photo_import` walks a directory (`--root`, default
`PHOTO_LIBRARY_ROOT`) and links each photograph to the item its filename
names, through the same writer (`app.image_links.attach`) the console uses.
The dry run decodes and validates every file, so it names one the imaging
layer would refuse, but writes no rows and no bytes. The first real run on the
safe-deposit photographs is a dry run the owner watches.

The filename convention is `<item_code>_<nn>.<ext>` (`app.photo_names`), for
example `CC-000412_01.jpg`. `01` is obverse and becomes the primary
photograph, `02` is reverse, anything past that is `unassigned`. Nothing
repairs a filename that misses the convention -- a lowercase `cc-` is a miss.

**Every file is stored; only the link is ever withheld** -- an import step
must never be the reason a photograph is lost. A photograph is stored
unattached and reported when its name does not match, its item code is
unknown, its item was deleted or split, two files in the run claim the same
slot (a collision links *neither*), or an earlier run already filled the slot.
An item that already has a primary keeps it: the `_01` is filed at sequence 1
but not primary, and reported under `primary`. A photograph linked onto an
item that is for sale is reported by item code. Unattached photographs are
filed by hand on the console's **Photos** page (`/owner/photos`).

## Finding items

The **Coins** and **Currency** screens (`/owner/inventory/coins`,
`/owner/inventory/currency`) share one search panel.

**The search box has no field syntax.** What you type is one term, matched
anywhere in an item's title, description, rating or item code, ignoring case.
It also matches the **name or alias of what the item is**: design series,
strike type, grade designation, attributes and, on coins, mint; on currency,
note class and serial features. So `mercury` finds Winged Liberty Head dimes
whose listings never say "Mercury", `denver` finds coins whose listings give
only the D, `legal tender` finds United States Notes, and `funnyback` finds
every classified $1 Series 1928 and 1934 note. A term under three letters must
be a whole name: `PR` finds proofs and `D` Denver, but `s` does not match
every strike containing an s. **Search tips**, under the box, lists examples;
clicking one runs it.

The rating is searched because it is often the only descriptive text an item
has: a Whatnot row's title is its denomination and its description a lot
number.

| Type | Finds |
|---|---|
| `1921 morgan` | several words are **one phrase, in that order** -- `morgan 1921` finds none |
| `morgan%1921` | `%` matches anything, so the words can be apart |
| `19_5` | `_` matches exactly one character: 1905, 1915 ... 1995 |
| `funny%back` | both `Funnyback` and `Funny Back` |

Text is matched as written: `ms65` and `ms-65` find different items.

The dropdowns and year boxes narrow whatever the search finds. A dropdown
reading **None recorded** is disabled because no matching item has that field
filled in. The **Item code** box, and on currency the **Serial number** box,
take the same `%` and `_` wildcards.

The **Grade** box takes a grade, not text:

| Type | Finds |
|---|---|
| `55` | exactly 55 -- not 55+ |
| `55+` | exactly 55+ |
| `55%` | 55 and 55+ |
| `BU`, `BU+`, `BU++` | 60-62, 63-64, 65-66 (pluses included) |
| `BU%` | all three: 60 to 66+ (`UNC` reads the same) |
| `MS65`, `PR69+`, `PR69%` | the number, with that strike |
| `AU`, `AU+` | 50 to 58+, or only the plus grades in it |
| `PROOF` | every proof |

Anything else is refused with the examples. The API's `grade_min` and
`grade_max` read the same terms: `grade_max=64` stops below 64+. The API also
takes `attribute=<code>` as a filter on either screen.

**Every field has an Alt+letter shortcut**, underlined in its label: Alt+S the
search box, Alt+H the tips, Alt+C clear, Alt+Y and Alt+O the years, Alt+P the
coins' **Strike type**. No field uses D, E or F, which the browser keeps. A
shortcut on a disabled dropdown does nothing.

## Inventory items

### How an item comes into being

| Path | When | Notes |
|---|---|---|
| **Entering an item on a purchase** (`POST /api/inventory`) | every new acquisition | requires a purchase order; see *Entering a purchase* |
| **Splitting a lot** (`POST /api/inventory/{id}/split`) | a bought lot becomes individual pieces | children inherit the parent's claims and a share of its cost (`equal`, or `relative` to a value per piece); the parent gets `split_at` and drops out of every count. There is no console screen for it yet |
| **The importer** (`app.importers`) | loading a new collection only | creates the item, its purchase order and its vendor together |
| **`python -m app.seed`** | a new, empty database | five demo items; never on real data |

Every path records an opening `item_status_history` row, so every item has a
lifecycle from its first row. Putting an owned item up for sale is a separate,
later step -- see *Offering an item for sale*.

**The item code is permanent.** It is drawn from a database sequence, assigned
once, never reused and never changed, and survives listing, sale, return and
relisting, because a returned item resumes its own history.

### How an item changes

| Endpoint | Use |
|---|---|
| `PATCH /api/inventory/{id}` | one item, any editable field |
| `POST /api/inventory/bulk` | the same change across many items, one transaction, all or nothing |
| `POST /api/inventory/receive` | record what arrived -- see *Receiving* |
| `POST /api/inventory/{id}/reviewed` | mark fields as confirmed by a person looking at the object |
| `PUT /api/inventory/{id}/errors` | replace the item's recorded errors -- see *Errors* |

**Editable scalars:** `source_title`, `description`, `year_start`, `year_end`,
`fineness`, `gross_weight_ozt`, `fine_weight_ozt`, `piece_count`, `item_cost`,
`shipping_cost`, `tax_rate`, `tax_includes_shipping`. The last two are NOT
NULL, and a null for either is refused naming the field.

**Editable classifiers**, set by code rather than id: `item_kind`, `country`,
`denomination`, `bullion_form`, `strike_type`, `grade`, `grade_designation`,
`grading_service`, `metal`, `series`, `storage_form`, `authenticity`,
`status`, `disposition`. Five are NOT NULL (`item_kind`, `storage_form`,
`authenticity`, `status`, `disposition`) and refuse a null or empty code.

**A grade is a number and a strike type.** `grade` takes `65` or `64+`; a
compound grade such as `MS65` or `PR69+` is split into the number and
`strike_type` (business, proof, specimen, reverse_proof,
enhanced_reverse_proof, sms), unless the request names a strike type itself.
Responses carry `grade`, `strike_type` and `grade_display` (`PR69+`).
Adjectival words are read at the bottom of their range: BU is 60, BU+ 63,
BU++ 65, PROOF PR63, AU 55.

An unknown field name is **refused**, not ignored -- a typo in a bulk edit
must not silently do nothing. Bulk edit does not set attributes: one set
applied to many items would wipe whatever each carried that the others do
not.

**Every console edit window** -- item, new item, order, platform, offer,
listing, record sale, lot, auction -- takes Alt plus the underlined letter to
jump to a field, and Ctrl+S or Ctrl+Enter to save. No letter is D, E or F,
which the browser keeps; a Save button is Alt+V; Escape closes a dialog.

### Status and location have one door

Everything that touches an item's lifecycle goes through
`backend/app/lifecycle_writes.py`:

| Function | Records |
|---|---|
| `record_initial_status` | the opening row, `from_status_id = NULL` |
| `set_status` | a transition, carrying the previous status |
| `set_location` | a move, with its `location_history` row |

`status_id` and `storage_location_id` are written **only** through these;
assign either anywhere else and the history silently stops being true. The
opening row is a separate function because `set_status` no-ops when the status
is unchanged, and a new item already has one.

Correcting a status through the console is itself recorded, with who did it
and when.

### Receiving

`POST /api/inventory/receive` records what a parcel contained. One request
covers one item or many, all or nothing.

Four outcomes: `received`, `missing`, `returned`, `canceled`. `missing` means
paid for, not cancelled, never arrived; a missing item can still be received
when it turns up.

- `arrived_on` is a **calendar date**, not a timestamp: a parcel that arrived
  on the 9th arrived on the 9th in every timezone. It is optional; a date more
  than one day ahead of UTC's today is refused.
- A storage location is recorded only for `received`, with its
  `location_history` row.
- Receiving something already received is refused with 409, naming its
  current status and arrival date, so a double submission is distinguishable
  from the wrong row.

**In the console** (`/owner/receiving`) there is one search form: part of an
order number (matched anywhere in it, any case) and/or what the item is --
kind, denomination, year, mint, serial, series year. By default it finds only
what has not arrived (`ordered` or `missing`); **Any status** shows a whole
order. Results come 200 at a time per kind and status; anything beyond that
is counted on screen, so narrow the search to see it. A link naming one order
(`/receiving?order=<id>`, from the inventory screens' order column or New
purchase's **Receive these**) opens with that order's header and its items
already found, searched by the order's id (an order number is not unique
across vendors and is sometimes not recorded).

Clicking a line opens a dialog for that one item: arrival date, storage
location, note, photographs, the four outcome buttons, the "Confirm or correct
fields" pane, the errors panel and, for a banknote, the Friedberg lookup.
After each receipt the search repeats, so what arrived drops off the list. The
endpoint takes a list, but the console sends one item per dialog: that is the
trade for the panel always being on screen, where a panel rendered under a
long table sat below the fold and clicking seemed to do nothing.

The **arrival date and storage location carry to the next item**, so a parcel
of twenty into one box is picked once. The **note does not carry**: it
describes one object ("corner bent"), and repeating it would record a fact
about a coin nobody checked.

If a **photograph fails to upload** the receipt still stands -- the arrival is
the fact, the photograph evidence added to it -- and the dialog stays open
naming the file that failed. Add the photograph afterwards from the item
editor's Photos panel.

Receiving only moves an item forward. A mistaken receipt is corrected from
the item editor's status field, which writes a history row like any other
transition.

### Optimistic concurrency

`inventory_item`, `listing`, orders, sales lots, auctions and platforms carry
a `version`. An update sends the version it read, and the write is refused
with 409 if somebody changed the row first. PostgreSQL's MVCC does not give
this: without it, the second of two people saving the same item silently
overwrites the first with values loaded before the change.

### Changing an item that is for sale

An item is *for sale* while an active listing with stock offers it, or an
order that has not shipped (pending, paid or packed) holds it. The item editor
says so at the top, naming the listing or order, and Save stays disabled until
**Change it anyway** is ticked. The API refuses such a save with 409 unless it
carries `acknowledge_for_sale`; bulk edit refuses the whole selection, naming
the items, and then offers **Change the items for sale too**. Saving nothing
needs no confirmation. Once an order ships, the item is ordinary again.

The same 409-unless-acknowledged rule guards: a receipt whose outcome is not
`received`; splitting a listed lot (an item already in an order refuses
unconditionally, since the split cannot be made at all); recording errors;
attaching, re-linking or deleting a photograph (`/api/images`,
`/api/image-links`); and merging a vocabulary value that moves it (the merge
preview names up to ten such items). Editing the listing itself
(`PATCH /api/listings/{id}`) is not affected. See
`docs/specs/for-sale-guards-design.md`.

### Removing an item

`DELETE /api/inventory/{id}` is a **soft delete** meaning *this row should
never have existed* -- a typo, a duplicate. Something sold, lost or given away
changes its `disposition` or `status` and keeps its history.

Two cases are refused, because both would fail silently:

- **A lot with pieces.** It holds the cost basis its children were allocated
  from.
- **An item that has ever been listed**, ended listings included. Order
  history references the listing, and nothing removes a listing row, so this
  is permanent.

Deleting twice is not an error. `DELETE /api/inventory/{id}/parent` detaches a
split child from its parent, for when the lineage itself was wrong.

## Entering a purchase

**New purchase** (`/owner/purchases/new`) is the one door for an acquisition:
no item is entered outside a purchase, so a standalone buy is a purchase
holding one item (`docs/specs/entry-panels-design.md`).

- **Vendors** are picked from a list (`GET /api/vendors`) or added inline
  (`POST /api/vendors`: name, kind, web address). Names are unique,
  case-insensitively.
- The purchase (`POST /api/purchase-orders`) needs only a vendor; the order
  number is optional, so a walk-in or show purchase needs nothing else. Vendor
  and order number together must be unique; the date, if given, must be no
  later than tomorrow.
- **Items** are entered on the purchase (`POST /api/inventory`) as `ordered`,
  or `received` for something already in hand. A **lot** is an item with a
  piece count above 1.
- **Tax fields** are per item, pre-filled from the purchase's controls: a rate
  that starts empty (the configured default), "No sales tax charged" (rate 0),
  and "Tax on shipping" (As configured / Taxed / Not taxed). An explicit rate
  must be 0-1 with up to four decimal places; `.0635` is accepted.
- **Save and add another** keeps `item_kind`, `status`, `country`,
  `denomination`, `series`, `series_year`, `series_letter`, `seal_color`,
  `fed_district`, `note_type`, `grading_service`, `metal`, `mint` and the
  purchase-wide tax defaults, and clears the rest (piece count back to 1).
- **Receive these** opens Receiving for that purchase; **Start another
  purchase** returns to step one.

The help band at the bottom of the console window explains whichever field
has focus, on this form and every other. All four endpoints above are
administrator-only.

## Orders

**Orders** lists every order, newest first: customer, lines at the price
paid, total, platform and status (`GET /api/orders`). The shop's **Your
orders** page is only the signed-in person's own (`GET /api/orders?mine=true`)
and has no status control; order administration lives in the console alone.

Status is changed from the order's row (`PATCH /api/orders/{id}`) through
pending, paid, packed, shipped, delivered, cancelled, refunded.

**Cancelling is one way.** Cancelling an unshipped order returns its stock to
the catalogue, after which the order cannot move to any other status (409):
allowing it would leave an order standing on stock already offered to the
next buyer. Place a new order instead. Cancelling after packing or shipping
returns no stock; re-sending `cancelled` is harmless.

Two kinds of unshipped order **cannot be cancelled**, because the listing they
sold has already ended and there is nothing to put the stock back on: a sale
recorded from an outside platform, and an order that bought a **sales lot**.
The API refuses with a 409 naming what is in the way; the Orders page greys
out **cancelled** on an outside-platform order. Once such an order has shipped
it can be cancelled normally (no stock returns), which is how a refund is
recorded. There is no "undo an outside sale" path: if one falls through,
restore the item's status and disposition by hand and offer it again.

**Placing an order for a customer.** **New order** opens an editor that
searches customers (and accounts with no customer record yet) and the
catalogue, and saves with `POST /api/customers/{id}/orders`. Prices default to
the listing's current price and can be overridden line by line.

**Revising a pending or paid order.** The same editor, from the order's
**Edit** button, sends the whole desired contents to `PUT /api/orders/{id}`,
because stock moves by the difference between old and new lines, all or
nothing. The request carries the order's `version`; a save over someone
else's change is refused and the editor asks for a reload. Orders past `paid`
cannot be revised.

**`payment_adjustment_due`** flags a paid order whose total changed after
payment, until someone handles the refund or extra charge outside the system;
nothing in ccwebdb moves money.

**History.** Every save -- placement and each revision -- is recorded: who,
and for a revision what changed line by line and the total's old and new
value. **History** on a row calls `GET /api/orders/{id}/changes` and groups
one save's rows into one entry. Order notes and who entered an order are
console-only.

**Each sale keeps the item as it was sold.** When a line is made -- checkout,
an order placed for a customer, a revision adding a line, a recorded outside
sale or a settled auction -- it copies the item and its listing into
`sales_order_item.item_snapshot`: title, description, year, denomination,
series, grade with strike, designation and grader, mint, certificates,
attributes, metal and weights, note details, costs, and the listing's title,
description and price. Correcting the item later, or reselling it after a
return, never changes that copy; a quantity or price change to an existing
line keeps it. The copy holds costs, so only the console sees it. The item
editor lists an item's **Sales** (`GET /api/inventory/{id}/sales`), each
"sold as" it was then.

The copy says which shape it is: `snapshot_version` 1 always has `item`;
version 2 has `item` for a single coin *or* `lot` (number, title,
description) and `items` (one entry per member) for a sales lot, never both.
Older copies are never rewritten; a reader asks for `lot` first. The lot half
is the only lasting record of which coins a sold group held.

## Selling

`docs/specs/selling-design.md` is the design. `app.offering_writes` is the
only code that changes a listing's status, the claim recording where an item
is offered, or a sales lot's status.

### Sales platforms

**Platforms** (`/owner/platforms`) lists every platform the business sells
through -- the web store, eBay, Whatnot, an auction house -- with its kind, an
optional link to the purchase source of the same name, account handle,
listing-link template and default fees.

The **web store platform** is created by the migration and every store listing
and order names it. Its kind cannot be changed and it cannot be retired --
checkout and the public catalogue are defined by it -- so the console hides
those controls and the API refuses both with 422. Every other platform is
added here and may be retired.

**Default fees** -- commission and processing rates, a fixed processing
charge, a per-listing fee -- are estimates for pricing an item before it
sells. **Nothing is seeded**: terms differ by account and category and change,
so an administrator enters them from their own account with the date they
were read (**Fees as of**), shown beside every estimate. Rates are typed as a
percentage (`13.25`) and stored as a fraction (`0.1325`). A fee of zero shows
as `0%` or `$0.00`; a blank means nobody has looked the terms up.

### Cleaning up purchase sources

A platform links to a `vendor` row as its purchase source, and the importer
created one vendor per spelling it met. `app.vendor_cleanup` tidies them, each
change named on the command line -- which vendors are the same business is
the owner's call:

```cmd
python -m app.vendor_cleanup
python -m app.vendor_cleanup --merge <from>:<into> --kind <id>:<code> --rename <id>:<name> --delete <id> --commit
```

`--merge` moves the purchase orders from one vendor onto another and removes
the duplicate; it is refused if both used the same order number.
`--kind` sets `vendor_kind`. `--rename` applies after the merges. `--delete`
removes a vendor with no purchase orders. Removing a vendor a platform names
as its purchase source is refused, naming the platform: unlink it on the
Platforms page first. Each option may be repeated.

### Offering an item for sale

From the **Coins** or **Currency** screen select items and choose **Offer for
sale...** in the bulk bar; from one item's editor, its **Offers** panel has
the same button. The panel hides the button only while the item is held --
active or paused -- by a listing on a platform other than the web store.

**Moving an item from the shop to another platform is one step**: offering an
item that is active in the web store pauses that store listing, and it resumes
when the new offer ends. The dialog takes one platform and one format (fixed
price or auction) with a row per item: price, title and description
(pre-filled), optional listing number, and cost, estimated fees, net and
margin for reference. **Offer** submits the batch to `POST /api/offers`. A
refusal -- already offered on another platform or already in the shop,
deleted, not received, split, or the platform retired -- names every affected
item inside the still-open dialog with the typed prices kept, and writes
nothing.

**Listings** (`/owner/listings`) shows every offer -- active and paused by
default, or **All, including ended** -- filterable by platform and format,
with price, cost, margin, status and a link to the platform's own page.
**Edit** (active rows only) changes price, title, description or listing
number (`PATCH /api/listings/{id}`) and nothing else. The item editor's
**Offers** panel shows the same rows for one item, ended ones included.

**A paused listing** is a store listing set aside because its item was offered
elsewhere. It keeps its price, is not for sale and does not appear in the
public catalogue; the Listings page names the offer that caused the pause. It
resumes on its own, at its old price, when that offer ends.

**Ending an offer.** **End** (Listings page, active and paused rows; the
Offers panel, active rows only) confirms, naming listing, item and platform,
then calls `POST /api/listings/{id}/end`. This is a withdrawal: the listing is
`ended` and any store listing it paused resumes. Ending is not reversible;
offering again makes a new listing.

**Recording a sale.** **Record sale...**, on active rows of a platform other
than the web store, enters a sale that happened elsewhere. The dialog takes
the sale price, the buyer's username on that platform (blank where the
platform does not name buyers), the platform's order number, and the fees
actually charged, one row per kind (commission, processing, listing, shipping
label, promotion, other), showing gross, fees, net and margin.
`POST /api/listings/{id}/sale` records the order (buyer matched or created,
price, fees, per-item shares) and ends the listing as sold in one
transaction. A store listing it had paused ends too rather than resuming. The
order appears on Orders already `paid` (or `delivered` for an auction house).

A shop item sells through checkout; an in-person sale of one is an order
placed for the customer. Whether **Record sale...** should also be offered on
store listings is an open decision in `docs/specs/selling-design.md`.

### Sales lots

A **sales lot** is a group of coins offered and sold as one thing -- three
Morgan dollars in one eBay listing, a type set in the web store. The coins
stay individually owned, costed and reportable throughout; the lot exists only
as long as the offer does. It is unrelated to a **purchase lot** (how
something came in, permanent); a coin can be in one of each.

**Putting a lot together.** On the **Coins** or **Currency** screen select
coins and choose **Group into lot...**: start a new lot with a title, or add
to a lot still assembling. The whole selection goes in, including any part off
the current page. **Sales lots** (`/owner/lots`) shows each assembling lot's
coins with cost and value, the group's running cost basis and value,
**Remove** per coin, **Edit wording...**, **Discard...** and **Offer for
sale...**.

The group's **value is a floor**: an unvalued coin contributes nothing, so the
page says how many coins are **Not yet valued**.

**What cannot go in:** a coin deleted, split, not received, already sold or
shipped, already in another open lot, or not a *whole* item to claim (a
listing offering more than one unit of it, or an unshipped order holding
units). Each refusal names the coin.

**Offering a lot** uses the same dialog as a single item, with one price for
the group. Any coin that cannot be offered refuses the whole lot. Offering
**freezes** the lot's title, description and membership. An empty lot cannot
be offered. Members' web store listings are paused, and come back if the lot
is dissolved. A coin in an offered lot cannot be offered on its own anywhere,
the web store included; end the lot's offer first.

**Ending a lot's offer dissolves the lot**: the listing ends, every coin is
released, and paused store listings resume. Nothing brings a dissolved lot
back. **Re-offer as a lot** on a dissolved row copies its title, description
and coins into a new assembling lot, and says if a coin has since been offered
on its own.

**A sold lot** (by **Record sale...** or a shop checkout) is marked sold,
releases its members, ends rather than resumes their paused store listings,
and files every coin as sold. The order has **one line** for the lot and
behind it **one share per coin**, each coin's part of the money and fees
divided by cost basis, which keeps per-coin gain answerable. Each member's
**Sales** list shows the lot and that coin's share.

A lot once offered is never deleted; the **Offered, sold and dissolved** table
keeps it, as the record of which coins went out together. Only a lot never
offered can be discarded. An order that bought a lot cannot be cancelled
(see *Orders*); if the sale falls through, restore the coins by hand and group
them again.

**In the shop**, a store lot is one card and one detail page with one price
and an **Add to cart** of one. It has no grade, year, country or metal, so
filters on those never match it, though text search finds its title. Its
picture is one of its coins, captioned as such, and its page lists the coins
in the lot, still after it sells. Its piece count is the **sum** of its
members'.

### Auctions

**Auctions** (`/owner/auctions`) runs an auction from draft to settled
(`app.auctions`, the only writer of `auction` and `auction_lot`). An auction
belongs to a platform (a live auction, an auction house, or a marketplace for
a single timed auction) and moves `draft` -> `scheduled` -> [`consigned`] ->
`closed` -> `settled`, or `cancelled`.

- **Add lot** offers an assembling sales lot, or a single item as a lot of
  one, in auction format, with the same refusals and pausing as any offer. Lot
  numbers and reserves are editable while the auction is draft, scheduled or
  consigned. Removing a lot ends its listing and deletes its row, so the
  number is free again.
- **Mark consigned** (auction houses only) moves every member item to that
  house's `consigned` storage location, created on first use.
- **Close** allows results to be entered; the settlement grid takes each
  lot's result (sold, unsold, withdrawn), hammer price and buyer, fees per
  buyer order, and for an auction house the location returned items go to.
- **Settle** applies it in one transaction: one order per buyer, with fees and
  per-coin shares; unsold and withdrawn lots dissolve and their store listings
  resume.
- **Cancel**, and removing a lot while the items are at the house, require a
  return location.

The `consigned` storage location kind is reference data loaded by
`python -m app.seeding load`, not by the migration; without it consigning
fails naming the missing code.

## Reference vocabularies

Classifiers -- grades, mints, denominations, metals and the rest -- are rows
in reference tables, not free text. Values are added from the dropdown where
they are needed, and renamed, retired, merged or reordered on the
**Vocabularies** page (`/owner/vocabularies`), which does not create values.

- **Rename** changes the label, which is what people read. The code never
  changes: saved searches, the data and the API use it. A renamed value is
  marked `manual`, so a later seed load keeps your wording.
- **Retire** stops a value being offered; every record keeps it, shown marked
  *(retired)*. **Restore** offers it again. A value the application looks up
  by code cannot be retired (409), though it can be renamed: every value of
  `item_status`, `disposition`, `sales_order_status`, `shipment_status`,
  `strike_type`, `item_kind`, `grade_scale`, `valuation_basis`,
  `authenticity`, `sales_venue_kind` and `sales_fee_kind`, plus vendor kind
  `unknown`, storage form `single`, currency `USD`, country `US`, note type
  `frn`, and image roles `obverse`, `reverse` and `unassigned`
  (`routers/reference.py`).
- **Merge into...** replaces a value with another for good. The page previews
  the effect: every item holding the old value moves to the kept one, the old
  label, code and aliases become aliases of the kept value (so ratings,
  searches and imports using the old word still find it), and the old value is
  deleted. The merge is remembered (`reference_merge`), so a seed load does
  not bring it back. A merge is refused when another vocabulary or a facts
  table uses the value; change those first, or retire instead. Values that
  cannot be retired cannot be merged away.

**Retire or merge?** Retire a value you no longer want offered whose records
are right. Merge a duplicate or mistake, so no record keeps it.

### What a picker offers, and in what order

**Order.** `GET /api/reference/{table}` returns values alphabetically by
label, case-insensitively, except nine tables ordered by `sort_order`:
`grade` (the scale's own order), `denomination` (face value, coins then
notes), the lifecycles `item_status`, `disposition`, `sales_order_status` and
`shipment_status`, `item_kind` (by how often each kind occurs),
`signature_combination` (chronological, narrowed to a note's series year) and
`sales_fee_kind` (the order a platform statement reads, "Other" last). The
order is decided once, in the API, so the shop, console and entry panels
agree.

**Fit.** `denomination.kind` (`coin` or `note`) and `applies_to` (`coin`,
`currency` or `any`) on `series`, `error_type` and `item_attribute` tell a
picker which values apply. `frontend/src/shared/kinds.js`'s
`fitsKind(entry, itemKind)` is the one place that mapping is written; every
picker that needs it passes it as its `filter`. A banknote's denomination
picker never lists a coin's, and its error-type picker never lists mint
errors.

**Adding a value while entering.** Every descriptive vocabulary's picker ends
with "+ Add a new value..." (`POST /api/reference/{table}`). Attributes and
error types ask for the label alone; the rest ask for a code and a label.
Where the label is enough:

- **The code is derived** -- lower-cased, punctuation to underscores
  ("Mismatched Serial" becomes `mismatched_serial`) -- and shown before
  saving.
- **If that code already names a value**, the existing one is selected rather
  than a duplicate created.
- **The kind is inferred** from the item being entered, so the new value is
  offered by the same picker; a value with no marker would fit no kind and
  vanish from its own list.
- **An attribute also asks for its group** (Serial, Variety, Release,
  Qualifier, Verification): the column is required with no default, and the
  owner chose to be asked rather than have one picked.

A value added this way is marked `manual`, which keeps it distinct from the
shipped catalogue and out of an export by default
(`python -m app.seeding export --out <dir>`, whose `--source` defaults to
`seeded`).

### Provenance and reference data

Every reference row records how it came to exist: `seeded` (shipped),
`derived` (inferred by the importer), or `manual` (typed by a person). A
machine guess must never be indistinguishable from a curated fact. Review
`derived` rows before anyone exports them.

**Before adding reference data, check it is free to use.** The catalogue is
sold, so reference data shipped inside it is redistributed. Facts are safe --
who held an office and when, design series names and year spans, mint
specifications, legislated compositions, common collector nicknames. A
publisher's *arrangement* is not: Friedberg numbering, Pick numbering,
price-guide values, or any catalogue's mapping of attributes to its own
numbers. See the Reference data section of `CLAUDE.md`.

### Other names (aliases)

A value's label is the standard term -- DCAM, United States Note, Walking
Liberty Half Dollar. What people write is an **alias**: UCAM, Legal Tender,
Walker. The Vocabularies page lists and edits every vocabulary's aliases, and
an alias works at once:

| Where | What it does |
|---|---|
| The search box | finds items of that value |
| The importer and rating pass | read the alias as the value, and the importer reports each (`aliased  : note_type: Legal Tender -> us_note (3 rows)`) |
| Dropdowns | a long list has a **Find** box; typing an alias offers the value with the alias in brackets, and Enter picks the first |

**Two values may share an alias** ("Cartwheel" is any large silver dollar).
Search finds both; the importer, which cannot choose, uses neither. The
console marks a shared alias. **An alias may not be a value's own label or
code**, since those are matched first.

**Removing a shipped alias retires it**, because seed loads only add and a
deleted one would come back; a retired alias is shown struck through and a
click restores it. An alias added in the console is deleted outright.

### Attributes

An item's **attributes** say what it is beyond its grade, any number of them:
a note can be a Star Note, a Fancy Serial and No Motto; a coin First Strike
and CAC. The item editor lists them under **Attributes**; × removes one and
the picker adds one, offering only attributes for that kind of item. They are
also editable in Receiving's "Confirm or correct fields" pane.

An attribute marked **read** was found by a rule (the serial, the rating, the
series) rather than set by a person. **Removing one keeps it removed**: no
rule adds it back. Setting it again restores it. Search finds items by an
attribute's name or alias (`godless`, `first strike`).

### Errors

`item_error` records mint and printing errors: an item may carry any number,
each with its own free-text note ("miscut at 3 o'clock, 4mm"), but not the
same type twice. `GET` and `PUT /api/inventory/{id}/errors` read and replace
the whole set.

One panel is mounted in three places:

| Where | Saving |
|---|---|
| The item editor, beside Attributes | its own `PUT`, independent of Save, so an error is neither held back by nor lost to a discarded edit |
| **New item** | held on the form; sent once the item is created |
| **Receiving**, in the item's dialog | its own `PUT`, once the item's record has loaded (its kind decides which types are offered) |

The type picker offers a note the currency error types and those that apply
to both, never the coin ones, and never a type already recorded on the item.

**On New item, the item is created first and its errors saved second.** If
the errors call fails, the form says so -- the item was created, its errors
were not -- keeps the rows and the new item's code on screen, and offers
**Retry**. Save stays disabled until the retry succeeds, so the same piece
cannot be entered twice.

## Backing up and restoring

The database is the record. The workbook is not a backup; it has not
described the collection since the import. **Photograph bytes are in no
database backup** -- back up `MEDIA_ROOT` separately.

There are two kinds of backup:

- **`pg_dump`** copies the database as it is, `alembic_version` included, to
  a file that can leave the machine. It is **the** backup before a migration.
  Dumps are kept in `%USERPROFILE%\dev\ccwebdb-backups\`, outside the
  repository.
- **`python -m app.backup`** copies the whole database into another database,
  building the schema from the SQLAlchemy models. It is portable (another
  engine is a different `--to` URL) and suits a working copy beside live. It
  is **not** a pre-migration backup: its schema comes from the *current
  models* and it carries no `alembic_version`, so once new code is checked out
  its copy already has the new tables and cannot be migrated.

```cmd
python -m app.backup                      copy to a timestamped ccwebdb_bak_* database
python -m app.backup --name before_split  copy under a name you choose
python -m app.backup --to <url>           copy anywhere SQLAlchemy reaches
python -m app.backup --list               what copies exist, and their size
python -m app.backup --verify <name>      compare a copy against the live database
```

**Always verify; `--list` is not evidence.** A copy that aborted partway still
lists at a plausible size, and one such copy held no inventory items and no
purchase orders at all. `--verify` compares every model table's row count and
prints `<name> matches the source on every table`, or the tables that differ
and exits non-zero:

- **Row counts that disagree.** For a copy taken today, any disagreement is a
  failed backup. An older copy is expected to differ: it records the
  collection as it was.
- **`OLDER SCHEMA -- ... has no <table>`.** The copy predates a migration. It
  records its own moment but cannot be compared table for table.

### Restoring

A restore is always **into a new database**, never over the live one, so the
original stays untouched until the replacement is checked and switching back
is a one-line edit.

From an `app.backup` copy, point `DATABASE_URL` at the copy and copy it again:

```cmd
set "DATABASE_URL=postgresql+psycopg://ccwebdb:<password>@localhost:5432/<copy name>"
python -m app.backup --name ccwebdb_restored
```

From a `pg_dump` file, create a database and `pg_restore` into it as in step 1
below. Either way, check the result, then set `DATABASE_URL` in `.env` to the
new database and restart the servers.

## Applying a schema release

A release that adds a migration is applied to the live database only after it
has been rehearsed on a restored copy of that database. `scripts\ccweb_psql.cmd`,
`pg_dump` and friends connect with the standard `PG*` variables; set the
password in each window you use.

1. **Back up with `pg_dump`, and prove the dump by restoring it.** From the
   repo root, with the code that live is running still checked out:

   ```cmd
   set "PGBIN=%CONDA_PREFIX%\Library\bin"
   set "PGHOST=localhost"
   set "PGUSER=ccwebdb"
   set "PGPASSWORD=<password>"
   "%PGBIN%\pg_dump.exe" -Fc -d ccwebdb -f "%USERPROFILE%\dev\ccwebdb-backups\ccwebdb_pre_release_YYYYMMDD.dump"
   "%PGBIN%\createdb.exe" ccwebdb_rehearsal
   "%PGBIN%\pg_restore.exe" -d ccwebdb_rehearsal --no-owner "%USERPROFILE%\dev\ccwebdb-backups\ccwebdb_pre_release_YYYYMMDD.dump"
   cd backend
   python -m app.backup --verify ccwebdb_rehearsal
   ```

   `PGBIN` is PostgreSQL's binaries inside the active `ccwebdb` environment,
   the same place `scripts\ccweb_env.cmd` finds them. Every table's row count
   must agree. `--verify` can compare them only while
   the checked-out models match live; with the release's code checked out it
   reports the new tables as missing. If the counts do not agree, the dump is
   not usable and nothing is applied.

2. **Rehearse on the restore.** With the release's code checked out, point
   both the application and psql at the rehearsal database, record the item
   count and cost basis, migrate and seed, and record them again:

   ```cmd
   set "PGDATABASE=ccwebdb_rehearsal"
   set "DATABASE_URL=postgresql+psycopg://ccwebdb:<password>@localhost:5432/ccwebdb_rehearsal"
   ..\scripts\ccweb_psql.cmd -c "select count(*), sum(total_cost) from inventory_item where split_at is null and deleted_at is null;"
   python -m alembic upgrade head
   python -m app.seeding load
   ..\scripts\ccweb_psql.cmd -c "select count(*), sum(total_cost) from inventory_item where split_at is null and deleted_at is null;"
   ..\scripts\ccweb_psql.cmd -c "select version_num from alembic_version;"
   set "DATABASE_URL="
   set "PGDATABASE="
   ```

   The two results must be identical, and `alembic_version` must read the new
   head. Clear both variables before going on -- they are what point the
   commands at the rehearsal rather than at live -- and drop
   `ccwebdb_rehearsal` afterwards (`"%PGBIN%\dropdb.exe" ccwebdb_rehearsal`);
   it is a full copy of the collection. Schema first, reference data second is
   the standing order.

3. **Stop the servers, keeping PostgreSQL**, so nothing writes while the
   schema changes:

   ```cmd
   scripts\ccweb_shutdown.cmd /keepdb
   ```

4. **Upgrade and seed live**, from `backend\`, with `DATABASE_URL` pointing
   at `ccwebdb` (the `.env` default):

   ```cmd
   python -m alembic upgrade head
   python -m app.seeding load
   ```

   Reference data a feature needs is loaded by the seed step, not the
   migration, so skipping it leaves the feature failing on a missing code.

5. **Check:** `alembic_version` reads the new head; the item count and cost
   basis match step 2's; every table the migration added is empty; the shop
   catalogue (`GET /api/catalog`) and the console answer once restarted.

6. **Restart** from a shell nothing else depends on:

   ```cmd
   scripts\ccweb_shutdown.cmd
   scripts\ccweb_startup.cmd
   ```

   The backend runs uvicorn without `--reload`, so until it restarts it serves
   the code that was running before, whatever the database's schema. Restart
   only once the release's branch is merged into `main` and checked out.

## A new collection

`scripts\ccweb_rebuild.cmd [<workbook>]` builds a separate database,
`ccwebdb_rebuild`, and never touches `ccwebdb`. In order: drop and create
`ccwebdb_rebuild`, `alembic upgrade head`, `app.seeding load`, the importer
(`app.importers.cli --commit`), `series_match`, `classifier_defaults`,
`series_classify`, `serial_patterns`, then `app.seed` last -- run earlier, its
demo items would take the first item codes and offset every real one. The
workbook defaults to `%USERPROFILE%\OneDrive\wnm3_coins.xlsx`.

The importer's rules are specific to that workbook's layout
(`docs/spreadsheet-import-design.md`); a collection in a different layout needs
its own profile. The importer can be run on its own as a dry run that touches
no database: `python -m app.importers.cli --file <path>` writes review CSVs to
`logs\import\`. For the existing collection none of this is a way to fix data
-- see *The collection record*.

### The canonical spreadsheet

The historic workbook is exactly `C:\Users\wnmil\OneDrive\wnm3_coins.xlsx`.
**Other files on this machine have the same name** -- under
`OneDrive\Documents\`, `OneDrive\Documents\coins\`, `OldDocuments\`,
`OldDocuments\coins\` and a phone download under `CrossDevice\`, among others
-- and all of them are stale snapshots, some only weeks older. `OneDrive\` also holds timestamped `wnm3_coins.backup-<date>-<time>.xlsx`
copies, so a glob such as `wnm3_coins*.xlsx` matches several files. Match the
exact name, never a prefix. If the path is ever in doubt, confirm modification
dates rather than assuming: OneDrive sync can restat a file it did not change.

The workbook has no plate numbers and no structured errors, and its `Taxes`,
`Total Cost`, `Profit` and `Profit %` columns are headers over nearly empty
cells; the importer reads only `Price` and `Shipping`.

## Things that are deliberately not configurable

- **Per-permission roles.** Two roles, one boundary.
- **Password reset by email.** No mail configuration exists.
- **Editing an account's email.** It is the account's identity.
- **Deleting a user.** Deactivate instead -- history rows reference the user
  id, and `ON DELETE SET NULL` on those columns exists so that removing a user
  never erases the record that the work was done.
- **A storage location customers can see.** This is an
  authorisation boundary: a public listing that leaked the safe-deposit box
  holding an item would be a security failure. It is enforced by
  `routers/catalog.py`, which builds every public response field by field, and
  by the test asserting the result carries no location -- not by the
  `public_catalog` view, which forbids the columns but which no endpoint reads.
- **Creating an ordinary storage location in the console.** The receiving
  picker lists existing locations (`GET /api/storage-locations`); only
  consignment creates one.
