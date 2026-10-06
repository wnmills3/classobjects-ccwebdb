# Vocabularies that fit the item, and recording errors

Every classifier -- denomination, grade, series, attribute, error type and the
rest -- is a controlled vocabulary, so records stay searchable and consistent.
That only works if the pickers are pleasant to use. So a form offers only the
choices that can apply to the item in hand, in an order a person can read; a
missing value can be added at the moment it is needed, without leaving the
form; existing values are maintained on one page; and an error on a coin or
note can be recorded with a note of its own.

Staff meet this in the management console: the pickers of the item editor (from the inventory pages, and for a new
item on `/management/purchases`),
Receiving (`/management/receiving`), and the Vocabularies page
(`/management/vocabularies`). The shop's filters read the same ordered
vocabularies.

## Ordering

`GET /api/reference/{table}` orders values:

- **by `label`, case-insensitively**, for every descriptive vocabulary;
- **by `sort_order`, then `code`**, for the tables in `_SEQUENCED_TABLES`
  (`backend/app/routers/reference.py`), whose order is their meaning:
  - a **scale**: `grade` (70, 69+, 69, ...) and `denomination` (face value
    ascending, coins then notes, so once filtered by kind a note's
    denominations read $1 -> $1000);
  - a **lifecycle**: `item_status`, `disposition`, `sales_order_status`,
    `shipment_status`;
  - a **curated sequence**: `item_kind` (by how often each kind occurs, so
    coin and currency lead), `signature_combination` (chronological) and
    `sales_fee_kind` (the order a platform statement is read in, with Other
    last).

The response says `sequenced` so clients can tell. Ordering is decided by the
API so that the shop's filters, the console's forms and the entry panels
cannot disagree. A value added mid-entry appears in its alphabetical place at
once. The endpoint is public: a vocabulary reveals nothing about what anyone
owns. `?year=` narrows a term-bounded table (signature combinations) to the
values in office that year; it is not the pairs a note of that *series* can
carry, which `GET /api/friedberg/signatures` answers from the issue facts.

## The Vocabularies page

The console's Vocabularies page (`management/pages/Vocabularies.jsx`) adds
values and maintains existing ones, for every vocabulary, so none of them
needs the database edited by hand.

| Action | Endpoint | Rule |
|---|---|---|
| **Add a value...** | `POST /api/reference/{table}` | See *Adding a value on the Vocabularies page*. |
| **Rename** | `PATCH /api/reference/{table}/{code}` | The label only. The code is the contract (saved filters, bookmarks, integrations) and never changes. Nothing migrates: records refer by foreign key. |
| **Edit details...** (a vocabulary with columns of its own) | same, with `extra` | Changes the vocabulary's own columns -- which items a series is offered for, its years, a mint's mark -- keyed and read as when a value is added. Only the columns sent change; a blank empties one, and is a 422 where the column cannot be empty. A change that breaks a uniqueness rule is a 409 naming its columns. The form (`VocabularyDetailsForm.jsx`) sends only what was changed. |
| **Move** (sequenced tables only) | same, with `sort_order` | Changes the value's position; the page shows a Position column for a sequenced vocabulary. |
| **Retire / restore** | same, with `is_active` | Takes the value out of the pickers; every record using it stays as it is. Refused (409) for a value the application looks up by code: the tables in `_CODE_KEYED_TABLES` (statuses, dispositions, strike type, item kind, fee kind and other lifecycles) and the single values in `_CODE_KEYED_VALUES` (country `US`, note class `frn`, the photo roles a photograph's filename names, and a few more), both in `app/references.py`. Each value carries `retirable` so the page can say so up front. Such a value can still be renamed. |
| **Merge into...** | `POST /api/reference/{table}/{code}/merge` | Moves every item holding the value to another, makes its names that value's aliases, and deletes it. A `dry_run` preview is shown first. Refused for a value another vocabulary or facts table uses, a code-keyed value, or a retired target; items on sale need `acknowledge_for_sale`. |
| **Aliases** | `POST` / `DELETE .../{code}/aliases` | See `item-attributes-design.md`. |

A renamed, moved or otherwise changed value becomes `manual`, so the next
seed load keeps the person's wording, position and details rather than
restoring the shipped ones.

**A page's copy of a vocabulary does not outlive a change made elsewhere.**
The console loads a vocabulary once per page and shares it between pickers
(`ReferenceProvider`). A value merged, retired or added in another tab would
otherwise stay as it was for the life of the page, and a save that picked a
value since merged away is a 422. A copy more than a minute old is fetched
again when a picker over it next appears and when the window gets focus
back; what is held stays shown until the answer arrives, and stays if the
fetch fails.

**A series added from an item is marked for that item's kind.** The series
picker in the item editor sends
`applies_to: sideFor(item kind)` with a value it adds. Left out, the column's
default makes it a coin's series, and `fitsKind` then hides it from the note
picker that added it.

**A retired value stays saveable on an item that already holds it.**
`GET /api/reference/{table}?include_inactive=true` serves retired values so a
form can render an old record, and saving that record sends the same code
back. `references.code_to_id` therefore accepts a retired code when it is the
value the row already holds (`keep`), and refuses any *new* use of it with
"Retired" in the message rather than "Unknown".

## Vocabularies that fit the item

Two markers already on the rows decide which values fit an item's kind, both
arriving in a value's `extra`:

| Vocabulary | Column | Values |
|---|---|---|
| `denomination` | `kind` | `coin`, `note` |
| `series`, `grade_designation`, `error_type`, `item_attribute` | `applies_to` | `coin`, `currency`, `any` |

The rule has two sides: `currency` on one; every other kind (`coin`,
`bullion`, `set`, `medal`, `token` and the rest) on the other. `denomination.kind = note` maps to the currency
side. A denomination is never filtered by code prefix (`usd_note_%`): a naming
convention is not a recorded fact.

`frontend/src/shared/kinds.js` is the one place the split is written:

- `fitsKind(entry, itemKind)` -- whether a value may be offered. Each picker
  passes it to `ReferenceSelect` as its `filter`.
- `sideFor(itemKind)` -- the `'currency' | 'coin'` a value *added* from a
  picker is marked with. It is the exact inverse of `fitsKind`'s test; marked
  by one rule and offered by another, a new value would vanish from the
  picker that created it.
- `COIN_ONLY_FIELDS` (strike type, metal, mint, bullion form, set form,
  variety, and the years -- a note's year is its series year),
  `CURRENCY_ONLY_FIELDS` (face and back plate, printing location, note class,
  seal color, Fed district, signature combination) and
  `fieldFitsKind(field, itemKind)`, which answers true for a field in neither
  set.
- `gradeFitsKind(grade, itemKind)` -- a note is offered only the note grade
  scale, anything else only the coin scales.

The item editor filters its classifier table and its variety box through
`fieldFitsKind`, and a kind change empties what no longer fits, where the
editor can say what went and restore it if the kind changes back
(`kindChange.js`). Identify (`management/identify.js`) asks it whether a kind
has a mint. **Known gap:** a note's own fields are not shown through the helper.
They are listed by hand -- `NOTE_CLASSIFIERS` and `NOTE_TEXT_FIELDS` in
`NoteFields.jsx` -- and
shown on `isCurrencyKind` directly, so their agreement with
`CURRENCY_ONLY_FIELDS` is kept by hand.

**The API enforces the denomination rule too.** A denomination whose `kind`
contradicts the item's kind is a 422 naming the items, on create, edit and bulk
edit, before anything is written. The screens will not offer it; the guard is
for a stale tab or a script.

## Adding a value on the Vocabularies page

**Add a value...** opens a form (`VocabularyAddForm.jsx`) built from what the
server says the vocabulary needs. `GET /api/reference/{table}` carries:

- `fields` -- the vocabulary's own columns beyond code, label and position
  (`app/reference_fields.py`, read from the model, so a vocabulary that gains
  a column gains a box). Each has a `name`, a `label`, `required`, and a
  `kind`: `text` (with `max_length`), `integer`, `decimal`, `boolean`,
  `choice` (one of `choices`) or `reference` (a code from `table`). A
  generated column is not listed. A denomination's are `currency`
  (reference), `face_value` (decimal) and `kind` (`coin` or `note`), all
  required; most vocabularies have none.
- `addable` -- false for the tables in `_CODE_KEYED_TABLES`
  (`references.extendable`). The application acts on each of their values by
  code, so one added would be a status no screen moves an item out of. The
  page says so in place of the button, and the POST answers 409.

`POST /api/reference/{table}` takes `label`, optional `code`, optional
`sort_order` (asked for only where the vocabulary is sequenced; 500 when
absent) and `extra`, keyed by field `name`:

- A column that refers to another vocabulary is sent as that value's code
  under the column's name less `_id` (`currency: "USD"`), the form the value
  comes back in.
- Each value is read for its column: a whole number, a decimal that is a
  finite number, true or false, one of the choices, text no longer than the
  column. Anything else, a name the vocabulary has no column for, or a
  required column left out, is a 422 naming the field. A blank is the same
  as leaving the column out.
- **The code is worked out when none is sent.** A denomination's says what it
  is, as the shipped ones do -- `usd_coin_0_03`, `usd_note_5000` -- and any
  other is named for its label (`Collector's Set` -> `collectors_set`). 409
  when the code is taken, 422 when the label gives none.
- A row that breaks a uniqueness rule is a 409 naming its columns: a second
  denomination of one currency, face value and side is refused, since a face
  value is one denomination however many designs carry it.

The value is recorded `manual`. The page lists it where the server sorts it
and tells the pickers their copy is stale.

## Adding a value while entering

A value can also be created from a field's picker: "+ Add a new value..." at
the foot of a `ReferenceSelect`, which posts to the same endpoint
(staff only, recorded `manual`, so additions stay distinguishable from the
shipped vocabulary and are left out of an export by default). A picker whose
values the code branches on, or that an add-by-label form cannot describe,
does not offer it (`allowAdd={false}`):

- the item editor: kind, status, strike type, grade designation, grading
  service, mint and denomination (`FIXED_VOCABULARIES` in `ItemEditForm.jsx`),
  and the note's class, seal, signatures and Reserve Bank;
- Receiving's Identify: denomination.

A denomination is a face value with a currency and a side, which a label
alone cannot give it: it is added on the Vocabularies page.

For attributes and error types the add form asks for **a label only**
(`labelOnly`):

- **Code** is derived on the client -- lower-cased, spaces and punctuation to
  underscores ("Mismatched Serial" -> `mismatched_serial`) -- and shown before
  saving.
- **If that code already exists**, the existing value is selected rather than
  added again -- the same name means the same thing -- and the picker says so.
  When the picker does not offer it (retired, already on the item, or the
  other kind of item), nothing is selected and the reason is shown instead.
- **Where a value came from is not shown.** `source` (seeded, derived,
  manual) is kept on the row, but the picker shows the label alone: values
  gathered over time may be folded into the base set.
- **Kind** is inferred from the item being entered with `sideFor`, sent as
  `applies_to` in `extra`. A value with no marker would match nothing and
  vanish from the list that created it.
- **Group** is required for an attribute: `attribute_group` is NOT NULL with
  no default, so the form has a group picker (Serial, Variety, Release,
  Qualifier, Verification) and Add stays disabled until one is chosen.

Attributes can be added wherever `AttributesField` appears: the item editor,
and so a new item on a purchase and Receiving's "Confirm or correct fields", which
opens the editor. Error types can be added wherever errors are recorded.

## Recording errors

`item_error` allows several errors per item -- a note is commonly miscut *and*
misprinted -- each with its own free-text note; the same type cannot be
recorded twice on one item. `GET` / `PUT /api/inventory/{id}/errors` read and
replace the whole set. The `PUT` is refused (409) for an item on offer until
`acknowledge_for_sale` is sent, and the set before and after goes into the
item's History like any other field change. An error is never inferred from
description text: a machine guess must not be indistinguishable from a
curated fact. Errors feed the suggested description, right after the grade:
they are what an error note sells on.

**One component, `ErrorsPanel`, in three places:** the item editor, New item,
and Receiving (where a note is inspected as it arrives). Each recorded error is
a row: the type and a note ("miscut at 3 o'clock, 4mm"). A type already
recorded is not offered again, and the type picker is filtered by `applies_to`.

| Where | Saving |
|---|---|
| Item editor | Held with the rest of the edit: adding, removing or re-noting an error enables Save, and Save `PUT`s the whole set (after the fields, under the form's own for-sale acknowledgement). A set that fails stays on screen with its reason. A set changed back to what was read is no change. If the set cannot be read, nothing is offered to edit, so Save can never replace a set nobody saw. |
| Receiving | The panel `PUT`s the whole set on every change (a note's text when its box loses focus): there is no Save there to hold it for. For an item on offer, the acknowledgement is asked once and holds while the panel is open. |
| New item | The item is created first, then its errors are saved. |

**The two-step case is designed, not assumed.** If the create succeeds and the
errors call fails, the form says so plainly -- the item was created, its errors
were not -- keeps what was typed, shows the item code, and offers **Retry**.
Save is disabled until the retry succeeds, so the same piece cannot be
entered twice. It never drops the errors silently and never pretends the item
failed.

## Not here

- Plate numbers and position for an error note live on the note's own detail
  fields.
- Searching by error: the inventory views' `error_type=` filter and
  `error_types` column (`inventory_search`).
