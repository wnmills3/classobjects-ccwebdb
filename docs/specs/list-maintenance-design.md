# List maintenance

*2026-09-27.* The owner-kept lists that are not vocabularies -- the Friedberg
catalog, sellers, vendors and storage locations -- are listed, corrected and
pruned on one console page, **Lists** (`/management/lists`). Not "catalogs": the
shop's public side is the catalog, and the console never links to it.

## Why

Each of these grows by inline entry while something else is being done: a
Friedberg number while a note is identified, a seller or vendor while a
purchase is entered, a location while an item is placed. A slip made there
had no way back. A Friedberg number pasted as `3007-` instead of `3007-L` could
not be corrected in the console, and typing the right number was refused as a
raw database error, because the same combination of attributes was already
recorded under the wrong number. Vocabularies (`/management/vocabularies`) and
sales platforms (`/management/platforms`) already have pages; these four did
not.

## The page

One tab per catalog. Each lists its rows, filtered by a search box, with how
many records use each row. Every row can be edited in place (**Edit**, then
**Save** or **Cancel**). **Delete** is offered only for a row nothing uses. A
row in use cannot be deleted: every foreign key into these tables is
`RESTRICT`, so the server refuses it too, and says how many records hold it.

| Tab | Shown | Editable |
|---|---|---|
| Friedberg numbers | number, the attributes it was recorded with, confirmed or not, items using it | number, description; **Confirm** / **Undo confirm** |
| Sellers | name, store link, purchases | name, store link |
| Vendors | name, link, kind, purchases and sales | name, link, kind |
| Storage locations | kind, institution, identifier, notes, items | the same four |

A Friedberg number's attributes are what identify its type and are not edited
here: a type recorded with a wrong attribute is deleted, if unused, and
recorded again from the note.

Correcting a Friedberg number keeps every item that uses it attached -- the
items hold the catalog row, not its text. Undoing a confirmation clears the
row's `verified_at`, so the next lookup offers it as proposed again (see
`receiving-purchases-design.md`, **Use**).

## The form of a Friedberg number

A number is cleaned, then checked, wherever one is saved -- recorded new,
or corrected here -- and again when one is confirmed (`app.fr_format`, the
rule the server holds; `friedberg-format.js` applies the same rule as it is
typed, so Save is held back with the reason shown).

- **Cleaned:** surrounding space and a `Fr.`, `Fr#` or `FR-` label in front
  of the digits are dropped, space around the hyphen is closed up, and the
  district letter is capitalised -- ` Fr. 3005-d ` is kept as `3005-D`.
- **Checked:** 1 to 4 digits, an optional letter (`1a`), an optional district
  `-A` to `-L`, and an optional `*` for a star note. `3007-` is refused as
  ending in a hyphen, `30070-L` as having five digits, anything else out of
  that form as not a Friedberg number -- each with a sentence saying which.
- **Confirming a malformed number is refused** until it is corrected: a
  confirmed number is one the next lookup attaches in one step. It can still
  be attached as proposed.

Only the form is known -- never which number belongs to which note, which is
the publisher's arrangement (`CLAUDE.md`, *Reference data*).

## A combination already recorded

Recording a number whose attributes match a row already in the catalog is
refused with 409 and the row named: *"That combination is already recorded as
3007- (row 14)."*, with `existing: {id, fr_number}` beside `detail`. The
Friedberg lookup offers **Correct 3007- to 3007-L** on that refusal, which
renames the recorded row and uses it -- the slip is repaired where it was
noticed, without a trip to the Lists page.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/friedberg/catalog?q=` | every catalog row, with `item_count`; `q` matches the number or description |
| `PATCH /api/friedberg/{id}` | `fr_number`, `description`, `verified` (true stamps, false clears) |
| `DELETE /api/friedberg/{id}` | 409 while an item uses it |
| `GET /api/sellers` | now with `order_count` |
| `DELETE /api/sellers/{id}` | 409 while a purchase names the seller |
| `GET /api/vendors` | now with `order_count` (purchases and sales) |
| `PATCH /api/vendors/{id}` | `name`, `url` (host recomputed), `vendor_kind` |
| `DELETE /api/vendors/{id}` | 409 while anything names the vendor |
| `GET /api/storage-locations` | now with `institution`, `identifier`, `notes`, `item_count` |
| `PATCH /api/storage-locations/{id}` | `kind`, `institution`, `identifier`, `notes` |
| `DELETE /api/storage-locations/{id}` | 409 while anything is or was kept there |

Names stay unique the way their create routes already enforce it: a rename
onto another row's name or number is a 409 naming that row.
