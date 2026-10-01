# System administration

**What this covers.** How to run the business system day to day, rather than
how to build it: accounts and roles, settings, the command-line passes that
keep the collection record correct, how items are entered, changed, received,
offered and sold, reports, reference vocabularies, backups, and applying a
schema release.

**Who it is for.** Whoever administers the system -- a manager using the
console, or someone at the command line on the machine that runs it.

**Why it matters.** The `ccwebdb` database *is* the record of the collection
and its cost basis. Most of what follows is about keeping that record true:
who may change it, which code paths are allowed to write each part, and how
to take and prove a backup before anything risky.

Related documents: [runtime-operations.md](runtime-operations.md) (starting
and stopping the services), [environment-setup.md](environment-setup.md)
(installing them), [database-design.md](database-design.md) (how the record
is structured).

## Contents

1. [Accounts](#accounts) -- roles, creating accounts, passwords
2. [Settings](#settings) -- `.env` and `backend/app/config.py`
3. [The collection record](#the-collection-record) -- the command-line passes
4. [Finding items](#finding-items) -- the inventory search panel
5. [Inventory items](#inventory-items) -- creating, editing, receiving, removing
6. [Entering a purchase](#entering-a-purchase)
7. [Sales](#sales) -- sales orders
8. [Selling](#selling) -- platforms, offers, sales lots, auctions
9. [Reports](#reports)
10. [Reference vocabularies](#reference-vocabularies) -- values, aliases,
    attributes, errors, seed files
11. [Lists](#lists-friedberg-numbers-sellers-vendors-storage-locations) --
    Friedberg numbers, sellers, vendors, storage locations
12. [Backing up and restoring](#backing-up-and-restoring)
13. [Applying a schema release](#applying-a-schema-release)
14. [Things that are deliberately not configurable](#things-that-are-deliberately-not-configurable)

**Where things are done.** Everything in the console is under the
**management console** at `/management`, a separate application from the shop
(`docs/specs/management-console-separation-design.md`); none of it is
reachable from the storefront. Command-line examples are cmd: `python -m`
commands run from `backend\` with the `ccwebdb` conda environment active;
`.\scripts\...` commands run from the repository root.

## Accounts

### The two roles

| Role | Sees | Reaches |
|---|---|---|
| `customer` | the shop; their own orders | nothing under `/management`, no cost basis |
| `manager` | everything | the whole console, including what each item cost |

There is no third role and no per-permission grid. The line the system
enforces is **cost basis and provenance are not customer-visible**, and one
boolean expresses it.

Every manager-only endpoint carries `require_admin` (`backend/app/deps.py`).
That dependency, not the console's separate bundle, is the access control; the
bundle split removes an information leak, it does not enforce anything.

### How accounts come to exist

- **Self-service registration** (`POST /api/auth/register`, the shop's
  **Register** page) always creates a `customer`.
- **A manager creates it** (`POST /api/users`, **People → Accounts →
  New account**): email, name, role and an initial password. The role has no
  default in the API, so a manager is never created by omission. An
  email already in use is refused with 409. There is no mail configuration,
  so pass the password on out of band.
- **Promotion**: a manager changes an account's role under **People**.
- **The first manager** of a new database is created by
  `python -m app.seed`, from `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD`,
  after `python -m app.seeding load` has loaded the reference vocabularies it
  names. It takes no options and writes at once. It also creates five demo
  items, each offered in the web store, so it is never run against a
  database holding a real collection. Change the password immediately -- the
  default is published in this repository.

### What a manager may change

On someone else's account (`PATCH /api/users/{id}`), exactly three fields:

| Field | Notes |
|---|---|
| `full_name` | display only |
| `role` | `manager` or `customer` |
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

### The last-manager guard

Demoting or deactivating the final active manager is refused with 409.
The check is about the **result, not the actor**: demoting yourself is fine
while somebody else is still a manager. Without it one click locks everyone
out, and recovery means editing the database by hand.

To hand over management: promote the new person, confirm they can sign
in, then demote the old account.

## Settings

Settings decide where the database, images and photographs are, how sessions
are signed, and how purchases are taxed. Defaults live in
`backend/app/config.py` and are overridden by environment variables or the
repo-root `.env` (template: `.env.example`). Names are the field names
upper-cased (`jwt_secret` -> `JWT_SECRET`). **A misspelt name is silently
ignored** (`extra="ignore"`), so check the spelling when a change seems to
have no effect. Settings are read when the API starts; change them, then
restart the API (`.\scripts\ccweb_shutdown.cmd /keepdb`, then
`.\scripts\ccweb_startup.cmd`).

The log settings `CCWEB_LOG_DIR`, `CCWEB_LOG_KEEP` and `CCWEB_LOG_MAX_BYTES`
are read by the runtime scripts, not by `config.py`; see
[logs/README.md](../logs/README.md).

### Must change before anything outside this machine can reach the system

| Setting | Default | Why |
|---|---|---|
| `JWT_SECRET` | `dev-only-insecure-secret-change-me` | signs every token; anyone who knows it can mint an admin session. The default is deliberately obvious so an unconfigured deployment is easy to spot |
| `FIRST_ADMIN_PASSWORD` | `adminpassword` | published in this repository; used only by `python -m app.seed` (`FIRST_ADMIN_EMAIL`, default `admin@example.com`, names the account) |
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

**The `ccwebdb` database is the record.** Data is improved in the console or
by the passes below, which fill what is empty and never overwrite what a
person set. There is no importer: the only bulk way in or out is the workbook
backup (*Backing up and restoring*), and recovery from a data problem is a
restore from a verified backup.

### The passes over stored items

A pass applies a rule to every stored item at once -- the work the console
does as each item is saved, done for everything recorded before the rule
existed. **Each pass is a dry run unless given `--commit`**: it prints what it
would change and writes no database rows.

| Pass | Does | Options |
|---|---|---|
| `python -m app.classifier_defaults` | fills note class, seal, signatures, Reserve Bank, composition and No Motto from the facts | `--commit` |
| `python -m app.series_match` | assigns a coin's series from the design its title or description names | `--commit` |
| `python -m app.series_classify` | assigns series from denomination and year, for coins the text left and all notes | `--commit` |
| `python -m app.serial_patterns` | derives star, radar, repeater and similar designations from a note's serial | `--commit` |
| `python -m app.listing_links` | fills an item's listing address and seller's item id each from the other; outside eBay and Whatnot, an item and its purchase share one lot page, and each takes it from the other when missing (`docs/specs/entry-panels-design.md`, *Listing links*) | `--commit --by EMAIL` |
| `python -m app.ebay_orders` | fills eBay purchases' missing order numbers and items' listing ids from eBay's purchase history | `FILE... [--review FILE.xlsx] [--commit --by EMAIL]` |
| `python -m app.photo_import` | links photographs to items by filename | `[--root DIR] [--commit]` |
| `python -m app.vendor_cleanup` | merges, renames, re-kinds or deletes purchase sources, by explicit instruction | see *Cleaning up purchase sources* |

`--by` names the account each change is logged under in the item's
**History**; `--commit` without it is refused. Run `classifier_defaults`
before `series_classify`: note class is evidence for series. Before running
any pass with `--commit` on the live database, take a verified backup and
read the dry-run counts.

The other command-line modules are not passes: `app.seeding` and `app.seed`
(reference data and a new database's first manager), `app.backup` and
`app.workbook_backup` (*Backing up and restoring*), and `app.reports`
(*Reports*). None of them has a dry run.

### eBay order numbers and listing ids

An eBay purchase is found and receipted by its order number, and a listing id
links an item back to the page it was bought from; `app.ebay_orders` fills
both in from eBay's own purchase history. That history comes from the Chrome
extension named in the README (*Useful tools*): one workbook per year, a row
per item bought. From `backend\`:

```cmd
python -m app.ebay_orders <workbook>... --review ..\logs\ebay_orders_review.xlsx
python -m app.ebay_orders <workbook>... --commit --by <your email>
```

The first is a dry run: it prints what it would do and, with `--review`,
writes the review workbook (the only file a dry run writes). `--commit` does
it, in one transaction, and records each changed
item's order number and listing id in its **History** under the account
named by `--by`. It matches by **eBay's item id** -- every eBay purchase links
its listing (`ebay.com/itm/<id>`) -- not by words or price, and it:

- sets **Seller's item id** (`sellers_item_id`) on each item from its own
  listing link, or its purchase's; only where empty;
- gives an eBay purchase with no order number the one order its listings were
  bought on;
- **merges the purchases one order was split into**: an order of several
  listings becomes one purchase, holding every item, each keeping its own
  listing's id, and linking the order's page on eBay.

For an item the pass could not fill, type the id into **Seller's item id** in
the item editor or the New item form (which keeps it for the next piece of
the same listing); the editor links it to the listing on eBay. An order
number belongs to the purchase: the editor's **Purchase:** link opens it, and
**Edit details** changes it.

The review workbook lists what it numbered, what it could not (a listing in no
order, or in several), and **stored order numbers eBay contradicts** -- those
are never changed by the pass; correct them on the purchase's **Edit details**.
Run it again after a new download: it only fills what is still empty.

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
save, and the value is yours: nothing fills that field again. A recorded
value is never replaced, but it narrows the facts: a $1
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

### Filing photographs: the photo import pass

`python -m app.photo_import` walks a directory (`--root`, default
`PHOTO_LIBRARY_ROOT`) and links each photograph to the item its filename
names, through the same writer (`app.image_links.attach`) the console uses.
The dry run decodes and validates every file, so it names one the imaging
layer would refuse, but writes no rows and no bytes. Every run on a new batch
of photographs starts as a dry run the owner watches.

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
filed by hand on the console's **Photos** page (`/management/photos`).

## Finding items

Finding the right items is the start of most console work -- editing,
offering, grouping into a lot, a bulk edit. The **Coins** and **Currency**
screens (`/management/inventory/coins`, `/management/inventory/currency`)
share one search panel.

**The search box has no field syntax.** What you type is one term, matched
anywhere in an item's title, description, rating, item code or its purchase's
order number, ignoring case -- so an eBay order number pasted into the box
(`11-15110-51877`, or part of it) finds what was bought on that order. A
term of six or more letters and digits, at least one a digit, also matches
order numbers with their separators removed, so `111511051877` works too.
It also matches the **name or alias of what the item is**: design series,
strike type, grade designation, attributes and, on coins, mint; on currency,
note class and serial features. So `mercury` finds Winged Liberty Head dimes
whose listings never say "Mercury", `denver` finds coins whose listings give
only the D, `legal tender` finds United States Notes, and `funnyback` finds
every classified $1 Series 1928 and 1934 note. A term under three letters must
be a whole name: `PR` finds proofs and `D` Denver, but `s` does not match
every strike containing an s. **Search tips**, under the box, lists examples;
clicking one runs it.

The rating -- the owner's own rating text, `rating` -- is searched because it
is often the only descriptive text an item has: many items' titles are only a
denomination and their descriptions a lot number.

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
takes `attribute`, `error_type`, `issue`, `missing`, `lot`, `sellers_item_id`
and `deleted` (`no`, `only`, `any`) as filters (`app/inventory_search.py`);
reports' drill-down links use several of them. An unknown filter name is
refused.

**Every field has an Alt+letter shortcut**, underlined in its label: Alt+S the
search box, Alt+H the tips, Alt+C clear, Alt+Y and Alt+O the years, Alt+P the
coins' **Strike type**. No field uses D, E or F, which the browser keeps. A
shortcut on a disabled dropdown does nothing.

## Inventory items

An inventory item is one thing owned -- a coin, a note, a set, or a lot
bought as one. This section covers every way one is created, changed,
received and removed, and the rules that keep its history and cost basis
true while that happens.

### How an item comes into being

| Path | When | Notes |
|---|---|---|
| **Entering an item on a purchase** (`POST /api/inventory`) | every new acquisition | requires a purchase order; see *Entering a purchase* |
| **Splitting a lot** (`POST /api/inventory/{id}/split`) | a bought lot becomes individual pieces | children inherit the parent's claims and a share of its cost (`equal`, or `relative` to a value per piece); the parent gets `split_at` and drops out of every count. In the console: **Split into pieces...** in the item editor, a row per piece with its own description and year |
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

**Editable scalars:** `source_title`, `description`, `sellers_item_id`,
`listing_url`, `year_start`, `year_end`, `fineness`, `gross_weight_ozt`,
`fine_weight_ozt`, `piece_count`, `item_cost`, `shipping_cost`, `tax_rate`,
`tax_includes_shipping` (`EDITABLE_SCALARS`, `routers/inventory.py`).
`tax_rate` and `tax_includes_shipping` are NOT NULL, and a null for either is
refused naming the field. `storage_location_id` moves the item through
`set_location` (*Status and location have one door*) and never needs
`acknowledge_for_sale`, since a location does not show to a buyer. A banknote holds no year of its own:
`year_start`/`year_end` sent for a note is refused (422); send `series_year`.

**Editable classifiers**, set by code rather than id: `item_kind`, `country`,
`denomination`, `bullion_form`, `set_form`, `strike_type`, `grade`,
`grade_designation`, `grading_service`, `metal`, `series`, `storage_form`, `authenticity`,
`status`, `disposition` -- and a coin's `mint` (by code) and `variety`, on its coin detail, created if it has none and refused for a banknote. Five are NOT NULL (`item_kind`, `storage_form`,
`authenticity`, `status`, `disposition`) and refuse a null or empty code.
A designation of the other kind is refused naming the item -- EPQ or PPQ on
anything but a note, DCAM, FBL and the rest on a note -- including by a bare
`item_kind` change to an item that holds one.

**Banknote fields**, on the note's detail and refused by name for any other
kind: `note_type`, `seal_color`, `fed_district` and `signature_combination`
(by code); `series_year`, `series_letter`, `serial_number`,
`face_plate_number`, `back_plate_number`; and `printing_facility` (`dc` or
`fw`), read from the face plate when one is sent -- a location sent beside a
face plate that says otherwise is refused.

**Attributes** (`attributes`, a list of codes) replace the item's whole set:
one the item had and the list omits is marked removed, so no rule adds it
back. `[]` clears them; null is refused. Single-item edits only -- bulk edit
refuses them.

**Certificate numbers** (`cert_numbers`, a list) replace the item's
`item_certification` rows as a set: a number kept keeps its row, one omitted
is deleted, a new one is recorded as graded by the item's grading service.
`[]` clears them; null, a blank or a repeat is refused. The item editor has
**Grade designation**, **Grading service** and **Certificate no.** (comma
separated) beside the grade; each change is logged in `item_field_change`.

**Suggest description** under the Description box fills the draft with a
description written from the item's saved record
(`GET /api/inventory/{id}/suggested-description`, `app.item_descriptions`),
in the owner's style: grade, designation and attributes first, then what it
is -- "Superb Gem Unc 67 EPQ Radar 1999 $1 S/N F06566560R. Federal Reserve
Note Green Seal." for a note, "MS64 First Strike 1921-S Morgan Dollar.
Silver, 0.7734 ozt fine." for a coin -- with recorded errors beside the attributes, right after the grade ("Error Note, Misaligned Print (Reverse) 1963A $1 ..."). No field
labels, district, signatures or grading service. It writes nothing; Save
keeps it. It is disabled while other edits are unsaved,
since it reads the saved item. A listing's suggested title carries the
designation too ("PMG 64 EPQ").

**A kind change moves the detail row** (`app.item_kinds`). An item made a
banknote loses its coin row (mint, variety, PCGS type) and gains an empty note
row, so its serial number and other note fields can be sent in the same
request. An item made anything else is refused while its note row still holds
a value -- the serial above all, or an attached Friedberg number -- until the
request clears it. The item editor's **Kind** picker does that clearing in the
draft: choosing a kind empties every field that does not fit it (a coin's
metal, strike type, weights and coin-side denomination, series, grade and
attributes; a note's own fields), names each one on screen, and puts them all
back if the original kind is chosen again. A note's fields appear as soon as
Currency is chosen; its Friedberg panel appears once the note is saved.

**A grade is a number and a strike type.** `grade` takes `65` or `64+`; a
compound grade such as `MS65` or `PR69+` is split into the number and
`strike_type` (business, proof, specimen, reverse_proof,
enhanced_reverse_proof, sms), unless the request names a strike type itself.
Responses carry `grade`, `strike_type` and `grade_display` (`PR69+`).
Adjectival words are read at the bottom of their range: BU is 60, BU+ 63,
BU++ 65, PROOF PR63, AU 55.

**An unknown field name in an edit is dropped without an error** -- the edit
schema (`InventoryItemUpdate`) does not forbid extra fields, so a misspelt
field in a `PATCH` or bulk edit does nothing. Creating an item
(`POST /api/inventory`) does refuse an unknown field. Bulk edit does not set
attributes: one set applied to many items would wipe whatever each carried
that the others do not.

**Every console edit window** -- item, new item, order, platform, offer,
listing, record sale, lot, auction, a purchase's details, a customer and their
address -- takes Alt plus the underlined letter to jump to a field, and Ctrl+S
or Ctrl+Enter to save. Every Save button underlines its S and shows "Ctrl+S"
beside it (`management/SaveButton.jsx`); it has no Alt letter of its own, so
the underlined S means Ctrl+S, not Alt+S. No letter is
D, E or F, which the browser keeps; Escape closes a dialog.

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

**In the console** (`/management/receiving`) there is one search form: part of an
order number (matched anywhere in it, any case) and/or what the item is --
kind, denomination, year, mint, serial, series year. By default it finds only
what has not arrived (`ordered` or `missing`); **Any status** shows a whole
order. Results come 200 at a time per kind and status; anything beyond that
is counted on screen, so narrow the search to see it. A link naming one order
(`/receiving?order=<id>`, from the inventory screens' order column or New
purchase's **Receive these**) opens with that order's header and its items
already found, searched by the order's id (an order number is not unique
across vendors and is sometimes not recorded).

Clicking a line opens a dialog for that one item: the Identify section (a
note's series year and letter, denomination, serial and plates, or a coin's
year, mint and denomination, saved only with Receive), arrival date, storage
location, note, photographs, the four outcome buttons, the "Confirm or correct
fields" pane, the errors panel and, for a banknote, the Friedberg lookup.
After each receipt the search repeats, so what arrived drops off the list.

When the whole parcel arrived, **Receive all N still ordered** (shown on a
linked order with two or more lines still `ordered`) opens one dialog over
all of them: one arrival date, one location, one note, and the four outcome
buttons, recorded in one all-or-nothing request. Photographs and the
Friedberg lookup are off there, since each belongs to one object. Lines
already received, or closed out as missing, are not included.

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

`inventory_item`, `listing`, sales orders, sales lots, auctions and platforms
carry a `version` (purchase orders do not). An update sends the version it read, and the write is refused
with 409 if somebody changed the row first. PostgreSQL's MVCC does not give
this: without it, the second of two people saving the same item silently
overwrites the first with values loaded before the change.

**The item editor merges field by field.** Its save also sends `base` -- the
value each changed field had when the edit began -- and the server then
refuses only where someone else has changed one of *those* fields since
(409 with `conflicts`: each field with what it was, theirs and yours, and
who changed it when). A change to any other field does not stop the save.
Every field an edit or bulk edit changes is logged in `item_field_change`
with who and when, which is how a conflict names the other person, and the
editor's **History** panel lists those edits with the item's status and
location moves, newest first (20 at a time, **Show all** for the rest; it
re-reads after every save). While the form is open it
checks for changes made elsewhere every 15 seconds and whenever the window
gets focus back: a field not being edited takes the new value, with a note
saying so; a field being edited that was changed elsewhere is listed with
both values and who changed it when, **Keep mine** or **Use theirs**, and
Save waits for a choice.
Callers that send no `base` (scripts) keep the whole-item version check.

### Changing an item that is for sale

An item is *for sale* while an active listing with stock offers it, or an
order that has not shipped (pending, paid or packed) holds it. The item editor
says so at the top, naming the listing or order, and Save stays disabled until
**Change it anyway** is ticked. The API refuses such a save with 409 unless it
carries `acknowledge_for_sale`; bulk edit refuses the whole selection, naming
the items, and then offers **Change the items for sale too**. Saving nothing
needs no confirmation. Once an order ships, the item is ordinary again.

**A new status or disposition takes an offered item off sale.** The editor
warns before the change is confirmed ("saving ends its offer") and the box
reads **Change the status and end the offer**; the save then ends every offer
holding the item, as receiving an item `missing` does. The bulk edit does the
same for each selected item whose status or disposition changes. An item in
an auction lot is refused: take the lot out of the auction first.

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

Three cases are refused with 409:

- **A lot with pieces split from it.** It holds the cost basis its children
  were allocated from; detach them first.
- **An item that has ever been offered** -- on its own or inside a sales lot,
  ended offers included. The offer is part of the sales history, and nothing
  removes a listing row, so this is permanent.
- **An item in a sales lot.** Take it out of the lot first.

Deleting twice is not an error. `DELETE /api/inventory/{id}/parent` detaches a
split child from its parent, for when the lineage itself was wrong; the piece
keeps the cost it was allocated, and repeating it is harmless.

## Entering a purchase

**Purchases** (`/management/purchases`) is the one door for an acquisition:
no item is entered outside a purchase, so a standalone buy is a purchase
holding one item (`docs/specs/entry-panels-design.md`).

- **Vendors** are picked from a list (`GET /api/vendors`) or added inline
  (`POST /api/vendors`: name, kind, web address). Names are unique,
  case-insensitively.
- The purchase (`POST /api/purchase-orders`) needs only a vendor, so a
  walk-in or show purchase needs nothing else. Left blank, the order number
  is generated (`Order-0001` and up). Vendor and order number together must
  be unique; the date, if given, must be no later than tomorrow.
- **Items** are entered on the purchase (`POST /api/inventory`) as `ordered`,
  or `received` for something already in hand. A **lot** is an item with a
  piece count above 1.
- **Tax fields** are per item, pre-filled from the purchase's controls: a rate
  that starts empty (the configured default), "No sales tax charged" (rate 0),
  and "Tax on shipping" (As configured / Taxed / Not taxed). An explicit rate
  must be 0-1 with up to four decimal places; `.0635` is accepted.
- **Save and add another** keeps what the next piece of the same purchase
  usually shares, and the purchase-wide tax defaults, and clears the rest;
  the exact list is in
  [specs/entry-panels-design.md](specs/entry-panels-design.md) (*New item*).
- **Receive these** opens Receiving for that purchase; **Start another
  purchase** returns to step one.

The help band at the bottom of the console window explains whichever field
has focus, on this form and every other. All four endpoints above are
manager-only.

## Sales

A sales order records what a customer bought, at what price, and where it is
in fulfilment. **Sales** (`/management/sales`) lists every sales order, newest first: customer, lines at the price
paid, total, platform and status (`GET /api/orders`). The shop's **Your
orders** page is only the signed-in person's own (`GET /api/orders?mine=true`)
and has no status control; order administration lives in the console alone.

Status is changed from the order's row (`PATCH /api/orders/{id}`) through
pending, paid, packed, shipped, delivered, cancelled, refunded.

**Cancelling is one way.** A cancelled order cannot move to any other status
(409); place a new order instead. Re-sending `cancelled` is harmless.
Cancelling an order that is pending or paid returns its stock to the catalog
-- which is why the order cannot come back: it would stand on stock already
offered to the next buyer. Cancelling once it is packed, shipped or delivered
returns no stock; that is how a refund is recorded.

Two kinds of pending or paid order **cannot be cancelled**, because the
listing they sold has already ended and there is nothing to put the stock
back on: a sale recorded from an outside platform, and an order that bought a
**sales lot**. The API refuses with a 409 naming what is in the way; the
Sales page grays out **cancelled** on an outside-platform order. Once such an
order is packed it can be cancelled like any other (no stock returns). There is no "undo an outside sale" path: if one falls through,
restore the item's status and disposition by hand and offer it again.

**Placing an order for a customer.** **New order** opens an editor that
searches customers (and accounts with no customer record yet) and the
catalog, and saves with `POST /api/customers/{id}/orders`. Prices default to
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
version 2 has `item` for a single coin *or* `lot` (id, title,
description) and `items` (one entry per member) for a sales lot, never both.
Older copies are never rewritten; a reader asks for `lot` first. The lot half
is the only lasting record of which coins a sold group held.

## Selling

How an owned item is put up for sale -- in the web store, on another
platform, grouped into a sales lot, or in an auction -- and how a sale made
elsewhere is recorded. The rule underneath all of it: an item is offered in
one place at a time, so it can never be sold twice. `app.offering_writes` is
the only code that changes a listing's status, the claim recording where an
item is offered, or a sales lot's status (`docs/specs/selling-design.md`).

### Sales platforms

**Platforms** (`/management/platforms`) lists every platform the business sells
through -- the web store, eBay, Whatnot, an auction house -- with its kind, an
optional link to the purchase source of the same name, account handle,
listing-link template and default fees.

The **web store platform** is created by the migration and every store listing
and order names it. Its kind cannot be changed and it cannot be retired --
checkout and the public catalog are defined by it -- so the console hides
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

A platform links to a `vendor` row as its purchase source, and the vendor
list can hold one business under several spellings. `app.vendor_cleanup`
tidies them, each
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

**Listings** (`/management/listings`) shows every offer -- active and paused by
default, or **All, including ended** -- filterable by platform and format,
with price, cost, margin, status and a link to the platform's own page.
**Edit** (active rows only) changes price, title, description or listing
number (`PATCH /api/listings/{id}`) and nothing else. The item editor's
**Offers** panel shows the same rows for one item, ended ones included.

**A paused listing** is a store listing set aside because its item was offered
elsewhere. It keeps its price, is not for sale and does not appear in the
public catalog; the Listings page names the offer that caused the pause. It
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
order appears on Sales already `paid` (or `delivered` for an auction house).

A shop item sells through checkout; an in-person sale of one is an order
placed for the customer (*Sales*). **Record sale...** refuses store listings.

### Sales lots

A **sales lot** is a group of coins offered and sold as one thing -- three
Morgan dollars in one eBay listing, a type set in the web store. The coins
stay individually owned, costed and reportable throughout; the lot exists only
as long as the offer does. It is unrelated to a **purchase lot** (how
something came in, permanent); a coin can be in one of each.

**Putting a lot together.** On the **Coins** or **Currency** screen select
coins and choose **Group into lot...**: start a new lot with a title, or add
to a lot still assembling. The whole selection goes in, including any part off
the current page. **Sales lots** (`/management/lots`) shows each assembling lot's
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
(see *Sales*); if the sale falls through, restore the coins by hand and group
them again.

**In the shop**, a store lot is one card and one detail page with one price
and an **Add to cart** of one. It has no grade, year, country or metal, so
filters on those never match it, though text search finds its title. Its
picture is one of its coins, captioned as such, and its page lists the coins
in the lot, still after it sells. Its piece count is the **sum** of its
members'.

### Auctions

**Auctions** (`/management/auctions`) runs an auction from draft to settled
(`app.auctions` is the only writer of an auction's status and custody and of
which `auction_lot` rows exist; `routers/auctions.py` creates a draft and
edits wording, dates, lot numbers and reserves directly). An auction
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

## Reports

`/management/reports` answers questions about the whole collection -- what is
missing or wrong in the record, what the collection is made of, what has been
bought and not yet arrived, what is on offer and what has sold, and what it
all cost -- without a spreadsheet or a one-off script. **Reports read only**:
nothing here writes, and fixing what a
report finds is done where it is always done -- the item editor, a bulk edit,
Receiving, a pass. **Manager only**, like anything that shows cost, value or
location (`docs/specs/reporting-design.md` is the design).

### The catalog

The menu lists every report by group; choosing one shows its purpose, its
parameters and its table. Groups appear in the order the API lists them:

| Id | Group | Title | What it shows | Parameters |
|---|---|---|---|---|
| `cb_holdings` | Collection | Holdings | What the collection is made of: kind x denomination, with items, pieces and total cost | Status (default received), Disposition (default held) |
| `cb_designs` | Collection | Coins by design | Design series held, across every non-currency kind: items, year span, total cost | Status, Disposition |
| `cb_notes` | Collection | Notes | Note type x series designation: items, seal colors, Federal Reserve districts, star notes and fancy serials, total cost | Status, Disposition |
| `cb_grades` | Collection | Grades | Grade band x strike type x grading service, across coins and currency, with items and total cost | Status, Disposition |
| `cb_metal` | Collection | Precious metal | Metal x form, over items with a fine weight: items, ounces, cost, and melt value at the latest spot price | Status, Disposition |
| `cb_attributes` | Collection | Attributes and errors | Every attribute and error type a live item carries, split by view, with items | Status, Disposition |
| `dq_issues` | Data quality | Open issues | Every named data-quality check, counted across coins and currency | none |
| `dq_completeness` | Data quality | Field completeness | Percent of live items with each field filled in, by kind | none |
| `dq_photos` | Data quality | Photographs | Live items with no photograph, by kind and status, plus photographs filed against no item | none |
| `dq_derived` | Data quality | Filled by a rule, not yet confirmed | Fields a machine pass filled in, and the rule that filled each one, that nobody has confirmed | none |
| `dq_purchases` | Data quality | Purchases with gaps | Purchases with a placeholder number, a missing or implausible order date, no web address, a zero-cost item, or no items | none |
| `dq_locations` | Data quality | Where items are | Live items by storage location, with items and total cost | none |
| `pr_outstanding` | Purchasing and receiving | Not yet arrived | Purchases with items still ordered or missing: vendor, seller, order date, days waiting, items outstanding and their cost; oldest first | Overdue after (days) (default 21) |
| `pr_spend` | Purchasing and receiving | Spending | Period x vendor: purchases, items, item cost, shipping, sales tax and total, over purchases with a live item | From, To, Period (month/quarter/year, default month) |
| `pr_sources` | Purchasing and receiving | Vendors and sellers | One row per vendor, and per seller a purchase has named: purchases, items, total spent, first/last order date | none |
| `pr_received` | Purchasing and receiving | Received | Arrival day x vendor, from acquisition-status history: items and total cost | From, To |
| `sl_offered` | Selling | On offer | Active and paused listings, items and sales lots, by venue: asking price against cost basis, and days listed | none |
| `sl_sales` | Selling | Sales | Month x venue: orders, gross, fees, net, cost basis and gain, for sales orders placed in range | From, To |
| `sl_fulfilment` | Selling | To ship | Orders still open and unshipped -- pending, paid or packed -- oldest first | none |
| `sl_aging` | Selling | Held and not offered | Live items received and held, not on offer or in an open lot, by months since received and kind | none |
| `sl_auctions` | Selling | Auctions | One row per auction, by status; for a settled one: lots, sold, unsold, hammer total and fees | none |
| `mn_basis` | Money | Cost basis | Status x disposition of every live item: items and total cost | none |
| `mn_tax` | Money | Sales tax paid | Period x vendor: purchases and sales tax paid, over purchases with a live item | From, To, Period (month/year, default month) |
| `mn_value` | Money | Recorded value | Per item kind: live items, the ones with a recorded value, their cost and value, the difference, and items without one | Status (default received), Disposition (default held) |

The console needs no change to show a report added later -- it renders
whatever `GET /api/reports` lists.

### Parameters and drill-downs

A report's own parameters -- `cb_holdings`'s status and disposition,
`pr_outstanding`'s "Overdue after (days)" -- are a dropdown or a box, the same
as the inventory filter panel, and the help band explains each one. An
emptied From or To date means no bound on that side -- an open range is a
normal question. Leaving any other parameter empty is refused ("Enter a value
for ..."), rather than silently running that parameter's default. The report and its parameters are kept in the
address (`/management/reports?report=pr_outstanding&overdue_days=30`), so a result can be
bookmarked or reopened; Run writes only the parameters that differ from their
defaults.

A row that names items links to where they are: the inventory search narrowed
to that row's values, or Receiving's `?order=<id>`. A percent cell in
`dq_completeness` links to the inventory search with `missing=<field>` added
-- the items with that field empty, kind-aware, the same test the report
itself counts by. The inventory page shows that filter as a chip reading
"Missing: <field>"; clicking the chip removes it. A row standing for exactly one item, rather than a count of
many, links straight to that item's editor with `?item=CC-######`; opening the
inventory search on that address opens the item on load, not only from a
click in the table.

### Export workbook and Print

**Export workbook** downloads the same result as an `.xlsx`: the report's
title and the parameters it ran with above the table (an empty From or To
reads "any"), a totals row below in
bold, then any notes -- the same layout `python -m app.reports run ...
--workbook` writes.

**Print** opens the browser's print dialog on the report as shown -- the same
rows, sort and parameters. The console menu, help band and parameter form are
hidden on paper; a heading printed only there names the report, its
parameters in words, when it ran and its row count. The table's header row
repeats on every page and a row is never split across one; a report of more
than six columns prints landscape. Nothing is carried by color alone -- an
overdue row reads "Overdue" in its own cell -- it is marked in words.

### The command line

The same registry, for a report wanted from a script or before the console is
open, from `backend\` with the `ccwebdb` conda environment active:

```cmd
cd backend
python -m app.reports list
python -m app.reports run pr_outstanding --param overdue_days=30 --workbook C:\Users\you\Downloads\outstanding.xlsx
```

`list` prints every registered report's id, group and title. `run <id>`
prints the report as a plain-text table -- title, parameters, header, rows,
totals, notes -- and `--param name=value` (repeatable) sets its parameters.
`--workbook FILE` also writes the same `.xlsx` the console's Export downloads.
Read-only, like everything here, so there is no `--commit`.

## Reference vocabularies

Classifiers -- grades, mints, denominations, metals and the rest -- are rows
in reference tables, not free text. Values are added from the dropdown where
they are needed, and renamed, retired, merged or reordered on the
**Vocabularies** page (`/management/vocabularies`), which does not create values.

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
  (`app/references.py`, `retirable`).
- **Merge into...** replaces a value with another for good. The page previews
  the effect: every item holding the old value moves to the kept one, the old
  label, code and aliases become aliases of the kept value (so ratings and
  searches using the old word still find it), and the old value is
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
`currency` or `any`) on `series`, `error_type`, `item_attribute` and
`grade_designation` tell a
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
shipped catalog and out of a seed export by default (below).

### Provenance and reference data

Every reference row records how it came to exist: `seeded` (shipped),
`derived` (inferred from the collection's data), or `manual` (typed by a
person). A
machine guess must never be indistinguishable from a curated fact. Review
`derived` rows before anyone exports them.

**Before adding reference data, check it is free to use.** The catalog is
sold, so reference data shipped inside it is redistributed. Facts are safe --
who held an office and when, design series names and year spans, mint
specifications, legislated compositions, common collector nicknames. A
publisher's *arrangement* is not: Friedberg numbering, Pick numbering,
price-guide values, or any catalog's mapping of attributes to its own
numbers. See the Reference data section of `CLAUDE.md`.

**Loading and exporting seed files.** The shipped vocabularies live in
`backend\data\reference\`. A seed load adds and updates rows, but never
brings back a merged value, never overwrites a row marked `manual`, and never
restores a retired alias.

```cmd
python -m app.seeding load                                  load every seed file
python -m app.seeding load --only <table>...                only those tables
python -m app.seeding load --data-dir <dir>                 from another folder
python -m app.seeding export --out <dir>                    write seed files, seeded rows only
python -m app.seeding export --out <dir> --source seeded manual --include-inactive
```

`--source` takes any of `seeded`, `derived` and `manual` (default `seeded`);
`--include-inactive` also exports retired values. A schema release runs
`load` after `alembic upgrade head` (*Applying a schema release*).

### Other names (aliases)

A value's label is the standard term -- UCAM, United States Note, Walking
Liberty Half Dollar. What people write is an **alias**: Ultra Cameo, Legal Tender,
Walker. The Vocabularies page lists and edits every vocabulary's aliases, and
an alias works at once:

| Where | What it does |
|---|---|
| The search box | finds items of that value |
| Dropdowns | a long list has a **Find** box; typing an alias offers the value with the alias in brackets, and Enter picks the first |

**Two values may share an alias** ("Cartwheel" is any large silver dollar).
Search finds both, and the console marks a shared alias. **An alias may not be a value's own label or
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

## Lists: Friedberg numbers, sellers, vendors, storage locations

These four lists are not vocabularies, but they grow the same way: a row is
added inline while something else is being done (a Friedberg number while a
note is identified, a seller or vendor while a purchase is entered, a storage
location from the location picker on New item, the item editor or
Receiving). **Lists** (`/management/lists`, a tab each, `?tab=` in the
address) is where a slip made there is corrected
(`docs/specs/list-maintenance-design.md`).

Each tab lists its rows with a search box and how many records use each row.
**Edit** changes a row in place; **Delete** is offered only for a row nothing
uses, and the server refuses the rest (every foreign key into these tables is
`RESTRICT`). A storage location any item has ever been in stays, as part of
that item's history. Locations of kind `consigned` and `sold` are made by the
auction and sale code: they cannot be added from the picker or edited here.
Merging duplicate vendors is done by `app.vendor_cleanup` (*Cleaning up
purchase sources*).

## Backing up and restoring

A backup is only as good as the proof that it restores. This section says
which kind of backup to take for which purpose, how to prove it, and how to
restore. **Photograph bytes are in no database backup** -- they live under
`MEDIA_ROOT` (*Settings*); back that folder up separately.

| Kind | Command | Use it for |
|---|---|---|
| **`pg_dump` file** | `pg_dump -Fc` (see *Applying a schema release*, step 1) | **the** backup before a migration or any risky `--commit`; a file that can leave the machine |
| **Workbook** | `python -m app.workbook_backup export` | a backup a person can open, read and correct, and the only bulk way in or out of the database |
| **Database copy** | `python -m app.backup` | a working copy beside live, on this server or any SQLAlchemy URL |

Backups are kept beside the repository, in `ccwebdb-backups\` (on this
machine `%USERPROFILE%\dev\ccwebdb-backups\`); the workbook export writes
there by default.

**Why `pg_dump`, not `app.backup`, before a migration.** `pg_dump` copies the
database exactly as it is, `alembic_version` included. `app.backup` builds the
copy's schema from the *checked-out SQLAlchemy models*: with a release's code
checked out before live is migrated, the copy holds the new schema under the
old revision, and cannot be migrated or restored faithfully.

**A backup is proved by restoring it and comparing, never by listing it.** A
`pg_restore --list` or `python -m app.backup --list` shows that a file or copy
exists and its size, not that it holds the collection: a copy that aborted
partway still lists at a plausible size, and one such copy held no inventory
items and no purchase orders at all. Restore into a new database and run:

```cmd
python -m app.workbook_backup compare postgresql+psycopg://ccwebdb:<password>@localhost:5432/<restored name>
```

`compare` reads both databases' tables from the databases themselves, not the
models, so it works whatever code is checked out. It checks the migration
revision and every row of every table through a digest, prints `identical`
and exits 0, or prints `DIFFERS <table>: <n> rows vs <m>` for each table that
differs and exits 1. For a backup taken moments ago, any difference is a
failed backup.

### The database copy (`app.backup`)

```cmd
python -m app.backup                      copy to a new ccwebdb_bak_<time> database
python -m app.backup --name before_split  copy to a new database of that name
python -m app.backup --to <url>           copy anywhere SQLAlchemy reaches
python -m app.backup --list               list the local ccwebdb_bak* databases and their size
python -m app.backup --verify <name>      row counts of a local copy against live
```

Every table is copied, `alembic_version` too (it has no model, so it is read
from the database itself). The copy is portable: another engine is a
different `--to` URL. `--verify` compares each model table's row count and
prints `<name> matches the source on every table`, or `MISMATCH` with the
tables that differ and exits 1. A copy that predates a migration reports
`OLDER SCHEMA -- <name> has no <table>`: it records its own moment but cannot
be compared table for table. An older copy is expected to differ in counts; it
records the collection as it was. `--list` only lists names beginning
`ccwebdb_bak`, so a copy made with `--name` is not listed.

### Restoring

A restore is always **into a new database**, never over the live one, so the
original stays untouched until the replacement is checked, and switching back
is a one-line edit.

- **From a `pg_dump` file:** `createdb` a new database and `pg_restore` into
  it, as in *Applying a schema release*, step 1.
- **From a workbook:** see *The workbook backup* below.
- **From an `app.backup` copy:** point `DATABASE_URL` at the copy and copy it
  again:

  ```cmd
  set "DATABASE_URL=postgresql+psycopg://ccwebdb:<password>@localhost:5432/<copy name>"
  python -m app.backup --name ccwebdb_restored
  set "DATABASE_URL="
  ```

Then check the result with `compare` (above), set `DATABASE_URL` in `.env` to
the new database, and restart the servers
(`.\scripts\ccweb_shutdown.cmd /keepdb`, then `.\scripts\ccweb_startup.cmd`).

### The workbook backup

The workbook is the only bulk way in or out of the database. Besides being a
backup a person can read, it is how many rows are corrected at once: export,
edit the sheets, import into a new database, `compare`, then switch to it.

`app.workbook_backup` writes every table to one `.xlsx`: an **About** sheet
(format, time, migration revision, rows per table), a **Columns** sheet
(each column's type, whether it may be empty, what it references), then one
sheet per table in foreign-key order. Ids and foreign keys are exactly as
stored, so relationships survive. The tables are read from the database
itself, not the models, so none is missed.

```cmd
python -m app.workbook_backup export                        live -> ccwebdb-backups\ccwebdb_<time>.xlsx
python -m app.workbook_backup export --out <file.xlsx>      ... to a file you name
python -m app.workbook_backup import <file.xlsx> --to <url> workbook -> an empty database
    [--unknown-for-missing]                                 a vocabulary link to nothing -> Unknown
python -m app.workbook_backup compare <url>                 live vs <url>, every row of every table
```

**Reading and editing it.** An empty cell is NULL; an empty string is written
`""`. Timestamps are ISO text with their time zone (one typed with no zone is
read as local time). JSON is JSON text, and `null` there is JSON's null, not
an empty value. Columns headed `(computed)` -- `total_cost`, `sales_tax`, ...
-- are for reading; the import ignores them and the database recomputes them.
Change values freely; keep the header row and the id columns as they are.

**Column widths are remembered.** Each export sizes its columns from
`backend\data\workbook_widths.json`, by sheet and column name, so a width stays
with its column when a migration adds or moves one. To change them, resize the
columns in an export, save it, and run:

```cmd
python -m app.workbook_backup widths <file.xlsx>
```

A sheet you resized replaces that sheet's remembered widths; the others stay.
Commit the widths file so the next export uses them.

**Restoring from it.** Always into a new database, from `backend\`:

```cmd
set PGDATABASE=postgres
..\scripts\ccweb_psql.cmd -c "create database ccwebdb_restored"
set PGDATABASE=
set "DATABASE_URL=postgresql+psycopg://ccwebdb:<password>@localhost:5432/ccwebdb_restored"
python -m alembic upgrade head
set "DATABASE_URL="
python -m app.workbook_backup import <file.xlsx> --to postgresql+psycopg://ccwebdb:<password>@localhost:5432/ccwebdb_restored
python -m app.workbook_backup compare postgresql+psycopg://ccwebdb:<password>@localhost:5432/ccwebdb_restored
```

The import refuses a database at a different migration revision than the
workbook's, the live database (recognised by the server, not by how the URL
is spelled), and any database that already holds inventory items; it clears
any other rows the migrations put there, and loads everything in one transaction: a refused row (a value that
does not fit its column, a duplicate) names its table and reason and loads
nothing. `compare` then says `identical`, or names each table that differs --
after edits, exactly the tables edited.

**Links that point at nothing.** Before anything is written, every link is
checked against the workbook's own rows. By default the import refuses and
lists each row whose link finds nothing, for example after a vocabulary
row was deleted from its sheet. Fix the sheet, or add `--unknown-for-missing`:
a link into a vocabulary (grade, mint, status and the like) is then pointed
at that vocabulary's **Unknown** row, and each substitution is printed:

```
  UNKNOWN inventory_item id 1: grade_id 99999 -> grade Unknown (0)
  1 link(s) set to Unknown
```

The Unknown row is the vocabulary's own `unknown` row if its sheet has one
(item status does). Otherwise it is added with id 0 -- real ids start at 1 --
labeled Unknown, sorted last, marked `manual` so a seed load leaves it
alone. Find the items that point at it and correct them in the console. A
vocabulary whose rows need more than a code and a label -- a denomination's
currency and face value, a mint's mark -- gets no invented row: the import
names the columns, and you add an `unknown` row to that sheet yourself. A link
to anything that is not a vocabulary (an item, an order, a purchase) is always
refused; there is no Unknown item.

## Applying a schema release

A schema release is a code change that adds an Alembic migration. It is the
riskiest routine operation, because it rewrites the live record in place, so
it is applied to live only after it has been rehearsed on a restored copy of
live, and only with a proven backup in hand.

**The migrations.** `backend\alembic\versions\` holds one baseline revision,
`3f9d1c7a2b64`, which builds the whole schema from
`backend\alembic\baseline.sql`, and ordinary revisions on top of it. From
`backend\`, `python -m alembic heads` names the newest and
`python -m alembic current` what a database is at. Reference data a feature
needs is loaded by `python -m app.seeding load`, not by the migration, so
every release is schema first, reference data second.

`pg_dump`, `pg_restore`, `createdb` and `dropdb` connect with the standard
`PG*` variables, set in step 1; set them in each window you use.
`scripts\ccweb_psql.cmd` fills in any it finds unset with the development
defaults, `PGPASSWORD` included.

1. **Back up with `pg_dump`, and prove the dump by restoring it.** With the
   code that live is running still checked out, from `backend\`:

   ```cmd
   set "PGBIN=%CONDA_PREFIX%\Library\bin"
   set "PGHOST=localhost"
   set "PGUSER=ccwebdb"
   set "PGPASSWORD=<password>"
   set "DUMP=%USERPROFILE%\dev\ccwebdb-backups\ccwebdb_pre_release_YYYYMMDD.dump"
   "%PGBIN%\pg_dump.exe" -Fc -d ccwebdb -f "%DUMP%"
   "%PGBIN%\createdb.exe" ccwebdb_rehearsal
   "%PGBIN%\pg_restore.exe" -d ccwebdb_rehearsal --no-owner "%DUMP%"
   python -m app.workbook_backup compare postgresql+psycopg://ccwebdb:<password>@localhost:5432/ccwebdb_rehearsal
   ```

   `PGBIN` is PostgreSQL's binaries inside the active `ccwebdb` environment,
   the same place `scripts\ccweb_env.cmd` finds them. `compare` must print
   `identical`: it checks the revision and every row of every table (*Backing
   up and restoring*). Anything else means the dump is not usable, and
   nothing is applied. Keep the dump until the release has been in use for a
   while; it is the way back.

2. **Rehearse on the restore.** Check out the release's code. Point both the
   application and psql at the rehearsal database, record the item count and
   cost basis, migrate and seed, and record them again:

   ```cmd
   set "PGDATABASE=ccwebdb_rehearsal"
   set "DATABASE_URL=postgresql+psycopg://ccwebdb:<password>@localhost:5432/ccwebdb_rehearsal"
   ..\scripts\ccweb_psql.cmd -c "select count(*), sum(total_cost) from inventory_item where split_at is null and deleted_at is null;"
   python -m alembic upgrade head
   python -m app.seeding load
   ..\scripts\ccweb_psql.cmd -c "select count(*), sum(total_cost) from inventory_item where split_at is null and deleted_at is null;"
   python -m alembic current
   set "DATABASE_URL="
   set "PGDATABASE="
   ```

   The two results must be identical, and `alembic current` must name the
   new head. Clear both variables before going on -- they are what point the
   commands at the rehearsal rather than at live -- and drop the rehearsal
   afterwards (`"%PGBIN%\dropdb.exe" ccwebdb_rehearsal`); it is a full copy
   of the collection.

3. **Stop the servers, keeping PostgreSQL**, so nothing writes while the
   schema changes. From the repo root:

   ```cmd
   .\scripts\ccweb_shutdown.cmd /keepdb
   ```

4. **Upgrade and seed live**, from `backend\`, with `DATABASE_URL` pointing
   at `ccwebdb` (the `.env` value):

   ```cmd
   python -m alembic upgrade head
   python -m app.seeding load
   ```

   Skipping the seed step leaves a feature that needs new reference data
   failing on a missing code.

5. **Check:** `python -m alembic current` names the new head; the item count
   and cost basis match step 2's; every table the migration added is empty.

6. **Restart** once the release's branch is merged into `main` and checked
   out, from the repo root, in a shell nothing else depends on:

   ```cmd
   .\scripts\ccweb_shutdown.cmd
   .\scripts\ccweb_startup.cmd
   ```

   Run `ccweb_startup.cmd` bare -- never piped or redirected, or the call
   hangs (*Runtime operations*). The backend runs uvicorn without
   `--reload`, so until it restarts it serves the code that was running
   before, whatever the database's schema. Then confirm with
   `.\scripts\ccweb_status.cmd` (exit 0), open the shop catalog
   (`GET /api/catalog`) and the console, and reload any open page.

## Things that are deliberately not configurable

- **Per-permission roles.** Two roles, one boundary.
- **Password reset by email.** No mail configuration exists.
- **Editing an account's email.** It is the account's identity.
- **Deleting a user.** Deactivate instead -- history rows reference the user
  id, and `ON DELETE SET NULL` on those columns exists so that removing a user
  never erases the record that the work was done.
- **A storage location customers can see.** This is an
  authorization boundary: a public listing that leaked the safe-deposit box
  holding an item would be a security failure. It is enforced by
  `routers/catalog.py`, which builds every public response field by field, and
  by the test asserting the result carries no location -- not by the
  `public_catalog` view, which forbids the columns but which no endpoint reads.
