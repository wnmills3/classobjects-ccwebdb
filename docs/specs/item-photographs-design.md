# Photographs on an item: importing them, and putting them right

How photographs get onto items: a command-line pass that files a library of
photographs by filename, and console surfaces for attaching, correcting and
filing them by hand. The shop serves an item's **primary** photograph
(`routers/catalog.py`), so every rule here is ultimately about what a buyer
sees.

## Model

- `image` is a stored photograph, content-addressed (`image.sha256` unique),
  with derivatives in `image_derivative`. `imaging.cleanse` strips all
  metadata at ingest and re-reads the written bytes to prove it is gone --
  photographs of valuables carry the GPS position of where they are kept.
  `captured_at` is the one field kept, because capture order helps match
  photographs to items. Originals are never served publicly; public requests
  are answered from derivatives.
- `item_image` links a photograph to an item, with an `image_role`
  (obverse, reverse, edge, detail, slab, certificate, group, packaging,
  unassigned), `is_primary` and `sort_order`. `inventory_item_id` is
  **nullable on purpose**: a photograph exists before anyone has decided what
  it shows, and must be storable and browsable in that state.
  `uq_item_image_primary` (partial unique) allows at most one primary per
  item; `uq_item_image_pair` one link per image and item.
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
  decision, and the console is where decisions are made. Console uploads
  file at `sort_order` 0, so the primary check is separate from the slot
  check.
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
  upload, change role, make primary, and **Remove**, which detaches and
  never deletes the photograph. It reads from the server after every write,
  never from the editor's draft.
- **`/management/photos`**: unattached photographs, most recent capture first
  (nulls last), each with an item picker that searches by item code across
  coins and currency. Where the pass's leftovers are filed, and where a
  photograph detached from the wrong item waits.
- **Receiving**: `ReceiptPanel` uploads photographs for the one item being
  received; the first is primary. A failed upload never rolls back the
  receipt.

All of these go through `ForSaleNotice` / the for-sale refusal: every link
change is guarded by `sale_state.guard` (`for-sale-guards-design.md`).

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/images` | upload (multipart); optionally attach with `inventory_item_id`, `image_role`, `is_primary` |
| `GET /api/images` | links for `inventory_item_id`, or `unattached=true`; **exactly one** is required, otherwise 422 |
| `GET /api/images/{sha256}/{kind}` | serve a derivative |
| `POST /api/images/{image_id}/links` | attach to an item, with role and primary |
| `PATCH /api/image-links/{link_id}` | change role, or make primary |
| `DELETE /api/image-links/{link_id}` | **detach**; the photograph survives |
| `DELETE /api/images/{image_id}` | destroy the photograph, its derivatives and its stored bytes |

`GET /api/images` requires a filter because an unfiltered list of every
photograph is a page nobody wants and a query that grows without bound.

The link routes live under **`/api/image-links`** rather than
`/api/images/links/...`, because `/api/images/{sha256}/{kind}` would
otherwise be separated from them only by route declaration order.

Detach and delete are separate endpoints so that correcting a filing error
can never destroy a photograph.

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
- Each guard call site fails a named test when deleted.
- Frontend: `PhotosPanel` and the Photos page, rendered with `strict: true`.
