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

- **Self-service registration** (`POST /api/auth/register`, the shop's
  **Register** page) always creates a `customer`. There is no way to register
  as an administrator.
- **An administrator creates it** (`POST /api/users`, **People → Accounts →
  New account** in the console): email, name, role and an initial password.
  Either role may be chosen and the role has no default in the API, so an
  administrator is never created by omission. Administrators only -- 401
  signed out, 403 for a customer. There is no mail configuration, so pass the
  password on out of band, as with **Set password** below. An email already
  in use is refused with 409.
- **Promotion**: an existing administrator changes an account's role in the
  console under **People**.
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

### Finding items

The **Coins** and **Currency** screens (`/owner/inventory/coins`,
`/owner/inventory/currency`) share one search panel.

**The search box has no field syntax.** Whatever you type is one term, matched
anywhere in an item's title, description, **rating** (the spreadsheet's
`Rating` text, as written) or item code, ignoring case. It also matches items
by the **name or alias of what they are**: design series, strike type, grade
designation and, on coins, mint; on currency, note class and serial features.
So `mercury` finds Winged Liberty Head dimes whose listings never say
"Mercury", `denver` finds coins whose listings give only the D, `legal tender`
finds United States Notes, and `funnyback` finds every $1 Series 1928 and 1934
note -- once those items have been classified (below). A term under three
letters must be a whole name: `PR` finds proofs and `D` Denver, but `s` does
not find every strike with an s in it. **Search tips**, under the box, lists
examples for each screen; clicking one runs it.

The rating is searched because it is often the only descriptive text an item
has: a Whatnot row's title is its denomination and its description a lot
number, and every "funnyback" note carries that word only in its rating.

Three things about the box are not obvious:

| Type | Finds | Why |
|---|---|---|
| `1921 morgan` | 27 items | several words are **one phrase, in that order** -- `morgan 1921` finds none |
| `morgan%1921` | 8 | `%` matches anything, so the words can be apart |
| `19_5` | 278 | `_` matches exactly one character: 1905, 1915 ... 1995 |
| `funny%back` | 48 notes | the sheet spells it `Funnyback` (18) and `Funny Back` (30) |

Text is matched as written: `ms65` and `ms-65` find different items (128 and
16). Counts are from the collection on 2026-09-16.

The dropdowns and year boxes narrow whatever the search finds. A dropdown
reading **None recorded** is disabled because no matching item has that field
filled in -- a gap in the data, not in the filter. The **Item code** box, and on
currency the **Serial number** box, take the same `%` and `_` wildcards.

The **Grade** box takes a grade, not text:

| Type | Finds |
|---|---|
| `55` | exactly 55 -- not 55+ |
| `55+` | exactly 55+ |
| `55%` | 55 and 55+ |
| `BU`, `BU+`, `BU++` | 60-62, 63-64, 65-66 (pluses included) |
| `BU%` | all three: 60 to 66+ (`UNC` reads the same) |
| `MS65`, `PR69+`, `PR69%` | the number, shown with that prefix |
| `AU`, `AU+` | 50 to 58+, or only the plus grades in it |
| `PROOF` | every proof |

Anything else is refused with the examples. The API's `grade_min` and
`grade_max` read the same terms: `grade_max=64` stops below 64+.
Coins also have a **Strike type** dropdown (Alt+P).

**Every field has an Alt+letter shortcut**, underlined in its label: Alt+S for
the search box, Alt+H for the tips, Alt+C to clear the filters, Alt+Y and
Alt+O for the years (as in the item editor). No field uses D, E or F, which
the browser keeps for itself. A shortcut on a disabled dropdown does nothing,
since a disabled control cannot take focus.

### How an item gets its series

A nickname finds an item through its **series**, so an unclassified item is
found only if its own text happens to contain the word. Two passes classify,
both reporting by default and writing only with `--commit`, both run by
`scripts\ccweb_rebuild.cmd`, and neither ever touches an item that already
has a series -- a hand correction always stands:

| Pass | Reads | Classifies |
|---|---|---|
| `python -m app.series_match` | title and description | coins, by the design they name |
| `python -m app.series_classify` | denomination and year (for a note, series year and letter), then title, description, rating and seal colour as evidence | coins the text left, and all notes |

`series_classify` assigns a design when the facts allow only one: a 1942 dime
is a Winged Liberty Head, a $1 Series 1963B is a Barr Note. It leaves for a
person what the facts cannot settle, and its report lists each case by item
code:

- **boundary** -- a year two designs share (a 1916 dime is Barber or Mercury;
  a 1921 dollar Morgan or Peace), unless the text names one of them;
- **conflict** -- the text names only designs the facts rule out, such as a
  note rated "funnyback" but recorded as Series 1923. Either the text or the
  year is wrong, and only the item in hand can say which.

A third section, **disagrees**, lists items whose series is already set but
which the facts rule out -- a Franklin Pierce dollar filed as a Franklin half,
a Kennedy half recorded as $1. Nothing there is changed; fix the series or the
denomination and year by hand.

Some designs share their face value and years with a far commoner one --
Hawaii and North Africa notes, commemorative halves and dollars, gold dollars,
American Innovation dollars. They are assigned only on evidence: text naming
them (for a note, also a brown or yellow seal). A piece that says nothing is
taken for the common design, so an unworded commemorative half is filed as a
Kennedy.

**Series 1929 National Bank Notes** ($5-$100, nicknamed *Brown Seal*, *1929
National*, *Small Size National*) share their series and brown seal with the
Series 1929 Federal Reserve Bank Notes. A note is filed as one when its text
names a national bank ("National", "Natl") or its class is recorded as
National Bank Note -- never when its class is Federal Reserve Bank Note, even
if its text says "Brown Seal". The class is filled first (from a rating such
as "Fed Res Boston" or "First National Bank of ..."), which is why the
rebuild runs `app.classifier_defaults` before `app.series_classify`.

A lot's pieces are imported carrying the lot's listing, so a title and
description shared with another piece of the same order are not read as
evidence about the piece; only its rating is. If the lot's text names an
evidence-only design ("Lot of 3 Commemorative Half Dollars"), the piece goes
to the review list instead.

The designs, their year ranges and nicknames are seeded from
`backend/data/reference/series.json`; see
`docs/specs/series-classification-design.md` for the facts and their sources.
Run the report and read the counts before running with `--commit` on the live
database.

### Classifier defaults: what the facts fill in

Most classifiers follow from a few facts. A $1 note of Series 1957 is a Silver
Certificate with a blue seal, signed Priest and Anderson; a Federal Reserve
Note's serial names its Reserve Bank; a 1964 dime is 90% silver. The console
fills these in so nobody types them
(`docs/specs/classifier-defaults-design.md`):

| Filled | From | Facts table |
|---|---|---|
| note class, seal, signatures | denomination, series year and letter | `note_issue.json` (Series 1928-2021) |
| Reserve Bank | a Federal Reserve Note's serial number | BEP's serial rules |
| composition, metal, fineness, weights | denomination, country and year | `composition.json` |
| the No Motto attribute | a $1 Silver Certificate's series | `app/attribute_rules.py` |

**No Motto** ("Godless") follows from the series: every $1 Silver
Certificate of Series 1928 through 1935F lacks "In God We Trust", Series
1935G was printed both ways, and 1935H on carries it. The first get the
attribute (marked *read*); a 1935G is listed as **needs evidence** unless its
rating or a person already says No Motto; a later note marked No Motto is
listed as **disagrees**. Remove the attribute in the editor and it stays
removed. Correct the series and an attribute the rule added goes with it.

**A person always wins.** A filled-in value shows a small *suggested* mark in
the item editor, with a tooltip saying where it came from. Change the field
and save, and the value is yours: the mark goes, and nothing fills that field
again. A value a person or the spreadsheet recorded is never replaced -- but it
does narrow the facts: a $1 Series 1928 note recorded with a red seal is a
United States Note.

**When it happens.** As an item is created or saved, its defaults are brought
up to date: correct a note's series year and its class follows. The **New item**
form asks as you type: choose $1 and Series 1957 and the class, seal and
signatures appear, marked as suggestions. For everything already recorded:

    python -m app.classifier_defaults            report, touching nothing
    python -m app.classifier_defaults --commit   write the defaults

The report lists, by item code, what a person should look at:

- **ambiguous** -- the series was issued in more than one class and nothing
  recorded says which (a $1 Series 1928 with no seal);
- **disagrees** -- a recorded class, seal, signature or Reserve Bank the facts
  rule out;
- **unknown issue** -- a series the facts table has no such note for, usually
  a mistyped series year;
- **serial prefix** -- a $5-or-higher note from Series 1996 on whose serial
  does not start with its series' letter;
- **stale default** -- a filled-in value the facts no longer support.

Note classes use BEP's names: United States Note (formerly also listed as
"Legal Tender Note", now a nickname of it), National Bank Note, Federal
Reserve Note, Federal Reserve Bank Note, Silver and Gold Certificate,
Fractional Currency, Demand Note, Treasury Note. Searching any class name or
nickname finds the notes of that class.

### What the rating says: the rating pass

**The database is the record** (the owner, 2026-09-16). The collection
workbook is a historical reference; data is improved here, by passes over
the stored items, not by importing again -- a rebuild would discard every
correction made in the console.

The rating -- the owner's own text, `grade_raw` -- often says more than the
first import read. `app.rating_pass` reads it again with the current rules
(`app/importers/rating.py`, which new imports use too):

| The rating says | Recorded |
|---|---|
| `69 PCGS`, `70DCAM PCGS` -- a number with no prefix | the grade, once something settles the strike: a P or SP prefix, a cameo (proof) or prooflike (business) designation, the same grade written in the description (`PCGS MS69`), or the description saying proof or uncirculated and not both |
| `SP68PCGS`, `P70DCAM`, `PR70DCAMPCGS` | a specimen; P70 as PCGS's proof; a designation and grader run together |
| `UCAM`, `Ultra Cameo`, `DPL`, `FT`, `6FS` | DCAM, DMPL, Full Torch, six full steps |
| `Reverse PF70`, `Rev Proof` | strike type reverse proof |
| `First Strike`, `Early Release`, `First Release`, `FDI`/`FDOI` | release attributes |
| `CAC` | the CAC attribute (green sticker); `CAC Gold` the gold one |
| `CACG`, or CAC with `First Delivery` | grader CACG, and First Delivery |
| `Genuine` | authenticity genuine, and the Genuine attribute |
| `No Motto`, `No God`, `Godless` | No Motto |

`FS` is Full Steps only where the item is a Jefferson nickel.

    python -m app.rating_pass            report, touching nothing
    python -m app.rating_pass --commit   apply

**It fills what is empty.** A grade, strike, designation or grader already
recorded stays, as does anything confirmed in the editor or emptied on
purpose. What it fills carries the *suggested* mark ("Read from the
rating"), and an attribute it adds shows *read*. Two things it corrects
rather than fills, each listed in the report: a strike the rating names
outright (a stored proof that the rating calls a reverse proof), and FS
recorded on something that is not a Jefferson nickel.

The report also lists **bare numbers with nothing to settle the strike**
(`67 ANACS` on a "1961 Washington 25 Cents" -- a 67 is not MS67 by default)
and **attributes for the other kind of item** (a note rated FDOI). Every
proposal is written to `rating_pass.csv` in the log directory.

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
`country`, `denomination`, `bullion_form`, `strike_type`, `grade`,
`grade_designation`,
`grading_service`, `metal`, `series`, `storage_form`, `authenticity`,
`status`, `disposition`. Five of those are NOT NULL (`item_kind`,
`storage_form`, `authenticity`, `status`, `disposition`) and refuse a null or
empty code rather than failing with an unhandled database error.

**A grade is a number and a strike type.** `grade` takes `65` or `64+`; a
compound grade such as `MS65` or `PR69+` is split into the number and
`strike_type` (business, proof, specimen, reverse_proof,
enhanced_reverse_proof, sms), unless the request names a strike type of
its own. Responses carry `grade`, `strike_type` and `grade_display`
(`PR69+`). Adjectival words are read at the bottom of their range: BU is
60, BU+ 63, BU++ 65, PROOF PR63, AU 55.

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

**In the console** (`/owner/receiving`), receipts are recorded **one line at a
time**. Clicking a line -- the item code, or anywhere on its row -- opens a
dialog named for that item, holding the arrival date, storage location, note,
photographs, the four outcome buttons, the field-review pane, and the Friedberg
lookup for a banknote. It closes on success and the order is read again, so the
line reappears in its new status. An already resolved line is shown for context
and does not open.

The endpoint still takes a list and is still all-or-nothing; the console simply
hands it one id. Recording twenty items is twenty dialogs, which is the trade
accepted for the panel always being on screen: rendered under a long table it
sat below the fold, so clicking appeared to do nothing.

To keep that trade cheap, the **arrival date and storage location carry to the
next line** -- a parcel of twenty into one safe deposit box is picked once, not
twenty times. The **note does not carry**: a date and a location describe the
parcel, but a note describes the object ("corner bent"), and repeating one onto
the next item would record a fact about a coin nobody checked.

The dialog closes only when everything landed. If a **photograph fails to
upload** the receipt still stands -- the arrival is the fact, the photograph is
evidence added to it -- but the dialog stays open naming the file that failed,
rather than closing over an error nobody would ever see. There is no in-app
retry for that upload: the item is now `received`, so its line no longer opens.
Re-attaching it needs the status set back to `ordered` from the item editor.

An item's **status can be corrected from the item editor** (Alt+S on the edit
dialog), which is the only way back: receiving moves an item forward only. That
path writes a status-history row like any other transition, so the item's
history stays a true account including the correction.

Following `/receiving?order=<id>` -- what the inventory screens' purchase-order
column and New purchase's **Receive these** both link to -- shows **only that
order**, with no picker over every other one. **Choose another order** goes back
to the list. Without the parameter the picker is the way in, and **By item**
searches by attribute for when the object is in hand and its order is unknown.

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

## Entering a purchase

**New purchase** in the console (route `/purchases/new`) is the one door into
adding an acquisition: no item is ever entered outside a purchase, so a
standalone buy is recorded as a purchase holding one item. See
`docs/specs/entry-panels-design.md` for the full design.

- **Vendors** are picked from a list (`GET /api/vendors`) or added inline
  (`POST /api/vendors`: name, kind, web address). Names are unique,
  case-insensitively.
- The purchase itself (`POST /api/purchase-orders`) needs only a vendor --
  the order number is optional, so a walk-in or show purchase needs nothing
  else. A vendor and order number together must be unique; the date, if
  given, must be no later than tomorrow (to allow for time zones).
- **Items** are entered on the purchase (`POST /api/inventory`) with a status
  of `ordered` or `received` -- `received` for something already in hand.
  A **lot** is simply an item with a piece count above 1; splitting it into
  individual pieces is a later step.
- **Tax fields** are set per item, pre-filled from the purchase's own tax
  controls: a tax-rate field that starts empty (meaning the configured
  default rate), "No sales tax charged" (sends a rate of 0), and "Tax on
  shipping" (As configured / Taxed / Not taxed). An explicit rate is
  validated as 0-1 with up to 4 decimal places, including a leading-dot form
  such as `.0635`.
- **Save and add another** keeps `item_kind`, `status`, `country`,
  `denomination`, `series`, `series_year`, `series_letter`, `seal_color`,
  `fed_district`, `note_type`, `grading_service`, `metal` and `mint`, plus
  the purchase-wide tax defaults, and clears everything else (title,
  description, year, grade, grade designation, serial number, certificate
  number, variety, item cost, shipping cost, and piece count back to 1), so
  entering several notes from the same purchase does not mean retyping the
  shared details each time.
- **Receive these**, once items are entered, opens Receiving
  (`/receiving?order=<id>`) for that purchase, and **Start another purchase**
  returns to step one to pick or start a different one.

All four endpoints above (`GET /api/vendors`, `POST /api/vendors`,
`POST /api/purchase-orders`, `POST /api/inventory`) are administrator-only.

## Orders

**Orders** in the console lists every order, newest first: who placed it,
what is in it at the price paid, the total, and its status. `GET /api/orders`
gives an administrator all of them, each naming its customer and every line's
listing title.

The shop's **Your orders** page is only ever the signed-in person's own orders
(`GET /api/orders?mine=true`), an administrator's included, and has no status
control: order administration lives in the console alone, and the calls for it
are in `owner/api.js`, out of the shop's bundle.

Status is changed from the order's row (`PATCH /api/orders/{id}`, administrators
only), through the whole `sales_order_status` vocabulary: pending, paid,
packed, shipped, delivered, cancelled, refunded.

**Cancelling is one way.** Cancelling an order that has not shipped returns its
stock to the catalogue. After that the order cannot be moved to any other
status -- the API refuses with 409, and the console locks the row and asks
before cancelling. Allowing it would leave an order standing on stock already
offered to the next buyer, the same coins sold twice. Place a new order
instead. Cancelling after packing or shipping returns no stock, and re-sending
`cancelled` is harmless.

**Placing an order for a customer.** The console's **New order** button opens
an editor that searches customers (and accounts that have no customer row
yet) and the catalogue, and saves with `POST /api/customers/{id}/orders`.
Item prices default to the listing's current price but can be overridden line
by line, for the cases -- a phone order, a show sale, a price matched to
another dealer -- where the sale price is not what is currently listed.

**Revising a pending or paid order.** The same editor, opened from a pending
or paid order's **Edit** button, sends the whole desired contents to
`PUT /api/orders/{id}` -- not a patch of one field, because stock moves by the
difference between the old and new line items, all or nothing. The request
carries the `version` the order was loaded at; a save over someone else's
change in the meantime is refused rather than silently applied, and the
editor asks the user to reload. Orders past `paid` cannot be revised this
way -- packed, shipped and later stock has already left the shelf.

**`payment_adjustment_due`** is set when a paid order's total changes after
payment -- the console flags the order and shows the badge until someone
handles the refund or additional charge outside the system; nothing in
ccwebdb moves money.

**History.** Every save -- the initial placement, and each revision -- is
recorded as one or more rows: who placed the order, and for a revision, what
changed line by line and the total's old and new value. The console's
**History** button on a row calls `GET /api/orders/{id}/changes` and groups
the rows from one save into one entry, oldest first.

Order notes and who entered an order are visible only in the console, never
to the customer.

**Each sale keeps the item as it was sold.** When a line is made -- at
checkout, when an order is placed for a customer, or when a revision adds a
line -- it copies the item and its listing: title, description, year,
denomination, series, grade with strike, designation and grader, mint,
certificates, attributes, metal and weights, the note's details, costs, and
the listing's title, description and price (`sales_order_item.item_snapshot`).
Correcting the item later, or selling it again after a return, never changes
that copy; a resale is a new line with its own. A quantity or price change to
an existing line keeps its copy, since it is the same sale. The order's line
title is the one it sold under. The copy holds costs, so the console sees it
and a shopper does not. The item editor lists an item's **Sales** under the
form, each "sold as" what it was then (`GET /api/inventory/{id}/sales`).

**Changing an item that is for sale.** An item is *for sale* while an active
listing with stock offers it, or an order that has not shipped (pending, paid
or packed) holds it. The item editor says so at the top, naming the listing
or order, and Save stays disabled until **Change it anyway** is ticked. The
API refuses such a save with 409 unless it carries `acknowledge_for_sale`;
bulk edit refuses the whole selection, naming the items, and then offers
**Change the items for sale too**. Saving nothing needs no confirmation. Once
an order ships, the item is ordinary again -- its sale keeps its copy.
Editing the listing itself (**Manage**) is not affected. Receiving, splits,
recorded errors, images and vocabulary merges do not ask either.

Both edit windows -- an inventory item's and an order's -- take Alt plus the
underlined letter to jump to a field, and Ctrl+S or Ctrl+Enter to save.

## Sales platforms

**Platforms** (`/owner/platforms`, beside Vocabularies) lists every platform
the business sells through -- the web store, eBay, Whatnot, an auction house
-- with its kind, an optional link to the purchase source of the same name,
account handle, listing-link template and default fees.

The **web store platform is created by the migration**, not entered by hand:
every existing listing and order is pointed at it, so a listing has always
named a platform. It is the one row whose kind cannot be changed and which
cannot be retired -- checkout and the public catalogue are defined by it, so
the console hides those controls for it and the API refuses both changes with
422. Every other platform is added on this page and may be retired like a
vocabulary value.

A platform's **default fees** -- commission and processing rates, a fixed
processing charge, a per-listing fee -- are estimates only, for pricing an
item before it sells; recording what a sale actually charged is a later
phase, not part of what is built yet. **Nothing is seeded.** Platform terms
change and differ by account and category, so an administrator enters them
from their own account, along with the date they were read (**Fees as of**),
shown beside every estimate. Rates are typed as a percentage (`13.25`) and
stored as the fraction the database and API use (`0.1325`).

### Cleaning up purchase sources

Sales platforms link to a `vendor` row as their purchase source, so before
platforms could be introduced the vendor list -- one row per spelling the
importer met, including typos and vendors with no kind set -- needed tidying:

    python -m app.vendor_cleanup                                        report, touching nothing
    python -m app.vendor_cleanup --merge 15:12 --kind 19:marketplace --commit   write the changes

Like the other passes above, it defaults to a dry run and writes only with
`--commit`. `--merge <from>:<into>` moves the purchase orders from one vendor
onto another and removes the duplicate (a vendor row holds no history of its
own); it is refused if both vendors used the same order number, since merging
would collide them. `--kind <vendor>:<code>` sets a vendor's `vendor_kind`.
`--delete <vendor>` removes a vendor with no purchase orders left. Every
change is named on the command line -- which vendors are the same business is
the owner's call, not something the script guesses -- and the report lists
what was done, or would be done, by name.

## Reference vocabularies

Classifiers -- grades, mints, denominations, metals and the rest -- are rows
in reference tables, not free text. Administrators add values in the console,
from the dropdown where they are needed, and rename or retire them on the
**Vocabularies** page (`/owner/vocabularies`):

- **Rename** changes the label, which is what people read. The code never
  changes: saved searches, the data and the API use it. Every record shows
  the new name at once. A renamed value is marked `manual`, so a later seed
  load keeps your wording instead of putting the shipped one back.
- **Retire** stops a value being offered in the pickers; every record that
  uses it keeps it, and its dropdown still shows it, marked *(retired)*.
  **Restore** offers it again. A value the application looks up by its code cannot be retired --
  any status, disposition, strike type, kind, authenticity, grade scale or
  valuation basis, and the values `single`, `USD`, `US`, `unknown` (vendor
  kind) and `frn` -- since receiving, importing or selling would stop; those
  can still be renamed.
- **Merge into...** replaces a value with another for good -- two values
  that say the same thing. Choose the value to keep and the page shows what
  would happen before asking: every item holding the old value moves to the
  kept one (an item that already had both keeps one), the old label, code
  and aliases become aliases of the kept value -- so ratings, searches and
  imports using the old word still find it -- and the old value is deleted.
  It is remembered (`reference_merge`), so a seed load does not bring it
  back and a seed row naming it lands on the kept value. A merge is refused
  when another vocabulary or a facts table uses the value (a denomination's
  composition, a series' year ranges, a note issue): change those first, or
  retire the value instead. Values that cannot be retired cannot be merged
  away either.

**Retire or merge?** Retire a value you no longer want offered but whose
records are right as they are. Merge a value that was a duplicate or a
mistake, so no record keeps it.

### Other names (aliases)

The standard term is a value's label -- DCAM, United States Note, Walking
Liberty Half Dollar. What people actually write is an **alias**: UCAM, Legal
Tender, Walker. **Vocabularies** in the console (`/owner/vocabularies`) lists
every vocabulary's values with their aliases, and adds or removes them. An
alias works at once:

| Where | What it does |
|---|---|
| The search box | finds items of that value, as above |
| The importer | reads the alias as the value, before inventing a new row, and lists each one in the report (`aliased  : note_type: Legal Tender -> us_note (3 rows)`) |
| Dropdowns | a long list has a **Find** box beside it; typing an alias offers the value, with the alias in brackets, and Enter picks the first |

**Two values may share an alias.** "Cartwheel" is any large silver dollar,
and "National Currency" two note classes. Search finds both; the importer,
which cannot choose, uses neither and records the word as it would an
unknown one. The console marks a shared alias.

**An alias may not be a value's own label or code**, since the label or code
is always matched first.

### Attributes

An item's **attributes** say what it is beyond its grade, and an item may
have any number: a note can be a Star Note, a Fancy Serial and No Motto; a
coin First Strike and CAC. The item editor lists them under **Attributes**;
the × removes one and the picker after them adds one, offering only
attributes for that kind of item. They are saved with the item.

An attribute marked **read** was found by a rule -- the serial number, or the
spreadsheet's rating -- rather than set by a person. **Removing one keeps it
removed**: the serial check does not add it back. Setting it again restores
it.

The search box finds items by an attribute's name or alias (`godless`,
`first strike`), and the API takes `attribute=<code>` as a filter on either
screen. Bulk edit does not set attributes: a whole set applied to many
items would wipe whatever each carried that the others do not.

**Removing a shipped alias retires it.** Seed loads only ever add, so a
deleted alias would come back on the next load; a retired one is kept, shown
struck through, and a click restores it. An alias added in the console is
deleted outright.

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
