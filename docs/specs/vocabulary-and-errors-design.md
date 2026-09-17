# Vocabularies that fit the item, and recording errors

Design. Status: **agreed with the owner 2026-09-17**; not yet built.

## The problem

Three reports from the owner, all while entering banknotes, and all the same
shape: **a form offers choices that cannot apply to the item in hand, and
hides or buries the ones that can.**

- The retired Manage page offered a strike type and a metal for a banknote
  and listed all 77 grades with the 30 note grades below 45 coin ones. That
  page is gone (commit `33a8f85`), and the item editor's metal box with it
  (`b4ee76f`), but the same gaps remain elsewhere.
- **A denomination picker offers coin and note denominations together.** The
  table already records which is which; nothing filters on it.
- **Attributes cannot be added while entering.** Every other picker offers
  "+ Add a new value...", and `POST /api/reference/{table}` accepts one, but
  the attribute picker sets `allowAdd={false}` -- so an attribute the shipped
  vocabulary lacks cannot be recorded at the moment it is in front of you.
- **Attributes read as unsorted**: Star Note, Fancy Serial, Consecutive, Low
  Serial, ... Error Note, Web Press. They are ordered by group, then by an
  assigned number, which is not an order anyone scanning the list can see.
- **Errors cannot be recorded at all.** `GET` and `PUT
  /api/inventory/{id}/errors` are built and tested; no page, and not even
  `owner/api.js`, calls them. `item_error` holds 0 rows, and 28 error types
  (16 coin, 11 note, 1 both) sit unused.

## Decisions

Made with the owner, 2026-09-17.

| Question | Decision |
|---|---|
| Picker order | **Alphabetical by label**, except grades and the four lifecycle vocabularies |
| Which lifecycles keep their order | `item_status`, `disposition`, `sales_order_status`, `shipment_status` |
| Where ordering is decided | **The API**, so every client agrees |
| Coin vs note vocabularies | **Mutually exclusive where they differ**, from the fact already recorded on the row |
| Adding a missing value mid-entry | **Type a label only**; code derived, kind inferred, group optional |
| Where a value can be added | The item editor, **New item (New purchase)** and **Receiving** |
| Recording errors | In the item editor, **New item** and **Receiving**, with a note per error |
| Errors on a new item | **Saved after the item is created**, with a visible retry if that second step fails |
| Error types | Can also grow with use, the same way attributes do |

Rejected:

- *Adding `applies_to` to `denomination`.* Proposed, then withdrawn when the
  column turned out to exist: `denomination.kind` is `coin` or `note` and is
  populated for all 21 rows. Measuring first removed a migration.
- *Filtering denominations on a code prefix* (`usd_note_%`). A naming
  convention is not a recorded fact, and it fails for any country whose codes
  are spelled differently.
- *Alphabetising the lifecycle vocabularies.* "Canceled, Missing, Ordered,
  Received, Returned" scrambles a sequence that is read constantly, and those
  four are exactly the vocabularies the code branches on.
- *Sorting in each picker.* The shop's filters, the console's forms and the
  entry panels would each be one edit away from disagreeing.
- *Inferring an error from description text.* `ErrorType`'s own docstring
  forbids it: a machine guess must never be indistinguishable from a curated
  fact.

## Ordering

`GET /api/reference/{table}` returns values ordered by `sort_order`, then
`code`. It will instead order:

- **by `label`, case-insensitively**, for every vocabulary;
- **by `sort_order`, then `code`** for `grade` (70, 69+, 69, ... is the
  scale's own order and the only one a grader can read) and for
  `item_status`, `disposition`, `sales_order_status` and `shipment_status`.

The exception list lives in one named constant beside `_CODE_KEYED_TABLES` in
`backend/app/routers/reference.py`, which already names those four for a
related reason: they are the vocabularies the application branches on.

`sort_order` stays on every table and stays editable -- it is what orders the
exceptions, and the Vocabularies page may expose it later. For an
alphabetical vocabulary it simply stops being read.

**Consequence worth stating:** a value added mid-entry appears in its
alphabetical place immediately, rather than at whatever position its default
`sort_order` of 500 would have put it.

## Vocabularies that fit the item

Two markers already exist, and neither needs a migration:

| Vocabulary | Column | Values |
|---|---|---|
| `denomination` | `kind` | `coin`, `note` |
| `series`, `error_type`, `item_attribute` | `applies_to` | `coin`, `currency`, `any` |

Both already cross the API inside `extra`, so no endpoint changes shape.

**The rule.** A form knows the item's kind. A picker over a marked vocabulary
offers a value when the marker says `any`, or when it matches the item's
side: `currency` on one side; `coin`, `bullion`, `set`, `medal` and `token`
on the other. That two-sided split is what grades and attributes already do
(`kind === 'currency' ? 'currency' : 'coin'`), and `denomination.kind`'s
`note` maps to the currency side.

One helper, `fitsKind(entry, itemKind)`, in `frontend/src/shared/` beside the
reference hook, so the mapping is written once. Each picker passes it as its
`filter`; `ReferenceSelect` already takes one.

**Fields that belong wholly to one side** stay gated by the form, because
the field itself does not apply: metal, strike type, mint and bullion form
for coins; note class, seal colour, Fed district and signature combination
for notes. Today's drift -- the editor kept a metal box the entry form had
dropped -- is why this design names the coin-only set in **one** exported
constant that every form imports rather than each form listing its own.

**The API enforces it too**, as `PATCH /api/inventory/{id}` and the bulk edit
now do for metal: a denomination whose `kind` contradicts the item's kind is
a 422 naming the items, on create, edit and bulk edit, before anything is
written. The screens will not offer it; the guard is for a stale tab or a
script. Live data already satisfies the rule -- 4,143 coins with coin
denominations, 1,078 notes with note denominations, no exceptions -- so no
cleanup precedes it.

## Adding a value while entering

The attribute picker gains "+ Add a new value...", the affordance every other
picker has, with a form of **one field: the label.**

- **Code** is derived: lower-cased, spaces and punctuation to underscores
  ("Mismatched Serial" -> `mismatched_serial`). Derivation happens on the
  client, which then sends both, so what is stored is visible before saving.
- **If that code already exists**, the existing value is selected rather than
  refused. The same name means the same thing, and an operator mid-entry
  should not have to resolve a collision they cannot see.
- **The kind** is inferred from the item being entered: `currency` on a note,
  `coin` otherwise. This is what makes the addition usable -- a value with no
  marker matches nothing and vanishes from the list that created it.
- **The group** (`attribute_group`) is left blank and can be set later on the
  Vocabularies page. A required group at entry time is a question the person
  usually cannot answer yet.
- **Source** is `manual`, as `POST /api/reference/{table}` already records, so
  additions stay distinguishable from the shipped vocabulary and are excluded
  from an export by default.
- **Where:** the item editor, New item and Receiving. `ReferenceSelect`
  already implements adding; this design passes it the extra columns
  (`applies_to`, and `kind` for a denomination) rather than reimplementing it.

**Error types grow the same way**, through the same form. A new error type
with no `applies_to` would disappear exactly as an attribute would.

## Recording errors

`ItemError` allows several errors per item -- a bill is commonly miscut *and*
misprinted -- each with its own free text, and the same type cannot be
recorded twice on one item. `GET`/`PUT /api/inventory/{id}/errors` replace the
whole set. Both exist; this design is the missing interface.

**One component, `ErrorsPanel`, used in three places:**

- **The item editor**, beside the Attributes row.
- **New item**, under the note's own fields.
- **Receiving**, where a bill is inspected as it arrives -- the moment an
  error is most likely to be noticed.

It shows each recorded error as a row: the type, and a note for that error
("miscut at 3 o'clock, 4mm"). A type already recorded is not offered again.
Removing a row removes that error when the panel saves. The type picker is
filtered by `applies_to`, so a note is offered the 11 currency types and the
one that applies to both, never the 16 coin ones.

**Saving:**

| Where | How |
|---|---|
| Item editor, Receiving | The item exists: the panel `PUT`s the whole set when the form saves. |
| New item | The item is created first, then the errors are saved. |

**The two-step case is the one that can go wrong, so it is designed rather
than assumed.** If the create succeeds and the errors call fails, the panel
says so plainly -- the item was created, its errors were not -- keeps what
was typed, and offers **Retry**. It does not silently drop them, and it does
not pretend the item failed: the item code is shown so the entry can be
finished later from the item editor if the retry also fails. A lookup or a
write that fails invisibly is the recurring defect this project has already
been bitten by: `docs/data-import-plan.md`, Amendment I, where a status that
could only be confirmed and never denied left 7,651 items "received" and
nothing receivable, and a renamed column would have dropped 2,800 assessments
without a word.

## Testing

- **Ordering:** the reference endpoint returns attributes alphabetically and
  grades in scale order; a value created with a late `sort_order` still
  appears alphabetically.
- **Fit:** a note's denomination picker offers note denominations only and a
  coin's offers coin ones; a note's error picker offers no coin error type; a
  new attribute added on a note is offered on the next note and not on a coin.
- **API guard:** a coin denomination on a banknote is a 422 naming the item,
  on create, edit and bulk edit, and the bulk case writes nothing at all.
  Mutation-checked: removing the guard makes those tests fail.
- **Adding:** a label alone creates the value with the derived code, the
  inferred kind and `manual` source, and selects it; a label whose code
  already exists selects the existing value and creates nothing.
- **Errors:** a set is saved and read back with its notes; removing a row
  removes that error; the same type cannot be added twice; on New item, a
  failing errors call leaves the item created, the panel populated, the
  message visible and Retry working (the test drives the failure, rather than
  trusting that the branch is right).

## Not in this design

- **Plate numbers and position** for an error note: they live on the note's
  own detail fields already.
- **Automatic error detection** from description text: forbidden by the model.
- **Error-based search filters:** `inventory_search` already has them.
- **Editing `sort_order` or creating values from the Vocabularies page:** a
  known gap, unchanged here. This design only stops `sort_order` being read
  for alphabetical vocabularies.
- **Retiring an attribute or error type:** the Vocabularies page does that.
