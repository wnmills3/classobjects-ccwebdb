# Photographs on an item: importing them, and putting them right

Design. Status: **agreed with the owner 2026-09-18**; not built.

## The problem

The collection has no photographs in it. `image`, `item_image` and the
unattached count are all **0** against 7,656 items, and the shop serves an
item's primary image (`routers/catalog.py`) -- so every listing the owner makes
today is a listing with no picture.

Two separate reasons, and the design has to answer both.

- **There is no way to add a photograph after receiving.** `api.uploadImage`
  has exactly one caller, `ReceiptPanel`. The receiving screen is the only
  moment a photograph can ever be attached to an item. The owner logs receipts
  quickly and photographs at leisure afterwards, so the one moment the console
  allows is the one moment they are not taking pictures.
- **The existing photographs are outside the system.** Roughly 673 files,
  taken in safe deposit boxes over time, never imported. There is no bulk
  ingest, and no console surface that lists images at all -- the API offers
  upload, serve-bytes and delete, and nothing else.

The schema was built for this and then never used. `ItemImage.inventory_item_id`
is **nullable on purpose**, and its docstring says why: photographs "exist
before anyone has decided what they depict, and must be storable, browsable and
searchable in that state".

## Decisions

Made by the owner during design, 2026-09-18.

1. **One spec covers both halves** -- photographs on an item, and importing the
   backlog -- rather than two sub-projects. They share a model, a writer and a
   review surface, and splitting them would mean designing the same link twice.
2. **The photograph library is a directory, configured by environment
   variable**, defaulting to `photos/` at the repository root and git-ignored.
3. **Filenames carry the item.** `<item_code>_<nn>.<ext>`, e.g.
   `CC-000412_01.jpg`. This is what turns linking 673 photographs from a search
   problem into a parse problem.
4. **`item_code`, not the numeric id.** The code appears throughout the console
   and on holders; a folder of `412_01.jpg` is unreadable to a person, and a
   mistyped numeric id silently names a different coin.
5. **The sequence carries role as well as order.** `_01` is the **obverse**,
   `_02` the **reverse**, `_03` and beyond **unassigned**. `_01` is also the
   **primary**, because the obverse is what the shop should show.
6. **A CLI pass imports, the console reviews.** `python -m app.photo_import`
   walks the library, parses, ingests and links; the console owns judgement on
   whatever the pass could not resolve.

### Rejected

- **Uploading the backlog through the browser.** No filesystem assumptions, and
  it works from any machine -- but the files are already on this one, 673
  through a file picker is a long sitting, and it discards the filename
  convention that makes the import cheap.
- **A watched folder that ingests continuously.** More moving parts than one
  import plus occasional additions justifies, and it turns "what happened to
  that photograph?" into a question about a background process rather than
  about a command someone ran.
- **Skipping files that do not match the convention.** A typo in a code would
  silently drop a photograph. Every file is ingested; only the *link* is
  withheld.
- **Storing the GPS coordinates.** Not considered, and recorded here so it is
  not revisited: `imaging.py` strips all metadata at ingest and re-reads the
  written bytes to prove it is gone, precisely because "photographs of
  valuables routinely carry the GPS coordinates of where they were taken --
  which is to say, of where the valuables are kept". `captured_at` is the one
  field kept, and the existing comment already says why: capture order helps
  link photographs to items.

## The filename contract

```
<item_code>_<nn>.<ext>          CC-000412_01.jpg
```

- **`item_code`** matches `CC-\d{6}` exactly, case-sensitive. `item_code` is
  generated as `'CC-' || lpad(nextval('item_code_seq'), 6, '0')`, so the shape
  is not a guess. A lowercase `cc-` is a miss, not a correction: a parser that
  repairs input teaches the operator that the convention does not matter.
- **`_<nn>`** is a zero-padded sequence of at least two digits. `_1` is a miss.
- **Role and primary follow from the sequence**: `_01` obverse and primary,
  `_02` reverse, `_03`+ `unassigned`. Both `obverse` and `reverse` are seeded
  `image_role` values, along with edge, detail, slab, certificate, group,
  packaging and unassigned.
- **The extension** is whatever `imaging.cleanse` already accepts. The pass does
  not keep a second list of formats that can drift from the real one.

### The five ways a file can fail, and what happens

Nothing is ever dropped. An unlinked photograph is still a stored, browsable
image -- which is exactly the state `ItemImage`'s nullable link exists for.

| Case | Ingested | Linked | Reported |
|---|---|---|---|
| Name does not match the pattern | yes, unattached | no | filename |
| Well-formed code, no such item | yes, unattached | no | filename and code |
| The item is deleted or split | yes, unattached | no | filename, code and which |
| Two files claim the same code and sequence | both | **neither** | both filenames |
| Item already has a photograph at that sequence | yes, unattached | no | filename and what holds the slot |

**Linking neither of two colliding files is deliberate**: with two candidates
and no way to choose, linking one of them is a coin flip presented as a fact.
And **an occupied sequence is never replaced silently** -- a re-shoot is a
decision, and the console is where decisions are made.

## The pass

`python -m app.photo_import`, following `app.vendor_cleanup`'s shape exactly:
**dry run by default**, printing counts and naming every exception; `--commit`
to write.

- **Root** from `settings.photo_library_root`, environment-configurable,
  defaulting to `REPO_ROOT / "photos"` -- mirroring `media_root`, which is the
  existing precedent for a filesystem path in settings. `photos/` is added to
  `.gitignore`.
- **`ingest` moves out of `routers/images.py`** into a domain module that the
  router and the pass both call. A CLI pass importing a router is backwards,
  and `ingest` is not an HTTP concern. The router keeps request handling.
- **Idempotent twice over.** `image.sha256` is unique and content-addressed, so
  a second run re-finds the same image rather than storing it again; and
  `uq_item_image_pair` means the pass must check before linking rather than
  rely on the insert failing.
- **`sort_order` is the sequence number**, so the console shows photographs in
  the order they were taken rather than in whatever order the rows were
  written.
- **An item that is deleted or split is not linked to**, and is reported like
  any other exception. Both states mean the code names something that is no
  longer a thing anyone holds -- `offering_writes.offer` refuses the same two
  for the same reason -- and a photograph filed against one is a photograph
  nobody will find.
- **It reports for-sale items before `--commit` and does not refuse them.** A
  CLI pass has nobody to acknowledge a warning, and threading a flag through it
  would produce a batch job that auto-acknowledges -- worse than no guard,
  because it looks safe. The pass instead names how many affected items are for
  sale, and the operator decides. This is consistent with
  `docs/specs/for-sale-guards-design.md`, which rejected guards inside writer
  modules for the same reason.

## The console

### `PhotosPanel`, in the item editor

Beside `OffersPanel` and `ErrorsPanel`, and built like them: it reads from the
server rather than from the editor's draft, and never writes a link itself.
Thumbnails in `sort_order`, each showing its role and which one is primary,
with upload, set-primary, change-role and remove.

This is the surface the owner asked for: an item can gain a photograph at any
time, not only in the seconds after its receipt was recorded.

### `/owner/photos`, for what the pass could not resolve

The unattached images, most recent capture first, each with an item picker.
Where the leftovers from the import get settled, and where a photograph
detached from the wrong item waits to be re-filed.

### The API cannot express any of this today

There is no list endpoint at all -- only upload, serve-bytes and delete.

| Endpoint | Purpose |
|---|---|
| `GET /api/images` | filtered by `inventory_item_id`, or `unattached=true` |
| `POST /api/images/{image_id}/links` | attach to an item, with role and primary |
| `PATCH /api/image-links/{link_id}` | change role, or make this one primary |
| `DELETE /api/image-links/{link_id}` | **detach**; the photograph survives |

`GET /api/images` **requires one of the two filters** and refuses with 422 when
given neither. An unfiltered list of every photograph in the collection is a
page nobody asked for and a query that grows without bound; making the caller
say which set it wants costs one parameter and removes the question.

The link routes live under **`/api/image-links`**, not under
`/api/images/links/...`. The images router already serves
`GET /api/images/{image_id}/{kind}` with an integer `image_id`, and a literal
`links` segment in that position is a path that only avoids collision by
FastAPI failing to parse `"links"` as an integer. Depending on declaration
order for correctness is a trap; a separate prefix has no ordering to get
wrong.

The last row is the distinction the current API lacks. `DELETE
/api/images/{image_id}` destroys the photograph and its stored bytes; detaching
a mis-filed photograph must not, or a filing error becomes data loss.

### `image_links.py`, the only writer of `ItemImage`

Mirroring `offering_writes` and `lifecycle_writes`. It owns the **primary
swap** -- clearing the existing primary before setting the new one, within one
transaction -- because `uq_item_image_primary` is a partial unique index and
will reject a second primary outright. It is also the single place the pass and
the console share, so the two cannot drift about what a link means.

### All four paths go through `sale_state.guard`

Attaching, detaching, changing the primary and deleting a photograph all change
what a buyer sees, because the shop serves the primary image. The guard exists
(`docs/specs/for-sale-guards-design.md`), `POST /api/images` and `DELETE
/api/images/{id}` already carry an acknowledgement, and the console already has
`ForSaleNotice` for exactly this.

## Testing

**The filename parser is a pure function and is tested as a table**: valid,
lowercase `cc-`, five digits, seven digits, missing sequence, single-digit
sequence, unknown extension, and the `_01`/`_02`/`_03+` role mapping. Pure
because a convention rots quietly, and a table puts every rule in one readable
place.

**The pass**, against a temporary library root: a dry run writes nothing;
`--commit` links what it said it would; a second run changes nothing; unmatched
files land unattached and are named; a duplicate code-and-sequence links
neither; an occupied sequence is skipped rather than replaced.

**The primary swap gets a mutation test.** Remove the clear-the-old-primary
step and `uq_item_image_primary` must reject the write -- which proves the
index is load-bearing rather than decorative.

**Every new guard call site gets the treatment from the previous branch**: each
test must fail when its guard is deleted, confirmed by a mutation pass. Four of
that branch's defects were tests that could not fail, and the cheapest time to
prevent the fifth is now.

**Frontend**: `PhotosPanel` and the unattached page, rendered with
`strict: true`.

## What this does not do

- **No live import.** The pass ships tested against temporary libraries. Its
  first run against the real photographs is a dry run the owner watches.
- **No migration.** `image`, `image_derivative`, `item_image` and `image_role`
  all already exist with the columns this needs.
- **No renaming helper.** The convention is applied to the files by whoever
  takes the photographs; the system reads it and never rewrites it.
- **No EXIF beyond `captured_at`.** Unchanged from today, deliberately.
- **No change to how images are served.** Originals stay unreachable; public
  requests are answered from derivatives, as now.

## Consequences worth knowing

- `ItemImage`'s docstring says "camera filenames carry only a timestamp, so
  linking is a manual, UI-assisted task rather than an import step". Decision 3
  supersedes that: filenames will carry the item. The docstring is updated as
  part of this work, because a comment that describes an abandoned assumption
  is worse than no comment.
- Moving `ingest` out of the router is the second time this feature family has
  pulled a writer into its own module. That is the pattern the codebase is
  converging on, and `image_links.py` follows it deliberately rather than by
  accident.
