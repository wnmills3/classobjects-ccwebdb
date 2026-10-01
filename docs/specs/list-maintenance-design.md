# List maintenance

*2026-09-29.* The owner keeps four lists that are not vocabularies -- the
Friedberg catalog, sellers, vendors and storage locations. Each grows by
inline entry while something else is being done: a Friedberg number while a
note is identified, a seller or vendor while a purchase is entered, a
location while an item is placed. A slip made there -- a Friedberg number
pasted as `3007-` for `3007-L`, a vendor entered twice -- is corrected or
pruned on one management-console page, **Lists** (`/management/lists`,
`pages/Lists.jsx`), for managers only. Vocabularies
(`/management/vocabularies`) and sales platforms (`/management/platforms`)
have their own pages. The page is not called "catalogs": the shop's public
side is the catalog, and the console never links to it.

## The page

One tab per list, kept in the address (`?tab=friedberg`, `sellers`,
`vendors`, `locations`) so a reload or a link lands on it. Each tab lists its
rows, filtered in the browser by a search box, with how many records use
each row. A row is edited in place (**Edit**, then **Save** or **Cancel**);
only the fields changed are sent, a blank as null, and the list is read
again after every write. **Delete** is offered only for a row nothing uses.
The server refuses the rest too -- every foreign key into these tables is
`RESTRICT` -- with a 409 saying how many records hold it.

| Tab | Shown | Editable | "Uses" counts |
|---|---|---|---|
| Friedberg numbers | number, the type it was recorded with, description, confirmed or proposed | number, description; **Confirm** / **Undo confirm** | notes holding it |
| Sellers | name, store link | name, store link | purchases naming the seller |
| Vendors | name, link, kind | name, link, kind | purchases and sales platforms naming the vendor |
| Storage locations | kind, institution, identifier, notes | the same four | items kept there now plus location-history rows to or from it |

A Friedberg number's type -- denomination, note type, series, district,
seal, web press, printing facility -- is what the lookup matches it by and
is not edited here: a type recorded with a wrong attribute is deleted, if
unused, and recorded again from the note.

Correcting a Friedberg number keeps every note that uses it attached -- the
items hold the catalog row, not its text. Undoing a confirmation clears the
row's `verified_at`, so the next lookup offers it as proposed again (see
`receiving-purchases-design.md`, **Use**).

A storage location of kind `consigned` or `sold` is made by the auction or
sale code: its row is not editable, neither kind is offered in the picker,
and the server refuses (422) editing one or changing a location to one. A
location an item has ever been in is part of that item's history, so it
cannot be deleted.

## The form of a Friedberg number

A number is cleaned, then checked, wherever one is saved -- recorded new
(`POST /api/friedberg`) or corrected here -- and again when one is confirmed,
here or by the lookup. `app/fr_format.py` (`normalize_fr`, `fr_problem`) is
the rule the server holds, applied by the request schemas;
`frontend/src/management/friedberg-format.js` (`normalizeFr`, `frProblem`)
applies the same rule as the number is typed, so Save is held back with the
reason shown.

- **Cleaned:** surrounding space and a `Fr.`, `Fr#` or `FR-` label in front
  of the digits are dropped, space around the hyphen is closed up, and the
  district letter is capitalised -- ` Fr. 3005-d ` is kept as `3005-D`. A
  mule's `m` after the district stays lower-case, with any star after it:
  `3007-EM` and `3007-e*m` are kept as `3007-Em` and `3007-Em*`. A seal
  shade at the end is capitalised after one space: `2008-b  lgs` is kept
  as `2008-B LGS`.
- **Checked:** 1 to 4 digits, an optional letter (`1a`), an optional district
  `-A` to `-L`, an optional `m` after the district for a mule (the answer
  the Friedberg web search is asked for when plates are given), an
  optional `*` for a star note, and an optional ` LGS` or ` DGS` for a
  light or dark green seal (`2008-B LGS`, `2008-B* LGS`). The seal is
  already part of a type's identity, so the two shades are two types; the
  suffix keeps their numbers apart. The shade must agree with the seal:
  an `LGS` number whose seal is recorded as anything but Light Green
  (`light_green`), or a `DGS` number on a light green seal, is refused
  (422) when it is recorded, when a row's number is corrected to it, and
  when it is attached to a note whose seal says otherwise. A seal not
  recorded yet contradicts nothing. `3007-` is refused as
  ending in a hyphen, `30070-L` as having five digits, anything else out of
  that form as not a Friedberg number -- each with a sentence saying which
  (422).
- **Confirming a malformed number is refused** (422, "Correct ... before
  confirming it") until it is corrected: a confirmed number is one the next
  lookup attaches in one step. It can still be attached as proposed.

Only the form is known -- never which number belongs to which note, which is
the publisher's arrangement (`CLAUDE.md`, *Reference data*).

## A combination already recorded

Recording a number whose type matches a row already in the catalog is
refused with 409 and the row named: *"That combination is already recorded
as 3007- (row 14)."*, with `existing: {id, fr_number}` beside `detail`. The
Friedberg lookup (`FriedbergLookup.jsx`) then offers **Correct 3007- to
3007-L**, which renames the recorded row and attaches it -- the slip is
repaired where it was noticed, without a trip to the Lists page.

A mule (`3007-Em`) and a star note (`3007-E*`) are types of their own,
so neither is refused by the plain number on file, nor the plain number by
them. Correcting a row's number is refused the same way (409, the row
named) when adding or dropping its `m` or `*` would make it a type already
recorded under another number.

## API

All manager only (`AdminUser`).

| Endpoint | Purpose |
|---|---|
| `GET /api/friedberg/catalog?q=` | every catalog row, ordered by number, with `item_count`; `q` matches part of the number or description |
| `PATCH /api/friedberg/{id}` | `fr_number`, `description`, `verified` (true stamps `verified_at` and the confirming user, false clears both) |
| `DELETE /api/friedberg/{id}` | 409 while a note holds it |
| `GET /api/sellers` | every seller, by name case aside, with `order_count` |
| `PATCH /api/sellers/{id}` | `name`, `store_url` |
| `DELETE /api/sellers/{id}` | 409 while a purchase names the seller |
| `GET /api/vendors` | every vendor, by name, with `order_count` (purchases and sales platforms) |
| `PATCH /api/vendors/{id}` | `name`, `url` (`host` recomputed), `vendor_kind` (null sets `unknown`) |
| `DELETE /api/vendors/{id}` | 409 while a purchase or sales platform names the vendor |
| `GET /api/storage-locations` | every location with `label`, `kind`, `institution`, `identifier`, `notes`, `item_count` |
| `PATCH /api/storage-locations/{id}` | `kind`, `institution`, `identifier`, `notes` |
| `DELETE /api/storage-locations/{id}` | 409 while anything is or was kept there |

Only the fields sent change. Uniqueness is kept the way the create routes
keep it, and a correction onto another row's value is a 409: a Friedberg
number already recorded (naming its row), a seller or vendor name already
used (case aside), or a storage location with the same kind, institution
and identifier (case aside).
