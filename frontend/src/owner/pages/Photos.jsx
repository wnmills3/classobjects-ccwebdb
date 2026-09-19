import { useEffect, useState } from 'react'

import { api } from '../api'

/**
 * The photographs the import pass could not place, and the ones detached
 * from the wrong item.
 *
 * The import that read item codes out of ~673 filenames refused to guess on
 * purpose: a name that does not follow the convention, an unknown item
 * code, a deleted or split item, two files claiming one slot, or a slot
 * already occupied all come out *unattached* rather than filed under a
 * best-effort guess. `PhotosPanel` (the item editor's photographs tab) is
 * the place a photograph gets filed onto the item it belongs to; this page
 * is where the ones nobody has filed yet -- and the ones a later correction
 * detaches from the wrong item -- wait, and where they get sent on.
 *
 * `api.listUnattachedImages()` already returns them newest-capture-first,
 * nulls last -- the order a person working through a box of prints would
 * recognise them in. This page renders that order as given; a `.sort()`
 * here would silently put them back into id order and undo the reason the
 * server orders them at all.
 *
 * Every row's item picker searches by item code rather than offering the
 * receiving-flow's `ItemFinder`: that component defaults to items that have
 * not arrived yet and forces a coins/currency choice before a code can even
 * be typed, which is the wrong shape for a photograph whose item is
 * probably already received (and may already be sold). What it does borrow
 * from `ItemFinder` is the technique -- a code does not say which view the
 * item lives in, so both `coins` and `currency` are searched in parallel and
 * the results merged -- and the result list's markup, a button per row
 * naming the item code and description.
 *
 * A successful `api.attachImage` drops the row from local state instead of
 * refetching the whole page. The operator is working through a list start
 * to finish; a refetch would re-order or re-page underneath them and lose
 * their place for no benefit, since the row they just filed is exactly the
 * one row that is certainly gone from the server's unattached set now.
 */

const INVENTORY_VIEWS = ['coins', 'currency']

/**
 * Finds one inventory item by code, searching both views since an item code
 * alone does not say whether it is a coin or a piece of currency.
 *
 * `onPick` receives the chosen row (`{ id, item_code, description }`) or
 * `null` when the operator edits the code after having picked one -- a
 * stale selection pointing at a different item than the box now on screen
 * is worse than no selection at all.
 */
function ItemPicker({ onPick }) {
  const [code, setCode] = useState('')
  const [matches, setMatches] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  function changeCode(value) {
    setCode(value)
    setMatches(null)
    onPick(null)
  }

  async function find() {
    const wanted = code.trim()
    if (!wanted) return
    setBusy(true)
    setError('')
    try {
      const pages = await Promise.all(
        INVENTORY_VIEWS.map((view) => api.searchInventory(view, { item_code: wanted })),
      )
      const rows = pages.flatMap((page) => page.rows)
      setMatches(rows)
      if (rows.length === 0) setError('No item matches that code.')
    } catch (err) {
      setError(err.message)
      setMatches(null)
    } finally {
      setBusy(false)
    }
  }

  function pick(row) {
    setMatches(null)
    setError('')
    setCode(row.item_code)
    onPick(row)
  }

  return (
    <div className="item-picker">
      <label>
        Item code
        <input
          type="text"
          value={code}
          onChange={(e) => changeCode(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              find()
            }
          }}
        />
      </label>
      <button type="button" disabled={busy} onClick={find}>
        Find
      </button>
      {error && <p className="error">{error}</p>}
      {matches && matches.length > 0 && (
        <ul className="item-picker-matches">
          {matches.map((row) => (
            <li key={row.id}>
              <button
                type="button"
                className="item-picker-row"
                onClick={() => pick(row)}
              >
                <span className="mono">{row.item_code}</span> {row.description}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** One unattached photograph: its thumbnail, an item picker, and the Link
 * action that files it. */
function PhotoRow({ row, onLinked }) {
  const [selected, setSelected] = useState(null)
  const [acknowledgeForSale, setAcknowledgeForSale] = useState(false)
  const [error, setError] = useState('')
  const [linking, setLinking] = useState(false)

  function link() {
    if (!selected) return
    setLinking(true)
    api
      .attachImage(row.image_id, {
        inventoryItemId: selected.id,
        acknowledgeForSale,
      })
      .then(() => {
        setError('')
        onLinked(row.image_id)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLinking(false))
  }

  return (
    <li className="photo-picker-row">
      <img src={row.thumbnail_url} alt="Unattached photograph" />
      <ItemPicker onPick={setSelected} />
      {selected && (
        <p className="muted">
          Chosen: <span className="mono">{selected.item_code}</span>{' '}
          {selected.description}
        </p>
      )}
      <label className="checkbox">
        <input
          type="checkbox"
          checked={acknowledgeForSale}
          onChange={(e) => setAcknowledgeForSale(e.target.checked)}
        />
        Attach even though the item is for sale
      </label>
      {error && <p className="error">{error}</p>}
      <button type="button" disabled={!selected || linking} onClick={link}>
        Link
      </button>
    </li>
  )
}

export default function Photos() {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    api
      .listUnattachedImages()
      .then((data) => {
        if (cancelled) return
        setRows(data)
        setError('')
      })
      .catch((err) => {
        if (cancelled) return
        setError(err.message)
        setRows([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  function drop(imageId) {
    // The row that was just linked is certainly gone from the server's
    // unattached set now -- filtering it out locally is exact, not a guess,
    // and keeps every other row's position on screen.
    setRows((prev) => (prev ?? []).filter((r) => r.image_id !== imageId))
  }

  const loading = rows === null

  return (
    <div className="photos-page">
      <h2>Unattached photographs</h2>
      {loading && <p className="muted">Loading...</p>}
      {!loading && error && <p className="error">{error}</p>}
      {!loading && rows.length === 0 && (
        <p className="muted">Nothing waiting to be filed.</p>
      )}
      {!loading && rows.length > 0 && (
        <ul className="photo-grid">
          {rows.map((row) => (
            <PhotoRow key={row.image_id} row={row} onLinked={drop} />
          ))}
        </ul>
      )}
    </div>
  )
}
