# Vocabularies that fit the item, and recording errors -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Written 2026-09-17. `main` is at `b7757f2`; cut `feat/vocabulary-and-errors` from it.

**Goal:** Every picker offers exactly the values that can apply to the item in
hand, in an order a person can scan; a missing value can be added by typing its
name; and an item's errors can be recorded where the item is entered, received
or corrected.

**Architecture:** Ordering moves into `GET /api/reference/{table}` so every
client agrees, with a named exception set for the vocabularies whose order is
their meaning. Kind-exclusivity reads facts already on the rows
(`denomination.kind`, `applies_to`) through one shared client helper, and is
enforced again in the inventory API. Adding a value reuses the existing
`POST /api/reference/{table}`, driven by a label-only form. Recording errors
reuses the existing `GET`/`PUT /api/inventory/{id}/errors`, which no client
has ever called, through one component used in three places.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, PostgreSQL 18, pytest;
React + Vite, vitest, Testing Library.

**Spec:** `docs/specs/vocabulary-and-errors-design.md`

**No migration.** Every column this plan needs already exists and is
populated: `denomination.kind` (12 coin, 9 note), `applies_to` on `series`,
`error_type` and `item_attribute`, and the whole `item_error` table.

## Global Constraints

- Git: work on `feat/vocabulary-and-errors`; never commit to `main`.
- Scripts and commands: cmd only, never PowerShell; sleep with `ping -n 2 127.0.0.1 >nul`.
- Files: Write/Edit tools; **no shell heredocs**, including for commit messages (`git commit -F <file>`); never `sed` a Windows path.
- Gate: `scripts\ccweb_check.cmd`, run as its own command with output redirected, and **read the script's own exit code** -- a trailing `; echo $?` reports the echo, not the gate. Commit in a separate call. One pytest run at a time.
- Node is not on PATH by default in Git Bash: `export PATH="/c/Users/wnmil/miniforge3/envs/ccwebdb:$PATH"` before `npx`. Python is `/c/Users/wnmil/miniforge3/envs/ccwebdb/python.exe`.
- pytest: `addopts` already has `-q`; do not add another. Escape regex metacharacters in `pytest.raises(match=...)` (ruff `RUF043`).
- Docstrings on every public class, method and function; every function annotated. No new lint ignores.
- Console API calls go in `frontend/src/owner/api.js`, never `shared/api.js` -- except calls the **shop** also needs, which is where `addReferenceValue` already lives.
- Money and rates are `Decimal`/strings, never float.
- Test output stays pristine: no new warnings. Prefer a plain `422` over `status.HTTP_422_UNPROCESSABLE_ENTITY` in new code -- the named constant is deprecated in Starlette and warns.
- **The owner is entering items in the console while this is built.** Never run a pass against `ccwebdb`; tests use `ccwebdb_test`. Frontend edits hot-reload their session, so keep the tree buildable at every commit.
- The backend runs without `--reload`: a backend change is not live until the owner restarts, which is their call, not this plan's.

---

## File map

| File | Responsibility |
|---|---|
| `backend/app/routers/reference.py` | ordering rule: alphabetical, with a named exception set |
| `backend/tests/test_reference.py` | its tests |
| `backend/app/routers/inventory.py` | the denomination-vs-kind guard, beside `_refuse_coin_only_fields` |
| `backend/tests/test_inventory_edit.py`, `test_inventory_create.py`, `test_inventory_bulk.py` | its tests |
| `frontend/src/shared/kinds.js` (new) | `fitsKind`, `COIN_ONLY_FIELDS`, `CURRENCY_ONLY_FIELDS`, `isCurrencyKind` -- one home for the coin/note split |
| `frontend/src/shared/kinds.test.js` (new) | its tests |
| `frontend/src/shared/reference.jsx` | label-only add form; `filter` already exists |
| `frontend/src/owner/pages/inventory/ItemEditForm.jsx` | use `fitsKind` for denomination/series/attributes; drop its private coin-only set |
| `frontend/src/owner/pages/entry/NewItemForm.jsx` | same, plus the two-step error save |
| `frontend/src/owner/pages/inventory/ErrorsPanel.jsx` (+ test) (new) | the errors component, used in three places |
| `frontend/src/owner/pages/receiving/ReceiptPanel.jsx` | mount `ErrorsPanel` per item being received |
| `frontend/src/owner/api.js` | `getItemErrors`, `setItemErrors` |
| `docs/system-administration.md` | how to add a value and record errors |

---

### Task 1: Pickers come back in a readable order

**Files:**
- Modify: `backend/app/routers/reference.py` (the `order_by` in the list endpoint, ~line 168)
- Test: `backend/tests/test_reference.py`

**Interfaces:**
- Produces: `_SEQUENCED_TABLES: frozenset[str]` in `reference.py` -- the vocabularies that keep `sort_order`; everything else is alphabetical by label.

- [ ] **Step 1: Write the failing tests**

Read `backend/tests/test_reference.py` first and follow its client/fixture
style. Add:

```python
def test_a_descriptive_vocabulary_comes_back_alphabetically(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Attributes are scanned by name; their seeded sort_order groups them."""
    labels = [
        value["label"]
        for value in client.get("/api/reference/item_attribute").json()["values"]
    ]

    assert labels == sorted(labels, key=str.lower)


def test_grades_keep_their_scale_order(client: TestClient) -> None:
    """70, 69+, 69 ... is the scale's own order; alphabetical would be noise."""
    codes = [v["code"] for v in client.get("/api/reference/grade").json()["values"]]

    assert codes.index("70") < codes.index("65") < codes.index("50")


def test_a_lifecycle_vocabulary_keeps_its_sequence(client: TestClient) -> None:
    """Ordered -> Received -> ... is read constantly; alphabetical scrambles it."""
    codes = [
        v["code"] for v in client.get("/api/reference/item_status").json()["values"]
    ]

    assert codes.index("ordered") < codes.index("received") < codes.index("canceled")


def test_a_value_added_late_still_sorts_alphabetically(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A mid-entry addition appears where it is looked for, not at the end."""
    client.post(
        "/api/reference/item_attribute",
        json={"code": "aardvark_test", "label": "Aardvark Test", "applies_to": "any"},
        headers=admin_headers,
    )

    labels = [
        v["label"] for v in client.get("/api/reference/item_attribute").json()["values"]
    ]

    assert labels[0] == "Aardvark Test"
```

Check the add-value payload shape against `ReferenceValueCreate` in
`backend/app/schemas.py` before relying on `applies_to` being accepted as a
top-level key (it may belong under `extra`); use whichever the schema defines.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_reference.py -k "alphabetically or scale_order or sequence or added_late"`
Expected: the alphabetical ones FAIL (order is `sort_order`, `code`); the grade
and lifecycle ones PASS already.

- [ ] **Step 3: Implement**

In `backend/app/routers/reference.py`, beside `_CODE_KEYED_TABLES`:

```python
#: Vocabularies whose order is their meaning, so they keep `sort_order`.
#: A grade scale runs 70, 69+, 69, ...; a lifecycle runs ordered, received,
#: canceled. Everything else is a descriptive list that is scanned by name,
#: and alphabetical is the only order a reader can predict.
_SEQUENCED_TABLES = frozenset(
    {
        "grade",
        "item_status",
        "disposition",
        "sales_order_status",
        "shipment_status",
    }
)
```

and replace the `order_by`:

```python
    order = (
        (model.sort_order, model.code)
        if table in _SEQUENCED_TABLES
        else (func.lower(model.label), model.code)
    )
    rows = db.scalars(stmt.order_by(*order)).all()
```

Import `func` from `sqlalchemy` if it is not already imported there.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_reference.py`
Expected: PASS. If another test asserted an order that was `sort_order`-based
for a now-alphabetical table, read it: if it was asserting the old order for
its own sake, update it; if it was asserting something else that happens to
depend on order, make it order-independent.

- [ ] **Step 5: Gate and commit** (`git commit -F <file>`; explain why the two
exception groups exist).

---

### Task 2: The API refuses a denomination that contradicts the kind

**Files:**
- Modify: `backend/app/routers/inventory.py` (beside `_refuse_coin_only_fields`, ~line 905; call sites in `create_item`, `update_item`, `bulk_edit`)
- Test: `backend/tests/test_inventory_edit.py`, `backend/tests/test_inventory_create.py`, `backend/tests/test_inventory_bulk.py`

**Interfaces:**
- Consumes: `Denomination.kind` (`DenominationKind.coin` | `.note`), `ItemKind`.
- Produces: `_refuse_mismatched_denomination(data: dict[str, object], items: Sequence[InventoryItem], db: Session) -> None` -- raises `HTTPException(422)`.

The rule, from the spec: a note takes `kind = note` denominations; every other
item kind takes `kind = coin`. Live data already satisfies it (4,143 / 1,078,
no exceptions), so no cleanup precedes this.

- [ ] **Step 1: Write the failing tests**

In `test_inventory_edit.py`, beside the metal tests added in `b4ee76f`
(read them and match their shape):

```python
def test_a_banknote_cannot_take_a_coin_denomination(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = make_item(db, item_kind_id=code_id(db, ItemKind, "currency"))

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"denomination": "usd_coin_0_25"},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert "denomination" in response.json()["detail"]


def test_a_coin_cannot_take_a_note_denomination(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    coin = make_item(db)

    response = client.patch(
        f"/api/inventory/{coin.id}",
        json={"denomination": "usd_note_1"},
        headers=admin_headers,
    )

    assert response.status_code == 422


def test_a_note_takes_a_note_denomination(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    note = make_item(db, item_kind_id=code_id(db, ItemKind, "currency"))

    response = client.patch(
        f"/api/inventory/{note.id}",
        json={"denomination": "usd_note_1"},
        headers=admin_headers,
    )

    assert response.status_code == 200
```

In `test_inventory_bulk.py`: a bulk edit setting a coin denomination across a
coin and a note is a 422 and **writes nothing** (assert the coin's
`denomination_id` is unchanged). In `test_inventory_create.py`: creating a
`currency` item with a coin denomination is a 422 (read that file for how it
builds a create payload, including the purchase order it needs).

- [ ] **Step 2: Run to verify they fail** (expect 200s where 422s are wanted).

- [ ] **Step 3: Implement**

```python
def _refuse_mismatched_denomination(
    data: dict[str, object], items: Sequence[InventoryItem], db: Session
) -> None:
    """Raise a 422 if a denomination belongs to the other side of the split.

    The same face value exists as a coin and as a note and they are different
    objects, which is what `denomination.kind` records. A picker will not
    offer the wrong one; this is for a stale tab or a script.
    """
    code = data.get("denomination")
    if not isinstance(code, str) or not code:
        return
    kind = db.scalar(select(Denomination.kind).where(Denomination.code == code))
    if kind is None:  # an unknown code is code_to_id's 422 to raise, not ours
        return
    currency_id = db.scalar(select(ItemKind.id).where(ItemKind.code == "currency"))
    # `note` denominations belong to currency items; every other kind --
    # coin, bullion, set, medal, token -- takes `coin` denominations.
    is_note_denomination = kind is DenominationKind.note
    mismatched = sorted(
        item.item_code
        for item in items
        if (item.item_kind_id == currency_id) != is_note_denomination
    )
    if mismatched:
        side = "banknotes" if is_note_denomination else "coins"
        raise HTTPException(
            status_code=422,
            detail=(
                f"denomination {code} belongs to {side}: {', '.join(mismatched)} "
                "cannot take it. Nothing was changed."
            ),
        )
```

Import `Denomination` and `DenominationKind` from `..models`. Call it beside
`_refuse_coin_only_fields` in `update_item` and `bulk_edit`. For `create_item`
the item does not exist yet: compare the payload's `item_kind` against the
denomination's kind directly there, with the same message shape, and put that
check next to the existing classifier resolution.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_inventory_edit.py tests/test_inventory_bulk.py tests/test_inventory_create.py`

- [ ] **Step 5: Mutation check**

Comment out the `raise` in `_refuse_mismatched_denomination`, run the three
new edit tests, confirm they fail, restore, confirm they pass.

- [ ] **Step 6: Gate and commit.**

---

### Task 3: One home for the coin/note split

**Files:**
- Create: `frontend/src/shared/kinds.js`, `frontend/src/shared/kinds.test.js`
- Modify: `frontend/src/owner/pages/inventory/ItemEditForm.jsx` (its private `COIN_ONLY` set and the grade/attribute filters), `frontend/src/owner/pages/entry/NewItemForm.jsx` (`isCurrencyKind`, its gating)

**Interfaces:**
- Produces:
  - `isCurrencyKind(kind: string): boolean` -- `kind === 'currency'`.
  - `COIN_ONLY_FIELDS: Set<string>` -- `strike_type`, `metal`, `mint`, `bullion_form`.
  - `CURRENCY_ONLY_FIELDS: Set<string>` -- `note_type`, `seal_color`, `fed_district`, `signature_combination`.
  - `fitsKind(entry, itemKind): boolean` -- true when the reference entry may be offered for an item of that kind.

`fitsKind` reads whichever marker the entry carries, both of which arrive in
`extra`: `applies_to` (`coin` | `currency` | `any`) on series, error types and
attributes; `kind` (`coin` | `note`) on denominations. An entry with neither
marker fits everything.

- [ ] **Step 1: Write the failing tests** (`frontend/src/shared/kinds.test.js`)

```js
import { describe, expect, it } from 'vitest'

import { COIN_ONLY_FIELDS, fitsKind, isCurrencyKind } from './kinds'

const entry = (extra) => ({ code: 'x', label: 'X', extra })

describe('fitsKind', () => {
  it('offers an applies_to=currency value only to a note', () => {
    expect(fitsKind(entry({ applies_to: 'currency' }), 'currency')).toBe(true)
    expect(fitsKind(entry({ applies_to: 'currency' }), 'coin')).toBe(false)
  })

  it('offers an applies_to=any value to both', () => {
    expect(fitsKind(entry({ applies_to: 'any' }), 'coin')).toBe(true)
    expect(fitsKind(entry({ applies_to: 'any' }), 'currency')).toBe(true)
  })

  // denomination.kind says `note`, not `currency`: the same face value exists
  // as both a coin and a bill and they are different objects.
  it('maps a denomination kind of note to the currency side', () => {
    expect(fitsKind(entry({ kind: 'note' }), 'currency')).toBe(true)
    expect(fitsKind(entry({ kind: 'note' }), 'coin')).toBe(false)
    expect(fitsKind(entry({ kind: 'coin' }), 'coin')).toBe(true)
  })

  it('treats bullion, sets, medals and tokens as the coin side', () => {
    for (const kind of ['bullion', 'set', 'medal', 'token']) {
      expect(fitsKind(entry({ applies_to: 'coin' }), kind)).toBe(true)
      expect(fitsKind(entry({ kind: 'note' }), kind)).toBe(false)
    }
  })

  it('offers an unmarked value to everything', () => {
    expect(fitsKind(entry({}), 'currency')).toBe(true)
    expect(fitsKind({ code: 'x', label: 'X' }, 'coin')).toBe(true)
  })

  it('names the fields a note does not have', () => {
    expect(COIN_ONLY_FIELDS.has('metal')).toBe(true)
    expect(COIN_ONLY_FIELDS.has('strike_type')).toBe(true)
    expect(isCurrencyKind('currency')).toBe(true)
    expect(isCurrencyKind('bullion')).toBe(false)
  })
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npx vitest run src/shared/kinds.test.js`
Expected: FAIL -- cannot resolve `./kinds`.

- [ ] **Step 3: Write the module**

```js
/**
 * The one place the coin/note split is written down.
 *
 * Two markers record it, both already on the rows and both arriving in a
 * reference value's `extra`: `applies_to` (coin | currency | any) on series,
 * error types and attributes, and `kind` (coin | note) on denominations,
 * where the same face value exists as both and they are different objects.
 *
 * It lives in `shared/` because the shop's catalogue filters face the same
 * question, and because a per-form copy is exactly how the item editor came
 * to offer a banknote a metal after the entry form had stopped.
 */

/** Whether an item of this kind is paper money. */
export function isCurrencyKind(kind) {
  return kind === 'currency'
}

//: Fields a banknote does not have. Coin, bullion, set, medal and token all do.
export const COIN_ONLY_FIELDS = new Set([
  'strike_type',
  'metal',
  'mint',
  'bullion_form',
])

//: Fields only paper money has.
export const CURRENCY_ONLY_FIELDS = new Set([
  'note_type',
  'seal_color',
  'fed_district',
  'signature_combination',
])

/** Whether a reference value may be offered for an item of `itemKind`. */
export function fitsKind(entry, itemKind) {
  const currency = isCurrencyKind(itemKind)
  const appliesTo = entry?.extra?.applies_to
  if (appliesTo) {
    return appliesTo === 'any' || appliesTo === (currency ? 'currency' : 'coin')
  }
  const denominationKind = entry?.extra?.kind
  if (denominationKind) {
    return denominationKind === (currency ? 'note' : 'coin')
  }
  return true
}
```

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Use it in both forms**

- `ItemEditForm.jsx`: delete its private `COIN_ONLY` set (added in `b4ee76f`)
  and import `COIN_ONLY_FIELDS` instead; pass
  `filter={(entry) => fitsKind(entry, value('item_kind'))}` to the
  denomination and series pickers, and replace `AttributesField`'s own `fits`
  with `fitsKind`.
- `NewItemForm.jsx`: import `isCurrencyKind` rather than defining it, and pass
  the same `filter` to its denomination and series pickers.
- Leave the grade filter alone: grades split by `grade_scale`, not by these
  markers, and it already works.

- [ ] **Step 6: Add form tests**

In `ItemEditForm.test.jsx`, beside the metal tests: a note's denomination
picker offers `$1 Bill` and not `Quarter`; a coin's offers `Quarter` and not
`$1 Bill`. Put both denominations in the test's reference mock with their
`extra.kind`. Mirror one of them in `NewItemForm.test.jsx`.

- [ ] **Step 7: Run `npx vitest run src/owner src/shared`, then gate and commit.**

---

### Task 4: Add a value by typing its name

**Files:**
- Modify: `frontend/src/shared/reference.jsx` (the `adding` branch, ~lines 103-140)
- Test: `frontend/src/shared/reference.test.jsx`

**Interfaces:**
- Consumes: `api.addReferenceValue(table, payload)` (already in `shared/api.js`), `fitsKind` (Task 3).
- Produces: `ReferenceSelect` gains two props:
  - `addFields: object` -- extra columns sent with a new value (e.g. `{applies_to: 'currency'}`).
  - `labelOnly: boolean` -- when true, the add form asks for a label alone and derives the code.
  - `codeFromLabel(label: string): string` is exported from `reference.jsx` for its tests.

- [ ] **Step 1: Write the failing tests**

```jsx
describe('codeFromLabel', () => {
  it.each([
    ['Mismatched Serial', 'mismatched_serial'],
    ['  Gutter fold  ', 'gutter_fold'],
    ["Printer's Mark", 'printers_mark'],
    ['Off-Center', 'off_center'],
    ['Ink Smear 2', 'ink_smear_2'],
  ])('%s becomes %s', (label, code) => {
    expect(codeFromLabel(label)).toBe(code)
  })
})
```

and, for the picker (follow the file's existing render helpers):

- typing a label and clicking Add calls
  `api.addReferenceValue('item_attribute', { code: 'mismatched_serial', label: 'Mismatched Serial', applies_to: 'currency' })`
  -- assert the **exact** payload, not `objectContaining`;
- the new value is then selected (`onChange` called with that code);
- when the derived code already exists in the loaded vocabulary, **nothing is
  posted** and the existing value is selected instead;
- with `labelOnly` false (every other picker), the form still asks for code
  and label as it does today.

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

```js
/** The code a typed label becomes: `Mismatched Serial` -> `mismatched_serial`. */
export function codeFromLabel(label) {
  return label
    .trim()
    .toLowerCase()
    .replace(/['`’]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}
```

In the `adding` branch, when `labelOnly` is set: one input (the label), a
derived code shown beside it so what will be stored is visible before saving,
and `addValue` sending `{ code, label, ...addFields }`. Before posting, look
for the derived code among the loaded `values`: if it is there, select it and
close the form without posting.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Turn it on where the spec says**

The attribute picker in `ItemEditForm` (`AttributesField`) currently passes
`allowAdd={false}`; give it `allowAdd labelOnly addFields={{applies_to: kind === 'currency' ? 'currency' : 'coin'}}`.
The same for the error-type picker built in Task 5, and for attributes in
`NewItemForm` and `ReceiptPanel` once Task 6 mounts them there.

- [ ] **Step 6: Gate and commit.**

---

### Task 5: The errors panel

**Files:**
- Create: `frontend/src/owner/pages/inventory/ErrorsPanel.jsx`, `ErrorsPanel.test.jsx`
- Modify: `frontend/src/owner/api.js`

**Interfaces:**
- Consumes: `GET /api/inventory/{id}/errors` -> `{inventory_item_id, errors: [{error_type, details, source, noted_by_id, noted_at}]}`; `PUT /api/inventory/{id}/errors` with `{errors: [{error_type, details}]}` (the whole set; the same type twice is a 422).
- Produces:
  - `api.getItemErrors(id)`, `api.setItemErrors(id, errors)`.
  - `<ErrorsPanel itemId={number|null} kind={string} value={Array} onChange={fn} />` -- controlled when `itemId` is null (New item holds the list itself), self-loading and self-saving when an id is given.

- [ ] **Step 1: API calls**

```js
  // Errors: several per item -- a bill is commonly miscut AND misprinted --
  // each with its own note. PUT replaces the whole set.
  getItemErrors: (id) => send(`/api/inventory/${id}/errors`),
  setItemErrors: (id, errors) =>
    send(`/api/inventory/${id}/errors`, { method: 'PUT', body: { errors } }),
```

- [ ] **Step 2: Write the failing tests**

Mock `../../api`. Cover: existing errors render with their notes; adding a
type and a note calls `setItemErrors` with the **exact** array
(`[{ error_type: 'miscut', details: 'miscut at 3 o\'clock' }]`); a type already
recorded is not offered again; removing a row and saving sends the remaining
set; the type picker offers only the item's kind (mock two error types with
`extra.applies_to` of `coin` and `currency`); a failed save shows the message
and keeps the rows.

- [ ] **Step 3: Build the component**

A list of recorded errors (type label, a details input, a Remove button) and a
picker plus note for adding one. `fitsKind` filters the picker (Task 3), and
it carries `allowAdd labelOnly addFields={{applies_to: ...}}` (Task 4) so a
missing error type can be added in place. When `itemId` is set, it loads on
mount and saves through `setItemErrors`; when `itemId` is null it is fully
controlled through `value`/`onChange` so a form that has no item yet can hold
the list.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Gate and commit.**

---

### Task 6: Errors in the three places

**Files:**
- Modify: `frontend/src/owner/pages/inventory/ItemEditForm.jsx` (+ test), `frontend/src/owner/pages/entry/NewItemForm.jsx` (+ test), `frontend/src/owner/pages/receiving/ReceiptPanel.jsx` (+ test)

**Interfaces:** consumes Task 5's `ErrorsPanel` and api calls.

- **Item editor:** mount `<ErrorsPanel itemId={itemId} kind={value('item_kind')} />`
  beside the Attributes row. It saves itself; the item form's own save is
  unaffected.
- **Receiving:** in `ReceiptPanel`, one panel per item being received, with
  that item's id. Read how the panel handles several items before choosing
  where it goes -- if the panel receives many items at once, show it only when
  a single item is selected rather than inventing a bulk semantic the API does
  not have (`PUT` replaces one item's set).
- **New item:** hold the list in form state (`itemId={null}`), and after
  `api.createInventoryItem(payload)` succeeds, call
  `api.setItemErrors(created.id, errors)`.

The two-step save is the part that can go wrong, so it is specified:

```js
      const created = await api.createInventoryItem(payload)
      if (errors.length > 0) {
        try {
          await api.setItemErrors(created.id, errors)
        } catch (err) {
          // The item exists. Saying "failed" would be false and would invite
          // a second entry of the same item.
          setErrorSaveFailure({ itemId: created.id, itemCode: created.item_code })
          setError(
            `${created.item_code} was created, but its errors were not saved: ` +
              `${err.message}`,
          )
          return                    // keep the form and its errors on screen
        }
      }
      onSaved?.(created)
```

with a **Retry** button beside that message calling `setItemErrors` again for
the recorded id, and clearing the failure on success before continuing as a
normal save.

- [ ] **Step 1: Write the failing tests**

For New item: a create followed by a failing `setItemErrors` leaves the item
code in the message, the errors still on screen, `onSaved` **not** called, and
Retry succeeding calls `setItemErrors` again and then `onSaved`. Drive the
failure with `api.setItemErrors.mockRejectedValueOnce(new Error('boom'))` --
the branch is the point of the test, so it must be exercised, not assumed.

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Wire the three places.**
- [ ] **Step 4: Run `npx vitest run src/owner`.**
- [ ] **Step 5: Gate and commit.**

---

### Task 7: Documentation

**Files:** `docs/system-administration.md`, `docs/specs/vocabulary-and-errors-design.md`

- [ ] **Step 1:** In `system-administration.md`, beside the vocabulary
  section: how a picker chooses what to offer (the two markers), that lists
  are alphabetical except grades and the four lifecycles, how to add a value
  by typing its name and where those additions show up (`manual`, excluded
  from an export), and how to record errors in the three places.
- [ ] **Step 2:** Set the spec's status line to
  `Design. Status: **agreed with the owner 2026-09-17**; built.`
- [ ] **Step 3:** Verify every claim against the code as the phase-1 doc task
  did -- a doc statement with no code behind it is a defect.
- [ ] **Step 4: Commit.**

---

### Task 8: Show the owner (no live data change)

Not dispatched to a subagent: the controller does this and reports.

- [ ] **Step 1:** Confirm with the owner before restarting the backend --
  they enter items during the day, and the restart drops their session.
- [ ] **Step 2:** After the restart, check in the console: a banknote's
  denomination list holds no coin denominations; attributes read
  alphabetically; adding an attribute by name works and it appears in place;
  an error can be recorded on a note from the item editor and from Receiving;
  and a new item entered with an error keeps both.
- [ ] **Step 3:** Report what was checked, including anything that looked
  wrong.
