import { useCallback, useContext, useMemo, useRef, useState } from 'react'

import { api } from './api'
import { codeFromLabel } from './reference-codes'
import { ReferenceContext, useReference } from './reference-context'
import { findEntries } from './reference-match'

//: A vocabulary longer than this gets a box to find a value by name or alias.
//: A short list is quicker to read than to search.
export const FIND_FROM = 10

/**
 * Classifier vocabularies, fetched once and shared.
 *
 * The API takes classifier *codes* -- `usd_coin_1_00`, `MS64`, `US` -- which is
 * the right contract between machines and useless to a person filling in a
 * form. This turns them into pickers that submit the code and show the label.
 *
 * Cached in one place because a single admin form needs five or six of these
 * vocabularies, and they change about as often as the software does.
 *
 * Each table is requested once, however many pickers ask for it in the same
 * commit and however often StrictMode repeats their effects: `requested` is
 * a ref, read and written synchronously, so the second caller sees the first
 * caller's request before any state has updated. `load` is therefore stable,
 * and `useReference` asks again only when its table is missing.
 */
export function ReferenceProvider({ children }) {
  const [tables, setTables] = useState({})
  //: table -> a token for its latest request, loaded or still in flight.
  const requested = useRef(new Map())

  // After adding a value the cached vocabulary is stale, so it is dropped and
  // refetched rather than patched locally -- the server decides sort order and
  // provenance, and guessing at them here is how a cache starts lying.
  const invalidate = useCallback((table) => {
    requested.current.delete(table)
    setTables((t) => {
      const next = { ...t }
      delete next[table]
      return next
    })
  }, [])

  const load = useCallback(async (table) => {
    if (requested.current.has(table)) return
    const token = {}
    requested.current.set(table, token)
    let values
    try {
      values = (await api.getReference(table)).values
    } catch {
      // A missing vocabulary must not take the form down: the field falls
      // back to a free-text input.
      values = []
    }
    // An answer to a request since invalidated is stale; the newer one wins.
    if (requested.current.get(table) === token) {
      setTables((t) => ({ ...t, [table]: values }))
    }
  }, [])

  const value = useMemo(
    () => ({ tables, load, invalidate }),
    [tables, load, invalidate],
  )

  return <ReferenceContext.Provider value={value}>{children}</ReferenceContext.Provider>
}

/**
 * A dropdown over one vocabulary.
 *
 * Falls back to a plain text input while the vocabulary is loading or if it
 * could not be fetched, so the form is always usable -- degraded, never
 * broken.
 *
 * A long vocabulary gets a find box beside it. Typing narrows the options by
 * label, code or alias -- "Walker" offers Walking Liberty Half Dollar, with
 * the alias shown so the match makes sense -- and Enter picks the first. A
 * native select's own type-ahead only matches the start of a label.
 */
export function ReferenceSelect({
  table,
  value,
  onChange,
  allowBlank = true,
  //: Whether this picker may add a value to the vocabulary. True for the
  //: descriptive tables, which grow with use. False for one the code itself
  //: branches on: receiving, the outstanding lists and the inventory views
  //: all key on the known `item_status` codes, so a status invented from a
  //: dropdown would be a row nothing downstream can reason about.
  allowAdd = true,
  placeholder,
  //: Optional `(entry) => boolean` narrowing what is offered -- a note's grade
  //: picker offers only the paper-money scale.
  filter,
  //: Keyboard accelerator attributes (`accessKey`, `aria-keyshortcuts`),
  //: passed straight to the `<select>` -- this module stays outside
  //: `management/`, so it takes the attributes rather than importing `shortcuts`.
  accessKey,
  'aria-keyshortcuts': ariaKeyshortcuts,
  //: Extra columns sent with a value this picker adds -- e.g.
  //: `{ applies_to: 'currency' }`, the kind the item being entered is. Goes
  //: into the POST's `extra`, which is where the reference API reads a
  //: table's own columns; `ReferenceValueCreate` has no top-level field for
  //: them, so the value would otherwise be created with none and vanish from
  //: this very picker (see `fitsKind`).
  addFields = {},
  //: When true, the add form asks for a label alone and derives the code
  //: from it with `codeFromLabel`, rather than asking for both. Attributes
  //: are the one vocabulary this is worth doing for: nothing about a "Star
  //: Note" that a person would type needs a separate code entered by hand.
  labelOnly = false,
  //: The `extra` column a chosen group is stored under, e.g.
  //: `'attribute_group'`. Present only for a table that has one -- item
  //: attributes today, not error types, which carry no group. Undefined
  //: leaves the add form without a group picker at all.
  groupField,
  //: `[{code, label}, ...]` offered when `groupField` is set. Required in
  //: that case: the column behind it is NOT NULL with no default, so a
  //: value posted without one would 422, and a silent default was the
  //: wrong call for the person to have made for them.
  groupOptions,
}) {
  const values = useReference(table, { includeRetired: true })
  const context = useContext(ReferenceContext)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState({ code: '', label: '', group: '' })
  const [error, setError] = useState('')
  const [find, setFind] = useState('')
  // Always derived: a label-only form stores it, and the code-and-label
  // form falls back to it when the code box is left empty.
  const derivedCode = codeFromLabel(draft.label)

  function selectAndClose(code) {
    onChange({ target: { value: code } })
    setAdding(false)
    setDraft({ code: '', label: '', group: '' })
    setError('')
  }

  async function addValue() {
    const label = draft.label.trim()
    const code = labelOnly ? derivedCode : draft.code.trim() || derivedCode
    if (labelOnly) {
      // The label a person typed may already name a value under another
      // wording -- or the same one, typed again. Either way the code already
      // exists, so posting it would only 409; selecting it is what they meant.
      const existing = values?.find((entry) => entry.code === code)
      if (existing) {
        selectAndClose(existing.code)
        return
      }
    }
    const extra = groupField ? { ...addFields, [groupField]: draft.group } : addFields
    try {
      await api.addReferenceValue(table, { code, label, extra })
      context?.invalidate(table)
      selectAndClose(code)
    } catch (err) {
      setError(err.message)
    }
  }

  if (adding) {
    // Vocabularies grow with use: rather than abandoning an entry that does
    // not fit, the missing value is added here and selected immediately.
    const canAdd = labelOnly
      ? draft.label.trim() !== '' &&
        derivedCode !== '' &&
        (!groupField || draft.group !== '')
      : draft.label.trim() !== '' && (draft.code.trim() !== '' || derivedCode !== '')
    return (
      <div className="add-reference">
        {labelOnly ? (
          <>
            <input
              placeholder="label"
              value={draft.label}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })}
            />
            {/* What will actually be stored, visible before saving -- a
                person typing "Off-Center" should see it becomes
                off_center, not guess. Empty for a label that derives to
                nothing (e.g. "!!!"), which is exactly why Add stays
                disabled below rather than trusting the raw label alone. */}
            <span className="derived-code">{derivedCode}</span>
            {groupField && (
              <select
                aria-label="group"
                value={draft.group}
                onChange={(e) => setDraft({ ...draft, group: e.target.value })}
              >
                <option value="">-- group --</option>
                {(groupOptions ?? []).map((g) => (
                  <option key={g.code} value={g.code}>
                    {g.label}
                  </option>
                ))}
              </select>
            )}
          </>
        ) : (
          <>
            <input
              // Shows the code the label will give when this is left empty.
              placeholder={derivedCode ? `code: ${derivedCode}` : 'code'}
              value={draft.code}
              onChange={(e) => setDraft({ ...draft, code: e.target.value })}
            />
            <input
              placeholder="label"
              value={draft.label}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })}
            />
          </>
        )}
        <button type="button" onClick={addValue} disabled={!canAdd}>
          Add
        </button>
        <button type="button" className="link" onClick={() => setAdding(false)}>
          Cancel
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    )
  }

  if (!values || values.length === 0) {
    return (
      <input
        value={value ?? ''}
        onChange={onChange}
        placeholder={placeholder}
        aria-label={table}
        accessKey={accessKey}
        aria-keyshortcuts={ariaKeyshortcuts}
      />
    )
  }

  // Only active values that pass the filter are offered. The one already
  // chosen is always shown, even retired or filtered out, so the form never
  // pretends a record holds nothing.
  const choosable = values.filter(
    (e) => (e.is_active !== false && (!filter || filter(e))) || e.code === value,
  )
  const active = values.filter((e) => e.is_active !== false)
  const found = findEntries(choosable, find)
  // A filter narrows what is offered, never what is shown as chosen: a value
  // already set stays visible even if it no longer fits.
  const offered = found.some(({ entry }) => entry.code === value)
    ? found
    : [
        ...choosable
          .filter((entry) => entry.code === value)
          .map((entry) => ({ entry, match: { alias: null } })),
        ...found,
      ]

  function pickFirst(e) {
    if (e.key !== 'Enter') return
    // Enter in a form would otherwise submit it.
    e.preventDefault()
    if (found.length > 0) onChange({ target: { value: found[0].entry.code } })
  }

  return (
    <div className="reference-select">
      {active.length > FIND_FROM && (
        <input
          className="reference-find"
          type="search"
          placeholder="Find..."
          aria-label={`Find ${table}`}
          value={find}
          onChange={(e) => setFind(e.target.value)}
          onKeyDown={pickFirst}
        />
      )}
      <select
        value={value ?? ''}
        onChange={(e) => {
          if (allowAdd && e.target.value === '__add__') setAdding(true)
          else onChange(e)
        }}
        aria-label={table}
        accessKey={accessKey}
        aria-keyshortcuts={ariaKeyshortcuts}
      >
        {allowBlank && <option value="">--</option>}
        {offered.map(({ entry, match }) => (
          <option key={entry.code} value={entry.code}>
            {entry.label}
            {match.alias ? ` (${match.alias})` : ''}
            {entry.is_active === false ? ' (retired)' : ''}
            {/* Values not shipped are marked, so a curated vocabulary
              can be told apart from one collection's guesses. */}
            {entry.source === 'seeded' ? '' : ' *'}
          </option>
        ))}
        {find && found.length === 0 && (
          <option value="" disabled>
            nothing matches {find}
          </option>
        )}
        {allowAdd && <option value="__add__">+ Add a new value...</option>}
      </select>
    </div>
  )
}
