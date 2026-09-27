# Identify First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Receiving and New item start with the facts that identify a piece, and the design series joins what those facts fill in.

**Architecture:** The backend's series pass (`app.series_classify`) gains a per-item refresh, called from `classifier_defaults.refresh_items` so every create and edit gets it, and a facts-only `suggest_series` used by the two suggestion endpoints. The frontend gets one shared list of Identify fields per kind (`management/identify.js`), a new `IdentifySection` at the top of the receipt dialog, and a reordered `NewItemForm`.

**Tech Stack:** FastAPI, SQLAlchemy 2, PostgreSQL, pytest; React 19, Vitest, Testing Library.

**Spec:** `docs/specs/identify-first-entry-design.md`

## Global Constraints

- Tests run against the test database only. Never point anything at the live `ccwebdb` database; never run two pytest sessions at once.
- `scripts\ccweb_check.cmd` (from Bash: `cmd //c "scripts\\ccweb_check.cmd"`, output redirected to a file, never piped) must exit 0 before each commit that ends a task.
- Every public function carries a docstring and full annotations (ruff `D`, `ANN`); mypy clean.
- Write files with Write/Edit, never shell heredocs. LF endings (`write_bytes`, not `write_text`, from Python).
- Comments give the reason, not the ruling or review round that set it.
- Field names, labels and help keys are the ones the item editor already uses.

## Review Focus

1. A note whose seal or class was chosen by the person: the series suggestion must use them as evidence (brown-seal $5 1934A is Hawaii) -- pinned in Task 2.
2. A person-emptied (`held`) series refilled by a save or by the batch pass -- pinned in Task 1.
3. A year corrected after a derived series was written: the old series must go, and a person's must not -- pinned in Task 1.
4. Receipt after a PATCH that succeeded: a retry must not resend Identify (a stale `base` would 409) -- pinned in Task 4.
5. Identify edits followed by Missing/Cancelled/Returned: not saved, and said so -- pinned in Task 4.

---

### Task 1: Per-item series refresh

**Files:**
- Modify: `backend/app/series_classify.py`
- Modify: `backend/app/classifier_defaults.py` (`refresh_items`)
- Test: `backend/tests/test_series_classify.py`, `backend/tests/test_inventory_create.py`

**Interfaces:**
- Produces: `series_classify.candidates(designs, inventory, denomination_id, year, letter) -> list[Design]`; `series_classify.refresh_series(db, item_ids: Collection[int]) -> None` (flushes, never commits); `classify(db, item_ids: Collection[int] | None = None)`; `_items` and `classify` skip items whose `series_id` is `held`.

- [ ] **Step 1: Failing tests** (in `test_series_classify.py`)

```python
from app.field_sources import HELD, SERIES_CLASSIFY, SUGGESTION, hold, record_derived
from app.models import ItemFieldSource
from app.series_classify import refresh_series


def _source(db: Session, item: InventoryItem) -> str | None:
    return db.execute(
        select(ItemFieldSource.derived_by).where(
            ItemFieldSource.inventory_item_id == item.id,
            ItemFieldSource.field_name == "series_id",
        )
    ).scalar_one_or_none()


def test_a_held_series_stays_empty_in_the_batch(db: Session, make_item: ItemFactory) -> None:
    dime = _coin(db, make_item, DIME, 1942)
    hold(db, [dime.id], ["series_id"])
    db.commit()

    run(db, commit=True)

    assert _series_code(db, dime) is None


def test_refresh_assigns_one_item_and_records_it(db: Session, make_item: ItemFactory) -> None:
    dime = _coin(db, make_item, DIME, 1942)
    other = _coin(db, make_item, DIME, 1942)

    refresh_series(db, [dime.id])
    db.commit()

    assert _series_code(db, dime) == "winged_liberty_head_dime"
    assert _source(db, dime) == SERIES_CLASSIFY
    assert _series_code(db, other) is None  # only the items named


def test_refresh_takes_back_its_own_guess_when_the_year_moves(
    db: Session, make_item: ItemFactory
) -> None:
    dime = _coin(db, make_item, DIME, 1942)
    refresh_series(db, [dime.id])
    db.commit()

    dime.year_start = dime.year_end = 1950  # a Roosevelt dime
    refresh_series(db, [dime.id])
    db.commit()

    assert _series_code(db, dime) == "roosevelt_dime"


def test_refresh_retracts_an_accepted_suggestion_the_facts_rule_out(
    db: Session, make_item: ItemFactory
) -> None:
    wlh = code_id(db, Series, "winged_liberty_head_dime")
    dime = _coin(db, make_item, DIME, 1942, series_id=wlh)
    record_derived(db, dime.id, ["series_id"], SUGGESTION)
    dime.year_start = dime.year_end = 1950
    refresh_series(db, [dime.id])
    db.commit()

    assert _series_code(db, dime) == "roosevelt_dime"


def test_refresh_never_clears_a_persons_series(db: Session, make_item: ItemFactory) -> None:
    barber = code_id(db, Series, "barber_dime")
    dime = _coin(db, make_item, DIME, 1942, series_id=barber)

    refresh_series(db, [dime.id])
    db.commit()

    assert _series_code(db, dime) == "barber_dime"


def test_refresh_leaves_a_held_series_empty(db: Session, make_item: ItemFactory) -> None:
    dime = _coin(db, make_item, DIME, 1942)
    hold(db, [dime.id], ["series_id"])

    refresh_series(db, [dime.id])
    db.commit()

    assert _series_code(db, dime) is None
    assert _source(db, dime) == HELD
```

Check the Roosevelt code with `grep -n '"roosevelt' backend/data/reference/series.json` and use the real one.

And in `test_inventory_create.py`:

```python
def test_a_coin_entered_gets_its_design_series_from_the_facts(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _purchase_order(db)
    res = client.post(
        "/api/inventory",
        json=_coin_payload(
            order.id,
            source_title="1942 dime",
            year_start=1942,
            denomination="usd_coin_0_10",
            mint="P",
        ),
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["series"] == "winged_liberty_head_dime"
```

- [ ] **Step 2: Run, expect failures** -- `cd backend && python -m pytest tests/test_series_classify.py tests/test_inventory_create.py -q` (import error for `refresh_series`; held test assigns).

- [ ] **Step 3: Implement** in `series_classify.py`:

```python
def candidates(
    designs: list[Design],
    inventory: str,
    denomination_id: int,
    year: int,
    letter: str | None,
) -> list[Design]:
    """The designs an item of this inventory, denomination, year and letter can be."""
    return [
        d
        for d in designs
        if d.applies_to == inventory and d.covers(denomination_id, year, letter)
    ]


def _rules_out(
    design: Design,
    inventory: str,
    denomination_id: int,
    year: int,
    letter: str | None,
    note_type_id: int | None,
) -> bool:
    """Whether the facts say an item cannot be `design`."""
    wrong_class = (
        design.note_type_id is not None
        and note_type_id is not None
        and design.note_type_id != note_type_id
    )
    return (
        wrong_class
        or design.applies_to != inventory
        or not design.covers(denomination_id, year, letter)
    )
```

`disagreements` calls `_rules_out`; `classify` calls `candidates`. `_items(db, item_ids=None)` adds, always:

```python
held = exists().where(
    ItemFieldSource.inventory_item_id == InventoryItem.id,
    ItemFieldSource.field_name == "series_id",
    ItemFieldSource.derived_by == HELD,
)
... .where(InventoryItem.series_id.is_(None), ~held)
```

and `.where(InventoryItem.id.in_(item_ids))` when `item_ids` is not None. `classify(db, item_ids=None)` passes it through and runs `disagreements` only when `item_ids is None`.

```python
#: Where a series came from, when this pass may take it back.
RETRACTABLE = (SERIES_CLASSIFY, SUGGESTION)


def refresh_series(db: Session, item_ids: Collection[int]) -> None:
    """Bring these items' design series up to date with their facts, uncommitted.

    A series this pass or an accepted suggestion wrote, which the facts now
    rule out, is cleared with its record; then every item left without one
    (and not held empty) is decided as the batch decides it.
    """
    if not item_ids:
        return
    db.flush()
    designs = load_designs(db)
    by_id = {d.id: d for d in designs}
    rows = db.execute(
        _with_facts(
            InventoryItem.id,
            ItemKind.code,
            InventoryItem.series_id,
            InventoryItem.denomination_id,
            InventoryItem.year_start,
            InventoryItem.year_end,
            CurrencyDetail.series_year,
            CurrencyDetail.series_letter,
            CurrencyDetail.note_type_id,
        )
        .join(
            ItemFieldSource,
            (ItemFieldSource.inventory_item_id == InventoryItem.id)
            & (ItemFieldSource.field_name == "series_id"),
        )
        .where(
            InventoryItem.id.in_(item_ids),
            ItemFieldSource.derived_by.in_(RETRACTABLE),
        )
    ).tuples()
    stale = []
    for item_id, kind, series_id, denomination_id, start, end, s_year, s_letter, cls in rows:
        design = by_id.get(series_id) if series_id is not None else None
        inventory = inventory_of(kind)
        year, letter = _design_year(inventory, (start, end), s_year, s_letter)
        if design is None or denomination_id is None or year is None or _rules_out(
            design, inventory, denomination_id, year, letter, cls
        ):
            stale.append(item_id)
    if stale:
        for item in db.execute(select(InventoryItem).where(InventoryItem.id.in_(stale))).scalars():
            item.series_id = None
        forget(db, stale, ["series_id"])
        db.flush()
    report = classify(db, item_ids)
    for item_id, series_id in report.assignments.items():
        db.get_one(InventoryItem, item_id).series_id = series_id
    _record_many(db, report.assignments)
    db.flush()
```

where the non-committing record is `record_derived(db, item_id, ["series_id"], SERIES_CLASSIFY)` per item (inline the loop instead of a `_record_many` helper). Retract when the facts are gone too (no denomination or no single year): a guess with nothing under it is not kept.

In `classifier_defaults.refresh_items`, after `apply(...)`:

```python
    # The design series follows the same facts; imported here because
    # series_classify reads through series_match, which the batch CLI of this
    # module does not need.
    from .series_classify import refresh_series

    refresh_series(db, item_ids)
```

(If there is no import cycle -- there is none today -- import at module top instead and drop that comment.)

- [ ] **Step 4: Run** the two test files, then the full backend suite once (`python -m pytest -q`, exit code is the verdict). Existing tests that assert `series is None` after a create or edit are now wrong only if the facts decide a series -- read each failure; fix the test's expectation only when the new value is the one the facts decide.

- [ ] **Step 5: Gate and commit** -- "A saved item's design series follows its facts, and a held one stays empty".

### Task 2: Series in the suggestion endpoints

**Files:**
- Modify: `backend/app/series_classify.py` (add `suggest_series`), `backend/app/routers/defaults.py`, `backend/app/schemas.py` (`NoteSuggestionOut`, `CoinSuggestionOut`)
- Test: `backend/tests/test_entry_suggestions.py`

**Interfaces:**
- Consumes: `candidates`, `decide`, `load_designs` (Task 1).
- Produces: `suggest_series(db, inventory: str, denomination_id: int | None, year: int | None, letter: str | None, seal_color_id: int | None, note_type_id: int | None) -> str | None`; `series: str | None` on both suggestion bodies.

- [ ] **Step 1: Failing tests**

```python
def _coin_lookup(client: TestClient, headers: dict[str, str], **params: object) -> dict[str, object]:
    response = client.get("/api/defaults/coin", params=params, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def test_a_coins_facts_name_its_design(client: TestClient, admin_headers: dict[str, str]) -> None:
    found = _coin_lookup(client, admin_headers, denomination="usd_coin_0_10", year=1942)
    assert found["series"] == "winged_liberty_head_dime"


def test_a_boundary_year_suggests_no_design(client: TestClient, admin_headers: dict[str, str]) -> None:
    found = _coin_lookup(client, admin_headers, denomination="usd_coin_1_00", year=1921)
    assert found["series"] is None


def test_a_notes_facts_name_its_design(client: TestClient, admin_headers: dict[str, str]) -> None:
    found = _note_lookup(client, admin_headers, denomination="usd_note_1", series_year=1928)
    assert found["series"] == "funnyback"


def test_a_chosen_seal_is_evidence_for_the_design(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    plain = _note_lookup(
        client, admin_headers, denomination="usd_note_5", series_year=1934, series_letter="A"
    )
    brown = _note_lookup(
        client, admin_headers, denomination="usd_note_5", series_year=1934,
        series_letter="A", seal_color="brown",
    )
    assert plain["series"] is None
    assert brown["series"] == "hawaii"
```

- [ ] **Step 2: Run, expect KeyError/None mismatch.**

- [ ] **Step 3: Implement**

```python
def suggest_series(
    db: Session,
    inventory: str,
    denomination_id: int | None,
    year: int | None,
    letter: str | None,
    seal_color_id: int | None = None,
    note_type_id: int | None = None,
) -> str | None:
    """The design these facts alone decide, for the entry forms; None if open."""
    if denomination_id is None or year is None:
        return None
    designs = load_designs(db)
    found = candidates(designs, inventory, denomination_id, year, letter)
    design, _, _ = decide(found, set(), {d.code for d in designs}, seal_color_id, frozenset(), note_type_id)
    return design.code if design is not None else None
```

In `suggest_note`, after `ids = suggest(...)`: seal and class are the chosen value or else the suggested one (`current[...] or ids.get(...)`), then `series=suggest_series(db, "currency", denomination_id, series_year, (series_letter or "").strip().upper() or None, seal_id, class_id)`. In `suggest_coin`: `series=suggest_series(db, "coin", denomination_id, year, None)` (reuse the denomination id already resolved). Schema: `series: str | None = None` on both, docstrings updated to say what the facts decide.

- [ ] **Step 4: Run** `tests/test_entry_suggestions.py tests/test_classifier_defaults.py`.
- [ ] **Step 5: Gate and commit** -- "The entry suggestions name the design series the facts decide".

### Task 3: New item form, facts first

**Files:**
- Create: `frontend/src/management/identify.js`, `frontend/src/management/identify.test.js`
- Modify: `frontend/src/management/pages/entry/NewItemForm.jsx`
- Test: `frontend/src/management/pages/entry/NewItemForm.test.jsx`

**Interfaces:**
- Produces: `identifyKeys(kind: string) -> string[]` -- `currency`: `['series_year','series_letter','denomination','serial_number','face_plate_number','back_plate_number']`; otherwise `['year_start', ...(fieldFitsKind('mint', kind) ? ['mint'] : []), 'denomination']`.

- [ ] **Step 1: Failing tests** -- `identify.test.js` for both lists and a kind without a mint (read `COIN_ONLY_FIELDS` in `shared/kinds.js` to pick one, e.g. `medal` if mint is coin-only there). In `NewItemForm.test.jsx`:

```jsx
function order(labels) {
  const all = screen.getAllByText((_, el) => el?.tagName === 'LABEL')
  return labels.map((l) => all.findIndex((el) => el.textContent.startsWith(l)))
}

it('asks a note for its identifying facts first, in the order they are written', async () => {
  renderForm()
  await chooseKind('currency')
  const at = order(['Series year', 'Series letter', 'Denomination', 'Serial number',
    'Face plate', 'Back plate', 'Printed at', 'Title'])
  expect(at.every((v, i) => i === 0 || v > at[i - 1])).toBe(true)
})

it('asks a coin for year, mint and denomination before anything else', async () => {
  renderForm()
  const at = order(['Year', 'Mint', 'Denomination', 'Country', 'Title'])
  expect(at.every((v, i) => i === 0 || v > at[i - 1])).toBe(true)
})

it('fills the design series the facts decide, marked suggested', async () => {
  api.suggestCoin.mockResolvedValue({ metal: 'silver_90', series: 'winged_liberty_head_dime' })
  // ...type year 1942, pick the dime ...
  expect(await screen.findAllByText('suggested')).toHaveLength(2)
})

it('after Save and add another, a note starts again at its serial number', async () => { /* ... */ })
it('after Save and add another, a coin starts again at its year', async () => { /* ... */ })
```

Reuse the file's existing render / kind-choosing helpers (read its top first) rather than the placeholder names above; the year is a `<div data-help>` with a `<label htmlFor>`, so the order helper must include those labels (it does: it reads every LABEL).

- [ ] **Step 2: Run** `cd frontend && npx vitest run src/management/pages/entry src/management/identify.test.js` -- fails.

- [ ] **Step 3: Implement**
  - `identify.js` as in Interfaces, with a docstring pointing at the spec.
  - `NewItemForm.jsx`: move JSX blocks into the spec's order -- Kind; Identify (coin: the year block, Mint, Denomination; note: Series year, Series letter, Denomination, Serial number, Face plate, Back plate, Printed at, then the issue warning); suggested (Series, then Note class, Seal, Signatures, Reserve Bank / Metal); Country; grading (Strike type, Grade, Grade designation, Grading service, Certificate number, Set form, Variety); then Title, Seller's item id, Pieces, Item cost, Shipping, Status, Description. The year block moves inside the grid (it is a `div`, so it sits in the grid like the labels).
  - `series` joins `COIN_SUGGESTED` and `NOTE_SUGGESTED` (and so `NO_SUGGESTIONS`), gets `{mark('series')}`, and since `chosen` only sends fields in the suggestion list, a person-picked series is sent as a fact -- the endpoints ignore unknown params? They do not accept `series`: filter `series` out of `chosen` (`fields.filter((k) => k !== 'series' && ...)`).
  - `titleRef` becomes `firstRef`, attached to the Serial number input for a note and the Year input for a coin; `finishSave(..., true)` focuses it. The Title keeps `{...accel('t')}`.
  - `SHARED_ON_REPEAT` unchanged (series is shared already; a suggested series stays marked).
- [ ] **Step 4: Run** the entry tests; then the whole frontend suite `npx vitest run`.
- [ ] **Step 5: Gate and commit** -- "New item asks for the identifying facts first".

### Task 4: Identify section in the receipt dialog

**Files:**
- Create: `frontend/src/management/pages/receiving/IdentifySection.jsx`, `.../IdentifySection.test.jsx`
- Modify: `frontend/src/management/pages/receiving/ReceiptPanel.jsx`, `ReceiptPanel.test.jsx` (add `suggestNote`, `suggestCoin` to the api mock)

**Interfaces:**
- Consumes: `identifyKeys` (Task 3); `api.updateInventoryItem(id, payload)`, `api.suggestNote`, `api.suggestCoin`.
- Produces: `<IdentifySection item values onChange />` -- controlled; `values` is `{key: string}` for `identifyKeys(item.item_kind)`. Helpers exported from `IdentifySection.jsx`: `identifyValues(item) -> object` (strings, '' for null) and `identifyChanges(item, values) -> {changes, base}` (only keys whose trimmed value differs; `series_year`/`year_start` sent as numbers or null; a coin's `year_start` change also sends `year_end` equal to it, with `base.year_end` from the item).

- [ ] **Step 1: Failing tests** (`ReceiptPanel.test.jsx`, new `describe('Identify')`):

```jsx
const NOTE = { ...ITEM, item_kind: 'currency', denomination: 'usd_note_1', series_year: 1957,
  series_letter: 'B', serial_number: 'A1B', face_plate_number: null, back_plate_number: null }

it('shows a note its identifying facts first, filled from the item', async () => {
  api.getInventoryItem.mockResolvedValue(NOTE)
  renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
  expect(await screen.findByLabelText('Series year')).toHaveValue(1957)
  expect(screen.getByLabelText('Serial number')).toHaveValue('A1B')
})

it('saves only what changed, with what it was, before the receipt', async () => {
  api.getInventoryItem.mockResolvedValue(NOTE)
  renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
  const serial = await screen.findByLabelText('Serial number')
  await userEvent.clear(serial)
  await userEvent.type(serial, 'A12345678B')
  await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))
  await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
  expect(api.updateInventoryItem).toHaveBeenCalledWith(412, {
    serial_number: 'A12345678B', base: { serial_number: 'A1B' } })
  expect(api.updateInventoryItem.mock.invocationCallOrder[0])
    .toBeLessThan(api.receiveItems.mock.invocationCallOrder[0])
})

it('sends nothing to the item when nothing changed', /* click Receive; updateInventoryItem not called */)
it('does not save Identify edits for Missing, and says so', /* type; find text /saved only with receive/i; click Missing; update not called */)
it('a refused save receives nothing and keeps what was typed', /* update rejects Error('bad plate'); receive not called; error shown; value kept */)
it('a receipt that fails after the save does not send the save again', /* receive rejects once then resolves; click twice; update called once */)
it('disables Confirm or correct fields while Identify has unsaved edits', /* ... */)
it('sends the for-sale acknowledgement with the save as well', /* update rejects {status:409, message:'For sale -- ...'} once; confirm; second update call has acknowledge_for_sale: true */)
```

Write each commented one out in full in the style of the first two (same helpers, same `waitFor` idiom). For the for-sale case, read the existing for-sale test in the file for how the dialog is confirmed.

- [ ] **Step 2: Run** `npx vitest run src/management/pages/receiving` -- fails.

- [ ] **Step 3: Implement `IdentifySection.jsx`** -- a `<fieldset className="identify">` with `<legend>Identify</legend>`, the read-only kind (`kindLabel` if `shared/kinds` has one, else the code), one labelled control per `identifyKeys(kind)` with `data-help={key}` (Denomination and Mint are `ReferenceSelect` with the editor's filters: `fitsKind(entry, kind)` for denomination), then "From these facts": a debounced (250 ms) call to `api.suggestNote` / `api.suggestCoin` with the section's facts (note: denomination, series_year, series_letter, serial_number; coin: denomination, country from the item, year) listing the non-null codes' labels and the warning; a failed lookup shows nothing.

`ReceiptPanel.jsx`:
- State `identify` = `{ itemId, values, baseline }`, reset during render when `itemDetail` for a new id arrives (the same adjust-during-render pattern as `trackedItemId`).
- `const { changes, base } = identifyChanges(baselineItem, identify.values)`; `dirty = Object.keys(changes).length > 0`.
- In `submit(outcome, acknowledged)`, before `api.receiveItems`: if `outcome === 'received' && dirty`, `await api.updateInventoryItem(singleItemId, { ...changes, base, ...(acknowledged ? { acknowledge_for_sale: true } : {}) })`, then set the baseline to the current values so a later retry has nothing to send. A throw falls into the existing catch (for-sale dialog or error line), so no receipt is posted.
- Render `IdentifySection` first, above `.filter-grid`, only when `itemDetail` is loaded; when `dirty`, a muted line: "Saved only with Receive." The **Confirm or correct fields** button gets `disabled={dirty}` and, when dirty, a muted "Receive or undo the Identify changes first."
- `used` (carried to the next line) is unchanged -- Identify is not carried.

- [ ] **Step 4: Run** the receiving tests, then the full frontend suite.
- [ ] **Step 5: Gate and commit** -- "Receiving opens with the facts that identify the piece".

### Task 5: Documentation and field help

**Files:**
- Modify: `docs/specs/entry-panels-design.md` (New item section: the new order, series suggested, focus on add-another), `docs/specs/receiving-purchases-design.md` (the dialog's Identify section), `docs/specs/series-classification-design.md` ("Two passes assign series" -> also on save; held respected), `docs/specs/classifier-defaults-design.md` (refresh on save includes series), `docs/system-administration.md` if it describes the receipt dialog's fields (grep "Confirm or correct").
- Modify: `frontend/src/management/fieldHelp.js` only if the help-key test fails (every Identify key already has help from the editor; `identify` legend needs none).

- [ ] **Step 1:** Edit each doc in place to the current state; no history wording.
- [ ] **Step 2:** Full gate; commit -- "The docs describe identify-first entry".
- [ ] **Step 3:** Browser check with the isolated headless Chrome (memory: browser-checks-without-users-chrome): open `/management/receiving`, open a note's receipt dialog, confirm the Identify section renders first; open Purchases, confirm the New item order for both kinds. Read-only: do not click Receive or Save against the live database.
- [ ] **Step 4:** Delete this plan file in the merge commit's branch before merging (plans are temporary).
