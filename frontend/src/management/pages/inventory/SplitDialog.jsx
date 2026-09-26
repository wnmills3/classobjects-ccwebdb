import { useState } from 'react'

import { api } from '../../api'
import HelpScope from '../../HelpScope'
import ModalDialog from '../../ModalDialog'
import { fromCents, isMoney, toCents } from '../../../shared/cents'
import ForSaleNotice from '../ForSaleNotice'
import { useMounted } from '../../useMounted'

/**
 * Breaking a lot into its pieces (`POST /api/inventory/{id}/split`,
 * `app/splitting.py`): a lot of nine proof sets becomes nine items, each with
 * its own year and description and a share of what the lot cost.
 *
 * **Equal** gives every piece the same share -- nine proof sets, a tube of
 * identical rounds. **By value** divides in proportion to a value typed per
 * piece -- face, melt or a catalog price -- for a set whose half dollar must
 * not carry the cent's cost. The shares shown are this dialog's estimate; the
 * server's allocation, which hands out the odd cents, is the real one and is
 * reported once the split is made.
 *
 * Every piece keeps the lot's seller's title, purchase, listing and
 * classifiers; the lot itself is kept, marked split, so the receipt's item
 * code still leads somewhere. A split cannot be undone here.
 */

const MAX_PIECES = 200
const WHOLE = /^\d+$/
const VALUE = /^\d+(\.\d+)?$/

let nextKey = 0

const blankRow = (item) => ({
  key: (nextKey += 1),
  description: item.description ?? '',
  year: item.year_start == null ? '' : String(item.year_start),
  count: '1',
  value: '',
})

/** Rows to start from: one per piece the lot says it holds, at least two. */
function startingRows(item) {
  const count = Math.min(Math.max(Number(item.piece_count) || 0, 2), MAX_PIECES)
  return Array.from({ length: count }, () => blankRow(item))
}

/**
 * Each row's estimated share of the item cost, in cents, or null where it
 * cannot be worked out yet. Floors each share, as the server's allocation
 * does before handing out the remainder.
 */
function shares(rows, mode, itemCost) {
  if (!isMoney(itemCost ?? '')) return rows.map(() => null)
  const weights = rows.map((row) => {
    if (!WHOLE.test(row.count) || Number(row.count) < 1) return null
    if (mode === 'equal') return Number(row.count)
    return VALUE.test(row.value.trim()) ? Number(row.value) * Number(row.count) : null
  })
  const sum = weights.reduce((total, w) => total + (w ?? 0), 0)
  if (weights.some((w) => w === null) || sum <= 0) return rows.map(() => null)
  const cents = toCents(itemCost)
  return weights.map((w) => Math.floor((cents * w) / sum))
}

/** What is wrong with the rows, or '' when they can be sent. */
function problems(rows, mode) {
  if (rows.length < 2) return 'A lot splits into at least two pieces.'
  if (rows.some((row) => !WHOLE.test(row.count) || Number(row.count) < 1)) {
    return 'Every piece holds at least one item: Pieces must be a whole number.'
  }
  if (
    rows.some((row) => row.year.trim() !== '' && !/^-?\d{1,4}$/.test(row.year.trim()))
  ) {
    return 'A year is a whole number, like 1980, or blank to keep the lot’s.'
  }
  if (mode === 'relative' && rows.some((row) => !VALUE.test(row.value.trim()))) {
    return 'Dividing by value needs a value for every piece, like 0.50.'
  }
  return ''
}

export default function SplitDialog({ item, onSplit, onClose }) {
  const [mode, setMode] = useState('equal')
  const [rows, setRows] = useState(() => startingRows(item))
  // The Pieces box as typed: it may be blank for a moment while a number is
  // retyped, which the rows themselves never are.
  const [countText, setCountText] = useState(() => String(rows.length))
  const [acknowledged, setAcknowledged] = useState(false)
  const [error, setError] = useState('')
  const [splitting, setSplitting] = useState(false)

  // Guards the continuation once the request settles: Cancel or Escape can
  // unmount the dialog meanwhile.
  const mounted = useMounted()

  // Only a listing can be acknowledged; an order refuses the split outright,
  // and the server says so.
  const listed = (item.sale_state ?? []).filter((use) => use.kind === 'listing')
  const estimate = shares(rows, mode, item.item_cost)

  function setCount(e) {
    const text = e.target.value
    setCountText(text)
    if (!WHOLE.test(text)) return
    const count = Math.min(Number(text), MAX_PIECES)
    setRows((current) =>
      count <= current.length
        ? current.slice(0, Math.max(count, 0))
        : [
            ...current,
            ...Array.from({ length: count - current.length }, () => blankRow(item)),
          ],
    )
  }

  const setCell = (index, key) => (e) => {
    const text = e.target.value
    setRows((current) =>
      current.map((row, at) => (at === index ? { ...row, [key]: text } : row)),
    )
  }

  const problem = problems(rows, mode)
  const canSplit = !splitting && problem === '' && (listed.length === 0 || acknowledged)

  async function split() {
    setSplitting(true)
    setError('')
    // The seller's words stay the seller's: every piece came out of the one
    // listing, so each carries the lot's title. What a piece *is* goes in its
    // description.
    const title = item.source_title || item.description || item.item_code
    const payload = {
      mode,
      pieces: rows.map((row) => ({
        source_title: title,
        piece_count: Number(row.count),
        ...(row.description.trim() !== '' && { description: row.description.trim() }),
        ...(row.year.trim() !== '' && { year_start: Number(row.year) }),
        ...(mode === 'relative' && { relative_value: row.value.trim() }),
      })),
      ...(acknowledged && { acknowledge_for_sale: true }),
    }
    try {
      const result = await api.splitItem(item.id, payload)
      if (!mounted.current) return
      onSplit(result)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
      setSplitting(false)
    }
  }

  const label = `Split ${item.item_code} into pieces`
  return (
    <ModalDialog label={label} onClose={onClose}>
      <HelpScope>
        <h2>{label}</h2>
        <p className="muted">
          Each piece becomes an item of its own, with a share of the lot&apos;s cost.
          The lot is kept, marked split, and is no longer counted. This cannot be
          undone.
        </p>
        {error && <p className="error">{error}</p>}
        <ForSaleNotice
          uses={listed}
          checked={acknowledged}
          onChange={setAcknowledged}
          action="End the offer and split it"
        />
        <div className="filter-grid">
          <label data-help="split_pieces">
            <span>Pieces</span>
            <input
              type="number"
              min="2"
              max={MAX_PIECES}
              value={countText}
              onChange={setCount}
            />
          </label>
          <label data-help="split_mode">
            <span>Divide the cost</span>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="equal">Equally</option>
              <option value="relative">By value</option>
            </select>
          </label>
        </div>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Description</th>
              <th>Year</th>
              <th>Items</th>
              {mode === 'relative' && <th>Value of one</th>}
              <th>Cost, about</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.key}>
                <td>{index + 1}</td>
                <td data-help="split_description">
                  <input
                    aria-label={`Description of piece ${index + 1}`}
                    value={row.description}
                    onChange={setCell(index, 'description')}
                  />
                </td>
                <td data-help="split_year">
                  <input
                    aria-label={`Year of piece ${index + 1}`}
                    inputMode="numeric"
                    size={6}
                    value={row.year}
                    onChange={setCell(index, 'year')}
                  />
                </td>
                <td data-help="split_items">
                  <input
                    aria-label={`Items in piece ${index + 1}`}
                    inputMode="numeric"
                    size={4}
                    value={row.count}
                    onChange={setCell(index, 'count')}
                  />
                </td>
                {mode === 'relative' && (
                  <td data-help="split_value">
                    <input
                      aria-label={`Value of piece ${index + 1}`}
                      inputMode="decimal"
                      size={8}
                      value={row.value}
                      onChange={setCell(index, 'value')}
                    />
                  </td>
                )}
                <td>{estimate[index] === null ? '--' : fromCents(estimate[index])}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted">
          The lot cost {item.item_cost ?? '--'} plus {item.shipping_cost ?? '0.00'}{' '}
          shipping; shipping is divided the same way.
        </p>
        {problem && <p className="error">{problem}</p>}
        <div className="row">
          <button disabled={!canSplit} onClick={split}>
            {splitting ? 'Splitting...' : `Split into ${rows.length} pieces`}
          </button>
          <button className="link" onClick={onClose}>
            Cancel
          </button>
        </div>
      </HelpScope>
    </ModalDialog>
  )
}
