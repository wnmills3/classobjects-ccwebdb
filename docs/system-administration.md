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

### Filing photographs: the photo import pass

`python -m app.photo_import` walks the safe-deposit-box photograph library and
links each photograph to the item its filename names, through the same writer
(`app.image_links.attach`) the console's Photos page uses:

    python -m app.photo_import            report, touching nothing
    python -m app.photo_import --commit   write the links

"Touching nothing" is literal: a dry run decodes and validates every file
through the same imaging layer, so it still names one the imaging layer would
refuse, but it writes no rows **and no bytes into media storage**.

`--root` defaults to `settings.photo_library_root`, the real library --
**never point this at it without `--commit` having been asked for on
purpose**; the owner watches the first real run personally.

The filename convention is `<item_code>_<nn>.<ext>` (`app.photo_names`), for
example `CC-000412_01.jpg`. Nothing here repairs a filename that misses it --
a lowercase `cc-` is a miss, not a correction. The sequence carries a role as
well as the order: `01` is obverse and becomes the item's primary photograph,
`02` is reverse, and anything past that is `unassigned`, left for a person to
set. Every file is stored regardless of what its name says -- an import step
must never be the reason a photograph is lost -- so only the *link* is ever
withheld.

A photograph the pass cannot place is stored, unattached, and reported rather
than guessed at: a name that does not match the convention, an item code
nothing recognises, an item that was deleted or split, two files in this run
claiming the same slot (a collision links *neither* -- there is no way to
prefer one claimant from the filename alone), or a slot an earlier run
already filled. An item that already has a primary photograph keeps it: the
`_01` is still filed at sequence 1, but non-primary, and the report names it
under `primary` -- what a buyer sees is not changed without being told. The
console's **Photos** page (`/owner/photos`) is where
those unattached photographs get filed onto the item they belong to by hand.
A photograph the pass links onto an item that is for sale is not refused --
it is reported, by item code, so the owner knows what changed under an active
listing without the pass needing to ask anyone to acknowledge it.

### How an item comes into being

Four paths create an `inventory_item`.

| Path | When | Notes |
|---|---|---|
| **Spreadsheet import** (`app.importers`) | the normal path | creates the item, its purchase order and its vendor together, at the moment of purchase |
| **Entering an item on a purchase** (`POST /api/inventory`) | a walk-in, show or one-off buy entered by hand | requires a purchase order; see "Entering a purchase" below |
| **Splitting a lot** (`POST /api/inventory/{id}/split`) | a bought lot becomes individual pieces | children inherit the parent's claims and a share of its cost; the parent gets `split_at` and disappears from every view |
| **`python -m app.seed`** | development only | never on real data |

All four record an opening `item_status_history` row, so every item has a
lifecycle from its first row -- see below.

Putting an already-owned item up for sale is a separate, later step, not a
creation path -- see "Offering an item for sale" below. The console's old
**Manage** page used to create an item and a listing together; it is retired,
because it could never offer an item the business already owned.

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
- **An item that has ever been listed.** Order history references the listing
  and the reports exclude deleted rows, so the order would point at a row that
  is not there. This one is **permanent, not a step to do first**: any listing
  row refuses, ended ones included, and nothing removes a listing row. Once a
  coin has been offered, the offer is part of the sales history and the item
  stays in the record.

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

Two kinds of unshipped order **cannot** be cancelled at all, for the same
reason: the listing they sold has already ended, so there is nothing to put
the stock back on. One is a sale recorded from an outside platform, where
recording it ended the listing; the other is an order that bought a **sales
lot**, which ends its listing and dissolves the lot as part of the sale. The
console refuses both with a 409 naming what is in the way. Once such an order
has shipped it can be cancelled normally -- cancelling then returns no stock
and so strands nothing, and is how a refund is recorded.

Every order now **names the platform it sold on**, since an order can come
from any of them. The Orders page uses it to grey out **cancelled** on an
order recorded from an outside platform, with the reason on the option, rather
than offering a choice the server would refuse. An order that bought a lot in
the web store is still offered the choice and refused by the server, which
shows the refusal as an error on the page.

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

**The copy has two shapes, and it says which one it is.** A line that sold a
single coin keeps an `item`; a line that sold a **sales lot** keeps a `lot`
(its number, title and description) and an `items` list -- the same per-coin
detail, one entry per member -- in place of `item`. `snapshot_version` on the
copy tells them apart: **version 1** always has `item`, **version 2** has
`item` *or* `lot` and `items`, never both. Older copies are never rewritten,
so a version-1 copy stays exactly as it was taken and stays readable; anything
reading a copy asks for `lot` first and branches on whether it is there. The
lot half is the only lasting record of which coins the group held, because
selling a lot releases its memberships.

**Changing an item that is for sale.** An item is *for sale* while an active
listing with stock offers it, or an order that has not shipped (pending, paid
or packed) holds it. The item editor says so at the top, naming the listing
or order, and Save stays disabled until **Change it anyway** is ticked. The
API refuses such a save with 409 unless it carries `acknowledge_for_sale`;
bulk edit refuses the whole selection, naming the items, and then offers
**Change the items for sale too**. Saving nothing needs no confirmation. Once
an order ships, the item is ordinary again -- its sale keeps its copy.
Editing the listing itself (`PATCH /api/listings/{id}`, the offers API) is
not affected. The same 409-unless-acknowledged rule guards six more writes: a
receipt whose outcome is not `received` (`POST /api/inventory/receive`),
splitting a listed lot (`POST /api/inventory/{id}/split` -- an item already in
an order refuses unconditionally, without an acknowledgement, since a split
there cannot be made at all), recording errors against an item for sale (`PUT
/api/inventory/{id}/errors`), attaching or deleting its photograph (`POST
/api/images`, `DELETE /api/images/{id}`), and merging a vocabulary value that
moves it (`POST /api/reference/{table}/{code}/merge`, which also names the
for-sale items, up to ten of them, in the merge preview before anyone
confirms).

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
stored as the fraction the database and API use (`0.1325`). A fee of **zero**
is shown as `0%` or `$0.00`, which is not the same as a blank cell: blank
means nobody has looked those terms up yet.

The **add and edit window takes Alt plus the underlined letter** to jump to a
field, and Ctrl+S or Ctrl+Enter to save, as the item and order editors do.

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
`--delete <vendor>` removes a vendor with no purchase orders left. Removing a
vendor a **sales platform names as its purchase source** is refused either
way, naming the platform: unlink it on the Platforms page first. Every
change is named on the command line -- which vendors are the same business is
the owner's call, not something the script guesses -- and the report lists
what was done, or would be done, by name.

## Offering an item for sale

Putting an already-owned item up for sale is separate from creating it (see
"How an item comes into being" above), and goes through its own writer,
`app.offering_writes` -- the only code that changes a listing's status or the
claim that tracks where an item is offered.

**Offering.** From the **Coins** or **Currency** screen
(`/owner/inventory/coins`, `/owner/inventory/currency`), select one or more
items and **Offer for sale...** in the bulk bar; from a single item's editor,
its **Offers** panel has the same button. The panel's button is hidden only
when the item is currently held -- active or paused -- by a listing on a
platform that is **not** the business's own web store; nothing is hidden
while the platform list is still loading or failed to load, and an item held
only by its own store listing still shows it. So **moving an item from the
shop to another platform is one step**: offering an item that is active in
the web store pauses that store listing automatically, and it resumes when
the new offer ends (see "A paused listing" below) -- there is no need to End
the store listing first. Either place opens a dialog for one platform and one
format (fixed price or auction) with a row per item: price, title and
description (pre-filled from the item), an optional listing number, and the
item's cost, estimated fees, net and margin beside it for reference. **Offer**
submits the whole batch to `POST /api/offers`; a refusal -- the item is
already offered on another platform, is already offered in the shop, has been
deleted, is not received, has been split, or the platform is retired -- names
every affected item **inside the still-open dialog, with the prices already
typed kept**, and writes nothing, so the batch is all or nothing.

**Listings** (`/owner/listings`) lists every offer the business has out --
active and paused by default, or every offer including ended ones with the
**All, including ended** status filter -- filterable by platform and format,
each with its price, cost, margin, status and a link to the platform's own
listing page when there is one. **Edit** (only on an active row) changes its
price, title, description or listing number (`PATCH /api/listings/{id}`);
nothing else, because a status change has consequences for other listings a
field assignment cannot express. The item editor's **Offers** panel shows the
same rows for one item, ended ones included, so where an item has been offered
before and for how much stays visible after the offer ends.

**A paused listing** is a store listing set aside because its item was offered
somewhere else while it was active in the web store. It keeps its price and
everything else, is not for sale while paused, and does not appear in the
public catalogue. The Listings page names the offer that caused the pause
("paused for listing #12 on eBay") when that listing is in the same result.
Nothing needs to be done to a paused listing directly: it resumes on its own,
at the price it had, when the offer that paused it ends.

**Ending an offer.** **End**, on the Listings page (active and paused rows) or
the item's Offers panel (active rows only -- a paused row there is the store
listing tucked behind the running offer, not something to end on its own),
asks a question naming the listing, the item and the platform, then calls
`POST /api/listings/{id}/end`. This is a withdrawal, not a sale: the listing
is marked `ended` and any store listing it had paused resumes at its old
price. Ending is not reversible from the console -- offering the item again
makes a new listing, and the old one's listing number and history stay as
they were.

**Recording a sale.** **Record sale...**, on the Listings page's active rows
only, is for an offer that sold somewhere other than the web store -- eBay,
Whatnot, an auction house -- and is being entered by hand after the fact. The
dialog asks for the sale price, the buyer's username on that platform (left
blank for an auction house's undisclosed buyer, or for any platform that does
not name buyers), the platform's own order or transaction number, and the
actual fee amounts it charged, one row per fee kind (`sales_fee_kind` --
commission, processing, listing, shipping label, promotion, other); gross,
fees, net and margin are shown as they are typed. **Record sale** submits to
`POST /api/listings/{id}/sale`, which both records the order (buyer matched
or created, price, fees, per-item shares) and ends the listing as sold in one
transaction -- the sale is already over by the time it is entered, so there
is no separate End step. A paused store listing the sold offer had set aside
ends too, rather than resuming, since the item is now gone. The new order
appears on the Orders page like any other, already `paid` (or `delivered` for
an auction house, which has already shipped for the owner).

**Record sale... is not offered on web-store rows.** A shop item sells through
the cart and checkout; entering an in-person sale of one is a matter of placing
an order on the customer's behalf from the Orders page. Whether the console
should also allow **Record sale...** for a store listing -- for a show or
over-the-counter sale of something that was also listed in the shop -- is an
open decision recorded in `docs/specs/selling-design.md`.

**An order recorded this way cannot be cancelled.** Because recording the sale
also ended the listing, there is nothing for a cancellation to put the stock
back on: the console refuses the change with a 409 naming the platform, rather
than leaving an ended listing holding stock nobody can see and an item stuck at
`sold` that could never be offered again. A store order is unaffected --
cancelling an unshipped one still returns its stock as it always did. If an
outside sale falls through, the remedy today is to enter the return by hand
(the item's status and disposition on its own page) rather than through the
order; a proper "undo an outside sale" path, which has to re-offer the item,
does not exist yet.

**The Manage page is gone.** The console's old **Manage** page created an item
and a shop listing together; it could never offer an item the business already
owned, which is every item in the collection, so it was retired. Enter a
purchase (above) to create an item, then offer it here.

## Selling coins together: sales lots

A **sales lot** is a group of coins offered and sold as one thing -- three
Morgan dollars in one eBay listing, a type set in the web store. It is a
temporary grouping, not an inventory item: the coins stay individually owned,
individually costed and individually reportable the whole time, and the lot
exists only for as long as the offer does.

A lot is not a **purchase lot**. A purchase lot is how something came in and
is permanent (see "Receiving" above); a sales lot is how something goes out
and ends when the offer does. A coin can be in one of each at the same time
and they say nothing about each other.

**Putting a lot together.** Coins go in from the **Coins** or **Currency**
screen, not from the Lots page: select them and choose **Group into lot...**
in the bulk bar, then either start a new lot -- give it a title, which is what
the offer and the shop will call it -- or add the selection to a lot that is
still assembling. The whole selection goes in, including the part of it that
is off the current page. **Sales lots** (`/owner/lots`) is where the group is
then read and finished: each assembling lot shows its coins with their cost
and value, a running cost basis and value for the group, **Remove** per coin,
**Edit wording...**, **Discard...**, and **Offer for sale...**.

The group's **Value is a floor, not an estimate**. A coin nobody has valued
contributes nothing to it, so the page says how many of the coins are **Not
yet valued** whenever there are any. Reading $1,400 as the worth of a group
when two of its five coins have never been valued is the mistake that figure
invites.

**What cannot go into a lot.** A coin that has been deleted or split, one that
is not received, one already sold or shipped, one already in another open lot,
and one that is not a *whole* item to claim -- a listing offering more than one
unit of it, or an unshipped order already holding units of it. Each refusal
names the coin and the reason.

**Offering a lot.** **Offer for sale...** on an assembling lot opens the same
dialog as offering a single item: one platform, one format, one price for the
whole group, with the lot's title and description pre-filled. Any coin in the
group that cannot be offered refuses the whole lot, naming that coin -- a lot
is all or nothing. Offering it **freezes** the lot: its title, description and
membership can no longer be changed, because the buyer is now looking at that
exact group. An empty lot cannot be offered at all, and the button says so by
staying disabled rather than waiting for a refusal.

A member's own **web store listing is paused** when the lot is offered, exactly
as it would be if the coin were offered on eBay on its own, and comes back if
the lot is dissolved.

**A coin in an offered lot cannot be offered on its own, anywhere** -- not even
in the web store. The item editor's Offers panel hides its **Offer for sale...**
button for such a coin, and the API refuses with the lot's number. The way to
sell one coin of a group separately is to end the lot's offer first.

**Ending a lot's offer dissolves the lot.** End is a withdrawal: the listing
ends, the lot is marked **dissolved**, every coin is released back to being
sold on its own, and any store listings the lot's offer paused come back at
their old price. **Nothing brings a dissolved lot back.** The confirmation
dialog says all of this before anything happens. Grouping the same coins again
starts a **new** lot with a new number; the Lots page's **Re-offer as a lot**
on a dissolved row does exactly that, copying the title, description and coins
into a fresh assembling lot -- and tells you if a coin has since been offered
on its own and so could not be taken back.

**What a sold lot leaves behind.** Recording a sale of a lot -- through
**Record sale...** on the Listings page for an outside platform, or through an
ordinary shop checkout for a store lot -- marks the lot **sold**, releases
every membership, ends rather than resumes each member's paused store listing,
and files every coin as sold. The order carries **one line** for the lot at the
price it sold for, and behind that line **one share per coin**, each holding
that coin's part of the money and of the fees, divided by cost basis. That is
what keeps per-coin gain answerable after a group sale. The item editor's
**Sales** list shows a group sale on each member's own page, saying which lot
it was and that coin's share of it -- the line's quantity and price belong to
the whole group, so the share is the number that is about the coin.

The lot itself is never deleted once it has been offered. The Lots page's
**Offered, sold and dissolved** table keeps it for good: it is how anyone later
finds out which coins went out together. Only a lot that was never offered can
be discarded.

**An order that bought a lot cannot be cancelled.** Selling the lot ended its
listing, so there is nothing for a cancellation to put the stock back on --
the console refuses with a 409 naming the listing. This is the same rule that
already applies to a sale recorded from an outside platform, reached by a much
more ordinary path. If a lot sale falls through, the coins are brought back
individually, by hand, and grouped again as a new lot.

**In the shop**, a store lot appears as one card and one detail page with one
price and an **Add to cart** of one. It carries no grade, year, country or
metal of its own -- no single value of any of them describes a group -- so the
catalogue's filters on those fields never match a lot, though a text search
still finds one through the offer's own title. Its picture is one of its coins,
and the caption says so. Below the (empty) specifications table the page lists
**the coins in the lot**, each with its own picture and details, and it keeps
listing them after the lot has sold, so a bookmarked page still says what the
group held. A lot's **piece count** is the **sum** of its members' -- a lot of
three rolls counts the coins, not the rolls.

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

### What a picker offers, and in what order

A dropdown over a vocabulary does two things beyond listing values: it puts
them in a sensible order, and, where the vocabulary distinguishes coins from
paper money, it hides the values that cannot apply to the item at hand.

**Order.** `GET /api/reference/{table}` (`backend/app/routers/reference.py`)
returns every vocabulary alphabetically by label, case-insensitively, except
eight tables ordered by `sort_order`: `grade` (70, 69+, 69, ... the scale's
own order), `denomination` (face value ascending, coins then notes), the four
lifecycles the code branches on -- `item_status`, `disposition`,
`sales_order_status`, `shipment_status` -- `item_kind` (curated by how often
each kind actually occurs, so coin and currency lead), and
`signature_combination` (chronological, and narrowed further to a note's own
series years by that note's year). `series` is not one of the eight -- it
stays alphabetical like everything else. The order is decided once, in the
API, so the shop's filters, the console's forms and the entry panels never
disagree about it.

**Fit.** Two markers, already on the rows, tell a picker which values apply
to the item being entered: `denomination.kind` (`coin` or `note`), and
`applies_to` (`coin`, `currency`, or `any`) on `series`, `error_type` and
`item_attribute`. `frontend/src/shared/kinds.js`'s `fitsKind(entry, itemKind)`
is the one place that mapping is written -- every picker that needs it passes
`fitsKind` as its `filter` rather than repeating the coin/note split by hand.
A value marked `any`, or matching the item's side, is offered; a value for
the other side is not -- a banknote's denomination picker never lists a
coin's, and its error-type picker never lists a coin's mint errors.

**Adding a value while entering.** Every descriptive vocabulary's picker
offers "+ Add a new value..." at the bottom of the list. Two of them --
attributes and error types -- ask for the label alone: nothing about "Star
Note" that a person would type needs a separate code entered by hand. The
rest still ask for a code and a label, both typed.

Where the label alone is enough:

- **The code is derived** from what was typed, lower-cased with punctuation
  turned to underscores ("Mismatched Serial" becomes `mismatched_serial`),
  and shown before saving so what will actually be stored is never a
  surprise.
- **If that code already names a value**, the existing one is selected
  instead of creating a duplicate -- the same name means the same thing, and
  nobody should have to resolve a collision they cannot see.
- **The kind is inferred** from the item being entered (paper money, or
  everything else), so the new value is immediately offered by the same
  picker that just created it -- a value saved with no marker would fit no
  kind and disappear from its own list.
- **An attribute additionally asks for its group** (Serial, Variety,
  Release, Qualifier, Verification) before Add is enabled. The database
  column is required with no default, and the owner chose to be asked rather
  than have one picked silently; leaving it for later on the Vocabularies
  page is not an option at entry time. Error types carry no group and are
  not asked for one.
- **The new value is marked `manual`** -- this last one however it was added,
  code typed or derived. It is the same provenance a hand
  correction gets, so it stays distinguishable from the shipped catalogue and
  from what an import has inferred, and it is left out of an export by
  default (`python -m app.seeding export`, whose `--source` defaults to
  `seeded` alone).

Attributes can be added from the item editor, and from Receiving's "Confirm
or correct fields" pane, which reuses the item editor's own form. Error types
can be added wherever an error is recorded -- see *Errors* below.

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

An attribute the shipped vocabulary lacks can be added from that same
picker without leaving the item -- see *What a picker offers, and in what
order* above, and note that adding an attribute is the one case where the
add form also asks for a group.

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

### Errors

`item_error` records mint and printing errors against an item -- a bill is
commonly miscut *and* misprinted, so an item may carry any number of errors,
each with its own free-text note ("miscut at 3 o'clock, 4mm"). The same
error type cannot be recorded twice on one item, and `GET`/`PUT
/api/inventory/{id}/errors` replace the whole set at once.

One panel is mounted in three places:

| Where | Saving |
|---|---|
| The item editor, beside Attributes | Its own `PUT`, independent of the Save button -- an error recorded here is not held back by, or lost to, a discarded edit elsewhere on the form |
| **New item** | Held on the form; sent once the item itself has been created |
| **Receiving**, in the per-item dialog, and only when **exactly one** item is ticked | Its own `PUT`, the same as the item editor |

On Receiving the panel appears only for a single ticked item, because `PUT
/api/inventory/{id}/errors` replaces **one** item's set and there is no honest
bulk meaning to give it for several parcels received together -- the same
reason a photograph is refused there. It also waits for that item's own record
to load, since the type picker cannot be offered before the item's kind is
known: an unknown kind reads as a coin, and a banknote would be shown the
sixteen coin error types.

The type picker is filtered the same way a denomination picker is: a note is
offered the eleven currency error types and the one type that applies to
both, never the sixteen coin ones, and a type already recorded on the item is
not offered again. Removing a row removes that error the next time the panel
saves.

**On New item, the item is created first and its errors are saved second.**
If the create succeeds and the errors call then fails, the form says so
plainly -- the item was created, its errors were not -- keeps the typed rows
and the new item's code on screen, and offers **Retry**. Save (and Save and
add another) stay disabled until that retry succeeds, so the same piece
cannot be entered a second time while its errors are still pending.

Error types grow the same way attributes do: "+ Add a new value..." on the
type picker, one field, no group to choose.

## Backing up and restoring

The database is the system of record. The spreadsheet is not a backup -- it is
a historical source, and it has not described the collection since the import.

`app.backup` copies the whole database into another database, schema and all,
building the schema from the SQLAlchemy models rather than from PostgreSQL. A
`pg_dump` file is still the fastest way to get a copy *off* the machine; this
is for keeping a working copy beside the live one and for moving to another
engine.

Run from `backend\`, with the conda environment active:

```cmd
python -m app.backup                      copy to a timestamped database
python -m app.backup --name before_split  copy under a name you choose
python -m app.backup --list               what copies exist, and their size
python -m app.backup --verify <name>      compare a copy against the live database
```

**Always verify, and never trust `--list`.** Size is not evidence. A copy that
aborted partway still appears in the listing at a plausible size: on
2026-09-20 the newest copy, `ccwebdb_bak_20260917_190359`, listed at 13 MB and
contained **no inventory items and no purchase orders at all**. `--verify` is
what found that; the listing had shown it as a backup for three days.

A verify prints either

```
ccwebdb_bak_20260920_183627 matches the source on every table
```

or the tables that differ, and exits non-zero. Two kinds of difference:

- **Row counts that disagree.** For a copy taken today, any disagreement is a
  failed backup. For an older copy, small differences are expected and correct
  -- it is a record of what the collection was then.
- **`OLDER SCHEMA -- ... has no <table>`.** The copy predates a migration. It
  is a usable record of its own moment but cannot be restored over the current
  application without running the migrations, and the tables added since will
  be empty.

### Restoring

There is no `--restore` flag; a restore is a copy in the other direction, so
the same code and the same verification apply. Set `DATABASE_URL` to the copy
and copy it into a fresh database:

```cmd
set "DATABASE_URL=postgresql+psycopg://ccwebdb:<password>@localhost:5432/ccwebdb_bak_20260920_183627"
python -m app.backup --name ccwebdb_restored
```

Then point the application at `ccwebdb_restored` by setting `DATABASE_URL` in
`.env`, and restart. Restoring *into a new database* rather than over the live
one is the point: the original stays untouched until the replacement has been
checked, and switching back is an edit to one line.

This procedure was run end to end on 2026-09-20 -- live to copy to restored
copy, 7,656 items, verifying against the live database on every table.

**Take one before any migration.** The three copies that hold the collection
all predate `offer_claim`, `sales_venue` and `item_attribute`, so before
2026-09-20 there was no copy that had both the current schema and the
collection in it.

## Applying a schema release

Two different things, kept separate on purpose:

1. **What is left to do for the auctions release** (phase 4 of
   `docs/specs/selling-design.md`), stated as the current fact -- not as a
   migration still ahead of it.
2. **The general procedure** a release still follows from here on, for the
   one after this.

Conflating them is the mistake to avoid: the auctions release did not reach
`ccwebdb` by the procedure below, and knowing that is what keeps the next
release from assuming it can skip a step because "last time we didn't need
it."

### This release: the schema is already live

On 2026-09-22 a dispatched agent ran `alembic downgrade -1` and then
`alembic upgrade head` against `ccwebdb` by mistake, while verifying a
migration fix on a database that was already at the auctions branch's
revision from ordinary development. No data was lost -- 7,656 items and a
$536,118.82 cost basis, every selling table empty, verified directly --
because the destructive step happened to land on tables holding zero rows.
The owner's decision was to accept the state rather than re-run a rehearsed
migration for its own sake: `ccwebdb`'s `alembic_version` already reads
`e267ec3aedc1`, the auctions branch's head revision.

**What is still outstanding is not the migration. It is seeding and a
restart, and both are done *after this branch is merged into `main`*** --
not before, and not instead of merging. Said in so many words because the
rest of this section only implies it: the schema being current in `ccwebdb`
says nothing about which code is checked out or running, and restarting the
servers off an unmerged branch would put the wrong thing in front of the
shop. The two steps:

1. **Load reference data.** From `backend\`, with the conda environment
   active:

   ```cmd
   python -m app.seeding load
   ```

   This is what puts `storage_location_kind.consigned` into the database.
   It is **not** seeded by the migration -- unlike `sales_venue_kind` and
   `sales_fee_kind`, the only two tables the schema itself seeds
   (`backend/app/models/__init__.py`), `storage_location_kind` has always
   been a JSON-backed vocabulary (`backend/data/reference/
   operations.json`), and the `consigned` row was added to that file rather
   than to the migration -- see migration `e267ec3aedc1`'s own docstring for
   why an `INSERT` there would have been the wrong fix. Without this step,
   `app.auctions.consign` fails the first time anyone tries to mark an
   auction consigned, naming a reference code that does not exist.

2. **Restart the servers.** `scripts\ccweb_startup.cmd` runs uvicorn without
   `--reload`, so the backend that is already running is still serving
   whatever code was live before this branch merged -- the schema being
   current in the database does not make the API current. From a shell with
   nothing depending on the current session:

   ```cmd
   scripts\ccweb_shutdown.cmd
   scripts\ccweb_startup.cmd
   ```

**Then check:**

| Check | Expected |
|---|---|
| `alembic_version` | `e267ec3aedc1` (`scripts\ccweb_psql.cmd -c "select version_num from alembic_version;"`) |
| Item count and cost basis | 7,656 items, $536,118.82 total cost, unchanged from before |
| Selling tables | 0 lots, 0 auctions, 0 fees, 0 shares -- nobody has sold anything yet |
| `storage_location_kind` | contains `consigned` (`scripts\ccweb_psql.cmd -c "select code from storage_location_kind order by sort_order;"`) |
| The shop catalogue | answers (`GET /api/catalog`, or load the storefront) |
| The console | answers, and `/owner/auctions` loads with no auctions listed |

### The general procedure, for the release after this one

The shape the auctions release was supposed to follow, and did not get the
chance to. Follow it next time regardless -- what happened this time was an
accident recovered from, not a reason to trust one less step.

1. **Back up with `pg_dump`, and verify it by restoring it.** Not with
   `app.backup`, for this step: that copy is built from the **current
   models** and carries no `alembic_version`, so once the new code is checked
   out it already has the new tables -- it cannot be migrated, and it tries
   to read tables live does not have yet. `pg_dump` copies the database as it
   is. (`app.backup --verify` is still the check for its own copies, and
   `--list` is still never evidence -- see *Backing up and restoring*.)

   ```cmd
   set "PGBIN=%USERPROFILE%\miniforge3\envs\ccwebdb\Library\bin"
   "%PGBIN%\pg_dump.exe" -Fc -d ccwebdb -f "%USERPROFILE%\dev\ccwebdb-backups\ccwebdb_pre_release_YYYYMMDD.dump"
   "%PGBIN%\psql.exe" -d postgres -c "CREATE DATABASE ccwebdb_rehearsal"
   "%PGBIN%\pg_restore.exe" -d ccwebdb_rehearsal --no-owner "%USERPROFILE%\dev\ccwebdb-backups\ccwebdb_pre_release_YYYYMMDD.dump"
   ```

   Then compare every table's row count between `ccwebdb` and
   `ccwebdb_rehearsal`; they must agree exactly. If they do not, the backup
   is not usable and nothing is applied.

2. **Rehearse the migration on that restore, and show the counts before and
   after.** This is what *Testing*, "Before live", in
   `docs/specs/selling-design.md` requires; a scratch run costs minutes.

   ```cmd
   set "PGDATABASE=ccwebdb_rehearsal"
   scripts\ccweb_psql.cmd -c "select count(*), sum(total_cost) from inventory_item where deleted_at is null;"
   set "DATABASE_URL=postgresql+psycopg://ccwebdb:<password>@localhost:5432/ccwebdb_rehearsal"
   python -m alembic upgrade head
   scripts\ccweb_psql.cmd -c "select count(*), sum(total_cost) from inventory_item where deleted_at is null;"
   set "DATABASE_URL="
   set "PGDATABASE="
   ```

   The two counts must agree, and `alembic_version` must read the new head.
   Clear both variables before the next step -- they are what point the
   commands at the rehearsal rather than at live -- and drop
   `ccwebdb_rehearsal` afterwards; it is a full copy of the collection.
   `scripts\ccweb_rebuild.cmd` runs `alembic upgrade head` then `python -m
   app.seeding load`, in that order, against a from-scratch database; the
   **order** is the convention this follows too: schema first, reference
   data second.

   Run this way for the first time on 2026-09-23, for migration
   `cb1bb956f50b`: every one of 81 tables matched after the restore, and
   the rehearsal left 7,656 items and $536,118.82 unchanged.

3. **Only then touch the live database**, with the servers stopped
   (`scripts\ccweb_shutdown.cmd /keepdb`) so nothing writes while the
   schema changes. Apply against `ccwebdb` itself, with `DATABASE_URL`
   pointing at it (the default in `.env`, so usually no override is needed):

   ```cmd
   python -m alembic upgrade head
   python -m app.seeding load
   ```

4. **Check**, the same shape as *This release* above: `alembic_version`
   reads the new head; the item count and cost basis are unchanged; every
   table the new migration added is empty (nobody has used a feature that
   did not exist five minutes ago); the shop catalogue and the console both
   answer.

5. **Restart the servers** -- `scripts\ccweb_shutdown.cmd` then
   `scripts\ccweb_startup.cmd` -- for the same reason as *This release*:
   uvicorn runs without `--reload`.

## Things that are deliberately not configurable

Worth knowing so nobody goes looking for a setting that was never written:

- **Per-permission roles.** Two roles, one boundary; see above.
- **Password reset by email.** No mail configuration exists.
- **Editing an account's email.** It is the account's identity.
- **Deleting a user.** Deactivate instead -- history rows reference the user
  id, and `ON DELETE SET NULL` on those columns exists so that deactivating a
  member of staff never erases the record that the work was done.
- **A storage location that customers can see.** `models/lifecycle.py` calls
  this an authorisation boundary, not a convention: a public listing that
  leaked the safe-deposit box holding an item would be a security failure,
  not a cosmetic one. What enforces it is `routers/catalog.py`, which builds
  every public response field by field, and the test that asserts the result
  carries no location -- not the `public_catalog` view, which forbids the
  column but which no endpoint actually reads.
