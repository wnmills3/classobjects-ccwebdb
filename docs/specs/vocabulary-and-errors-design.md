# Vocabularies that fit the item, and recording errors

A form offers only the choices that can apply to the item in hand, in an
order a person can read; a missing value can be added at the moment it is
needed; and an error on a coin or note can be recorded with a note of its own.

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
once.

## The Vocabularies page

The console's Vocabularies page (`management/pages/Vocabularies.jsx`) maintains
existing values. It does not create them; see *Adding a value while entering*.

| Action | Endpoint | Rule |
|---|---|---|
| **Rename** | `PATCH /api/reference/{table}/{code}` | The label only. The code is the contract (saved filters, bookmarks, integrations) and never changes. Nothing migrates: records refer by foreign key. |
| **Move** (sequenced tables only) | same, with `sort_order` | Changes the value's position; the page shows a Position column for a sequenced vocabulary. |
| **Retire / restore** | same, with `is_active` | Takes the value out of the pickers; every record using it stays as it is. Refused (409) for a value the application looks up by code: the tables in `_CODE_KEYED_TABLES` and the single values in `_CODE_KEYED_VALUES`. Such a value can still be renamed. |
| **Merge into...** | `POST /api/reference/{table}/{code}/merge` | Moves every item holding the value to another, makes its names that value's aliases, and deletes it. A `dry_run` preview is shown first. Refused for a value another vocabulary or facts table uses, a code-keyed value, or a retired target; items on sale need `acknowledge_for_sale`. |
| **Aliases** | `POST` / `DELETE .../{code}/aliases` | See `item-attributes-design.md`. |

A renamed or moved value becomes `manual`, so the next seed load keeps the
person's wording and position rather than restoring the shipped one.

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
| `series`, `error_type`, `item_attribute` | `applies_to` | `coin`, `currency`, `any` |

The rule has two sides: `currency` on one; `coin`, `bullion`, `set`, `medal`
and `token` on the other. `denomination.kind = note` maps to the currency
side. A denomination is never filtered by code prefix (`usd_note_%`): a naming
convention is not a recorded fact.

`frontend/src/shared/kinds.js` is the one place the split is written:

- `fitsKind(entry, itemKind)` -- whether a value may be offered. Each picker
  passes it to `ReferenceSelect` as its `filter`.
- `sideFor(itemKind)` -- the `'currency' | 'coin'` a value *added* from a
  picker is marked with. It is the exact inverse of `fitsKind`'s test; marked
  by one rule and offered by another, a new value would vanish from the
  picker that created it.
- `COIN_ONLY_FIELDS` (strike type, metal, mint, bullion form),
  `CURRENCY_ONLY_FIELDS` (note class, seal color, Fed district, signature
  combination) and `fieldFitsKind(field, itemKind)`, which answers true for a
  field in neither set.

The item editor filters its classifier table through `fieldFitsKind`; the New
item form gates its strike type, metal and Mint/Variety blocks on it.
**Known gap:** the currency side is not yet wired through the helper. A note's
own fields are listed by hand -- the editor's `NOTE_CLASSIFIERS`, the entry
form's note block and suggestion lists -- gated on the item kind directly. Both
forms show the same four today, but that consistency is kept by hand until
those lists read `CURRENCY_ONLY_FIELDS`.

**The API enforces the denomination rule too.** A denomination whose `kind`
contradicts the item's kind is a 422 naming the items, on create, edit and bulk
edit, before anything is written. The screens will not offer it; the guard is
for a stale tab or a script.

## Adding a value while entering

New values are created from a field's picker: "+ Add a new value..." at the
foot of a `ReferenceSelect`, which posts to `POST /api/reference/{table}`
(staff only, recorded `manual`, so additions stay distinguishable from the
shipped vocabulary and are left out of an export by default). A picker over a
vocabulary the code branches on does not offer it (`allowAdd={false}`):
status and strike type in the item editor, and the note fields.

For attributes and error types the add form asks for **a label only**
(`labelOnly`):

- **Code** is derived on the client -- lower-cased, spaces and punctuation to
  underscores ("Mismatched Serial" -> `mismatched_serial`) -- and shown before
  saving.
- **If that code already exists**, the existing value is selected rather than
  refused: the same name means the same thing.
- **Kind** is inferred from the item being entered with `sideFor`, sent as
  `applies_to` in `extra`. A value with no marker would match nothing and
  vanish from the list that created it.
- **Group** is required for an attribute: `attribute_group` is NOT NULL with
  no default, so the form has a group picker (Serial, Variety, Release,
  Qualifier, Verification) and Add stays disabled until one is chosen.

Attributes can be added in the item editor, and so in Receiving's "Confirm or
correct fields", which reuses it. Error types can be added wherever errors are
recorded.

## Recording errors

`item_error` allows several errors per item -- a note is commonly miscut *and*
misprinted -- each with its own free-text note; the same type cannot be
recorded twice on one item. `GET` / `PUT /api/inventory/{id}/errors` read and
replace the whole set. An error is never inferred from description text: a
machine guess must not be indistinguishable from a curated fact.

**One component, `ErrorsPanel`, in three places:** the item editor, New item,
and Receiving (where a note is inspected as it arrives). Each recorded error is
a row: the type and a note ("miscut at 3 o'clock, 4mm"). A type already
recorded is not offered again, and the type picker is filtered by `applies_to`.

| Where | Saving |
|---|---|
| Item editor, Receiving | The item exists; the panel `PUT`s the whole set when the form saves. |
| New item | The item is created first, then its errors are saved. |

**The two-step case is designed, not assumed.** If the create succeeds and the
errors call fails, the form says so plainly -- the item was created, its errors
were not -- keeps what was typed, shows the item code, and offers **Retry**.
It never drops them silently and never pretends the item failed.

## Not here

- Plate numbers and position for an error note live on the note's own detail
  fields.
- Error-based search filters live in `inventory_search`.
