# Photographs on an item: importing them, and putting them right

*2026-09-29.* A photograph is how a buyer sees a coin or note and how the
owner confirms which physical piece a record describes. This feature gets
photographs onto items and keeps them filed correctly:

- **In the management console** the owner adds, labels, promotes to primary,
  moves and removes an item's photographs in the item editor's
  **Photographs** panel (`/management/inventory/coins` or `/currency`,
  `PhotosPanel`); attaches
  photographs while receiving an item (`/management/receiving`,
  `ReceiptPanel`); and files photographs that belong to no item yet on
  **Photos** (`/management/photos`).
- **From the command line**, `python -m app.photo_import` files a whole
  library of photographs named by the `CC-######_NN` convention.

The shop serves an item's **primary** photograph and nothing else
(`routers/catalog.py`, no fallback to "the first one"), so every rule here is
ultimately about what a buyer sees. Every change to an item's photographs is
therefore guarded by the for-sale rule.

## Model

- `image` is a stored photograph, content-addressed (`image.sha256` unique,
  the hash of the cleansed bytes), with derivatives (`thumb`, `web`) in
  `image_derivative`. The same photograph stored twice is one row: `ingest`
  returns the existing image, keeping its name. Derivatives are served at
  `/api/images/{sha256}/{kind}`, public so a plain `<img>` works in the shop,
  and addressed by hash rather than id so the unlisted collection cannot be
  enumerated. `imaging.cleanse` strips all
  metadata at ingest and re-reads the written bytes to prove it is gone --
  photographs of valuables carry the GPS position of where they are kept.
  `captured_at` is the one field kept, because capture order helps match
  photographs to items. Originals are never served publicly; public requests
  are answered from derivatives.
- `item_image` links a photograph to an item, with an `image_role`
  (obverse, reverse, edge, detail, slab, certificate, group, packaging,
  unassigned), `is_primary` and `sort_order`. A photograph exists before
  anyone has decided what it shows, and is storable and browsable in that
  state: it is an `image` row with no `item_image` row at all. The
  `inventory_item_id` column is nullable, but `image_links` never writes a
  link without an item.
  `uq_item_image_primary` (partial unique) allows at most one primary per
  item; `uq_item_image_pair` one link per image and item. A photograph
  filed with no `sort_order` -- every console upload, and an attach that
  names none -- goes after the item's last one, so a reverse lists after its
  obverse.
- Every file is re-encoded as it is stored (JPEG, or PNG where it has
  transparency), so a WebP can be filed as a JPEG is. Only the formats
  photographs arrive in are decoded -- JPEG (a phone's multi-picture JPEG
  among them), PNG, WebP, GIF, TIFF and BMP; anything else is refused as
  not a readable image.
  `image.source_ref` is the name the photograph goes by: the file's name
  as uploaded, with the stored format's extension when it was converted
  (`CC-007595_02.webp` is kept as `CC-007595_02.jpg`; `DSC00417.JPG` stays
  as it is), a name made for its place when it was fetched from a web
  address, and renamed when it is filed in another place (below).
- `image_store.ingest` stores a photograph -- original and both derivatives
  -- and is shared by the upload endpoint and the import pass.

## `image_links.py` -- the only writer of `item_image`

The same single-writer rule `offering_writes` keeps for listings, so the
console and the import pass cannot drift about what a link means.

- **The primary swap.** Promoting a photograph demotes the incumbent first,
  in the same transaction; the partial unique index would reject a second
  primary outright.
- **Filling the vacancy.** `attach` makes a new link primary when the item
  has none, even if the caller did not ask: the shop has no fallback to "the
  first photograph", so an item with photographs and no primary shows a
  buyer nothing. Filling a vacancy never demotes anyone; an incumbent is
  displaced only when the caller asks for `is_primary`.
- **Keeping it filled.** `fill_primary_vacancy` promotes the next photograph
  (lowest `sort_order`, then id) when `detach` removes the primary, and when
  `DELETE /api/images/{id}` cascades links away.
- `attach` refuses (`LinkRefused`) a photograph already linked to that item.

## The filename convention

```
<item_code>_<nn>.<ext>          CC-000412_01.jpg
```

Parsed by `photo_names.parse`, a pure function.

- **`item_code`** matches `CC-\d{6}` exactly, case-sensitive -- the shape the
  database generates. A lowercase `cc-` is a miss, not a correction: a parser
  that repairs input teaches the operator the convention does not matter.
  The code rather than the numeric id, because it is what appears in the
  console and on holders, and a mistyped id silently names a different coin.
- **`_<nn>`** is at least two digits, 1 or more. `_1` is a miss.
- **The sequence carries the role**: `_01` obverse and primary, `_02`
  reverse, `_03` and beyond `unassigned`.
- **The extension** is whatever `imaging.cleanse` accepts; the pass keeps no
  second list of formats.

## The import pass

```
python -m app.photo_import [--root PATH] [--commit]
```

`--root` defaults to `settings.photo_library_root` (`photos/` at the
repository root, environment-configurable, git-ignored). **Dry run by
default**: it writes no rows *and no bytes to media storage* -- it runs
`imaging.cleanse` to validate and hash each file, but not `ingest`, which
would store files no rollback can remove. `--commit` writes. The report
prints each exception by name and the counts.

**Nothing is dropped.** In a committing run every file `imaging` accepts is
stored; only the *link* is withheld.

| Case | Stored | Linked | Reported as |
|---|---|---|---|
| `imaging` refuses the file | no | no | `REJECTED`, with the reason |
| Name does not match the convention | yes, unattached | no | `unmatched` |
| No item has the code | yes, unattached | no | `unmatched` |
| The item is deleted or split | yes, unattached | no | `unmatched`, saying which |
| Two files claim the same code and sequence | both | **neither** | `collision`, both names |
| The item already has a link at that `sort_order` | yes, unattached | no | `occupied`, naming the holder |
| A `_01` for an item that already has a primary | yes | yes, not primary | `primary`, naming what kept it |
| This photograph is already linked to the item | already stored | already linked | counted as `already` |

- **A collision links neither file**: with two candidates and nothing to
  choose between them, linking one is a coin flip presented as a fact.
- **An occupied slot is never replaced silently**, and **an existing primary
  is never taken away**: a re-shoot or a change to what a buyer sees is a
  decision, and the console is where decisions are made. A console upload
  files after the item's last photograph: an item's first upload takes slot
  1, and a `_01` for that item is reported as `occupied` and not linked. A
  primary that sits at any other slot is not seen by the slot check, so the
  primary check is separate from it.
- **Idempotent**: a second run re-finds images by hash and existing links,
  and changes nothing.
- `sort_order` is the sequence number, so the console lists photographs in
  shooting order.
- **For-sale items are reported, not refused.** A CLI pass has nobody to
  acknowledge a warning, and a batch job that auto-acknowledges is worse
  than no guard. The report lists the linked items that are for sale.

## The console

- **`PhotosPanel`**, in the item editor beside `OffersPanel` and
  `ErrorsPanel`: thumbnails in `sort_order` with role and primary shown;
  add, change role, make primary, **Move** to another item, and **Remove**,
  which detaches and never deletes the photograph. **Nothing in it writes
  until the editor's Save** -- except a new kind of photograph, which the
  role picker's **+ Add a new value...** creates at once by its name (code
  derived, as for attributes); choosing it for the photograph is still held.
  A new photograph is added from a file, dropped onto the picker, pasted from
  the clipboard with **Ctrl+V** while the picker has focus, or from a **web
  address**, and listed as not saved yet, with Discard. A dropped file that
  is not an image refuses the whole drop with a message naming it. A paste
  takes only the clipboard's image files; a paste carrying none is left
  alone, so text still pastes wherever it was aimed. A new role, a new
  primary, a removal or a move for a filed photograph shows in place, marked
  not saved yet, with Undo; choosing the saved value again drops it. A move
  names the other item by its code (the Photos page's `ItemPicker`) and
  refuses the item the photograph is already on. Save applies them after the
  fields, the Friedberg number and the errors, under the editor's one
  for-sale acknowledgement: roles and the new
  primary first, then moves, then removals (so the server never fills a
  vacated primary over the one chosen), then new photographs. Whatever
  fails stays held with its reason. The panel reads from the server again
  after Save. A new photograph says **what it shows** with its own picker,
  beside it in the held list -- never one beside the add controls, which
  read as the label of the photograph listed above it. It starts as
  Obverse, then Reverse, whichever the item lacks once held changes are
  counted; once the item has both it starts empty, the panel asks, and Save
  waits until every held photograph says what it shows.
- **`/management/photos`**: unattached photographs, most recent capture first
  (nulls last), each with an item picker that searches by item code across
  coins and currency. Where the pass's leftovers are filed, and where a
  photograph detached from the wrong item waits.

**Moving a photograph** (`image_links.move`) keeps its role, files it after
the target's photographs, makes it the target's primary only when the target
had none, and gives the item it left its next photograph as primary. Both
items are checked by the for-sale guard, under one acknowledgement.

**Named for its place.** A photograph moved, or filed from
`/management/photos`, is renamed for its new place (`CC-008079_02.jpg`) when
its name was another item's place -- a real item's code and a position -- and
it is filed on no other item (`image_links.name_for_place`). A camera's
`DSC00417.JPG` keeps its name, and so does a group photograph shared by
several items.

**Receiving**: `ReceiptPanel` uploads photographs for the one item being
received, chosen from a file, dropped onto the picker, or pasted with
Ctrl+V; the first is primary, and none is given a role. They are sent after
the receipt is recorded, under the receipt's own for-sale acknowledgement. A
failed upload never rolls back the receipt: the panel stays open naming each
file that failed.

All of these go through `ForSaleNotice` / the for-sale refusal: every link
change is guarded by `sale_state.guard` (`for-sale-guards-design.md`).

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/images` | upload (multipart); optionally attach with `inventory_item_id`, `image_role`, `is_primary`. Re-uploading a photograph the item already has updates its role (when one is sent) and primacy rather than refusing |
| `POST /api/images/from-url` | fetch from a web address and file on an item (below) |
| `GET /api/images` | links for `inventory_item_id`, or `unattached=true`; **exactly one** is required, otherwise 422 |
| `GET /api/images/{sha256}/{kind}` | serve a derivative |
| `POST /api/images/{image_id}/links` | attach to an item, with role and primary |
| `PATCH /api/image-links/{link_id}` | change role (an explicit null clears it, an omitted field is left alone), or make primary |
| `POST /api/image-links/{link_id}/move` | file on another item (`inventory_item_id`), placed, promoted and renamed there |
| `DELETE /api/image-links/{link_id}` | **detach**; the photograph survives |
| `DELETE /api/images/{image_id}` | destroy the photograph, its derivatives and its stored bytes |

`GET /api/images` requires a filter because an unfiltered list of every
photograph is a page nobody wants and a query that grows without bound.

The link routes live under **`/api/image-links`** rather than
`/api/images/links/...`, because `/api/images/{sha256}/{kind}` would
otherwise be separated from them only by route declaration order.

Detach and delete are separate endpoints so that correcting a filing error
can never destroy a photograph.

### From a web address

`POST /api/images/from-url` -- `url`, `inventory_item_id`, `image_role`,
`is_primary`, `acknowledge_for_sale`. The server fetches the address
(`app/image_fetch.py`), stores it as any upload is (converted, stripped), names
it for its place on the item -- `CC-000412_02.jpg` -- and files it there, after
the item's other photographs. Only `http(s)` is fetched, only from a host whose
every address is public (not loopback, private, link-local, multicast or
reserved), each redirect checked the same way and at most three, the body no
larger than the upload limit, each connection and each read given 15
seconds and the whole fetch 60. The host is resolved
once and the request sent to the address checked -- the name carried in the
`Host` header and as the TLS server name, so the certificate is still
verified against it -- so a host cannot answer public to the check and
private to the connection (DNS rebinding). A refused fetch, or bytes
`imaging` will not take, is a 422 naming why, and stores nothing; 404 for an
unknown item; 409 when the item already has that photograph; the for-sale
acknowledgement as for an upload. Bytes already stored (the same hash) are
filed under the existing image, which keeps its own name.

**The full-size picture is the one fetched.** A marketplace serves one
photograph at many sizes and the size is part of the address: eBay's
`.../s-l140.webp` is the picture at `.../s-l1600.webp`, forty times smaller,
and the address a person copies is whichever the page showed.
`image_urls.full_size` rewrites an address to the largest form its host
serves -- eBay's `s-l<edge>` to `s-l1600` (asking for more returns the same
file), an NGC certificate picture without its `TN_` prefix or `@<w>X<h>`
suffix -- and leaves any other address alone. `image_fetch.fetch_full_size`
fetches that form, and the address as given when it is refused. The address
kept is the one the bytes came from.

`python -m app.image_enlarge [--commit] [--limit N] [--list]` does the same
for photographs already stored. One is looked at when its address has a
larger form and the picture held is no bigger than that address names (an
eBay picture held larger than its `s-l500` address was fetched at full size
already). The full-size picture is fetched and, when it really is larger,
put behind the same image row (`image_store.replace_content`): the row keeps
its id, so every item keeps the photograph in its place, role and primary;
its bytes, hash, dimensions, renditions and address change, and the files it
no longer uses are deleted once the change is committed. Left as they are,
and counted: a picture no larger than the one held, one the collection
already stores as another image, one that could not be fetched, and one on
an item that is for sale. A dry run fetches nothing; a commit writes every
25, so a run cut short keeps what it had done.

**The address is kept.** `image.source_url` holds the web address a
photograph was fetched from; an uploaded file has none. An image already
stored learns its address the first time it is fetched from one, and keeps
it: a second address serving the same bytes does not replace it. The item
editor and the Photos page show it in the enlarged picture's window, as
"Where it came from".

`python -m app.image_sources MANIFEST.csv FOLDER [--commit]` recovers the
address of photographs stored before it was kept, from what a download left
behind: a manifest with a `url` column, and a folder holding each address's
file under the SHA-1 of the address. Each file is cleansed as an upload is
and matched to the stored image with the same content -- never by item or
file name, so a photograph moved or replaced since cannot take an address
that is not its own. Only an image with no address is written; a dry run
unless `--commit`.

## Tests

- `tests/test_photo_names.py`: the parser as a table -- valid names,
  lowercase `cc-`, five and seven digits, missing and single-digit
  sequences, and the role mapping.
- `tests/test_photo_import.py`, against libraries under `tmp_path` (never the
  real one): a dry run writes no rows and no bytes, asserted separately;
  `--commit` links what it reported; a second run changes nothing; every row
  of the table above.
- `tests/test_image_links.py`: the primary swap, vacancy filling on attach,
  detach and delete. The demotion is load-bearing: remove it and
  `uq_item_image_primary` rejects the write.
- `tests/test_image_move.py` (moving and `name_for_place`),
  `tests/test_image_order_and_names.py` (placement after the last photograph,
  names kept as stored), `tests/test_image_from_url.py` (the fetch guards,
  with a stubbed resolver and client), `tests/test_image_urls.py` (the
  full-size form of an address), `tests/test_image_enlarge.py` (the
  replacement in place, what is left alone, and the full-size fetch on
  adding by address), `tests/test_image_sources.py` (the
  address recovery pass), `tests/test_images.py` (metadata
  stripping against a GPS-tagged fixture, hashing, derivatives, the upload,
  list and serving routes).
- Each guard call site fails a named test when deleted: the upload and the
  deletion in `tests/test_for_sale_guards.py`, the fetch from a web address
  in `tests/test_image_from_url.py`, the attach, re-role and detach in
  `tests/test_image_links.py`, and both ends of a move in
  `tests/test_image_move.py`.
- Frontend: `PhotosPanel.test.jsx`, `Photos.test.jsx` and
  `ReceiptPanel.test.jsx`.
