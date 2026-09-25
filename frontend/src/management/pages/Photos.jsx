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
 * recognize them in. This page renders that order as given; a `.sort()`
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
 *
 * The for-sale acknowledgement is driven by the server's refusal, the same
 * shape `ReceiptPanel` uses and for the same reason: this page holds no
 * `sale_state` for the items it can attach to -- the row does not even know
 * which item that is until an `ItemPicker` search returns one -- so an
 * always-present "attach anyway" checkbox would be guessing on every row
 * whether there is anything to acknowledge. `ForSaleNotice`'s own docstring
 * names the cost of guessing wrong: a control that is on for every row
 * regardless teaches the operator to tick it without reading it. Fetching
 * `sale_state` per picked item to decide would avoid that, but at the price
 * of a request per selection just to answer a question the attach attempt
 * already answers for free -- `PhotoRow` attempts the attach with no
 * acknowledgement first, and only shows anything when the 409 says there is
 * something to acknowledge, with the server's own message displayed
 * verbatim so the operator sees which listing or order they would be
 * overriding.
 *
 * That confirmation renders inline in the row rather than in `ReceiptPanel`'s
 * `ModalDialog`. `ModalDialog`'s own docstring gives its reason for being
 * modal: a form in the page's normal flow lands under a full table and pager,
 * below the fold, so acting on it looks like it did nothing. Nothing here is
 * below any fold -- the refusal appears in the exact row the operator just
 * clicked Link on, already on screen -- and this page is a grid of many rows
 * an operator works through one after another, where blocking every other
 * row's picker and Link button until one row's question is answered would
 * cost more than the modal's isolation buys back.
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

/**
 * One unattached photograph: its thumbnail, an item picker, and the Link
 * action that files it.
 *
 * `refusal` holds the server's 409 message while it is being answered -- a
 * question, not a failure, the same distinction `ReceiptPanel` draws with
 * `forSaleRefusal`, so it is kept out of `error` (the plain "something went
 * wrong" line) and shown as its own inline block instead. Picking a
 * different item clears it: a refusal names a specific listing or order on
 * the item that was selected when the attempt was made, and it would be
 * wrong to carry that answer over to a different item nobody has attempted
 * yet.
 */
function PhotoRow({ row, onLinked }) {
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState('')
  const [refusal, setRefusal] = useState('')
  const [linking, setLinking] = useState(false)

  function pickItem(item) {
    setSelected(item)
    setError('')
    setRefusal('')
  }

  function attempt(acknowledgeForSale) {
    if (!selected) return
    setLinking(true)
    api
      .attachImage(row.image_id, {
        inventoryItemId: selected.id,
        acknowledgeForSale,
      })
      .then(() => {
        setError('')
        setRefusal('')
        onLinked(row.image_id)
      })
      .catch((err) => {
        // A 409 whose message starts "For sale" is `sale_state.guard`
        // asking to be told again, on purpose -- see the module docstring.
        // Anything else (a network failure, a 404 because the item was
        // deleted between the search and the click) is an ordinary failure.
        if (err.status === 409 && err.message.startsWith('For sale')) {
          setError('')
          setRefusal(err.message)
          return
        }
        setRefusal('')
        setError(err.message)
      })
      .finally(() => setLinking(false))
  }

  return (
    <li className="photo-picker-row">
      <img src={row.thumbnail_url} alt="Unattached photograph" />
      <ItemPicker onPick={pickItem} />
      {selected && (
        <p className="muted">
          Chosen: <span className="mono">{selected.item_code}</span>{' '}
          {selected.description}
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {!refusal && (
        <button
          type="button"
          disabled={!selected || linking}
          onClick={() => attempt(false)}
        >
          Link
        </button>
      )}
      {refusal && (
        <div className="for-sale" role="alert">
          {refusal}
          <div className="row">
            <button type="button" disabled={linking} onClick={() => attempt(true)}>
              {linking ? 'Linking...' : 'Link anyway'}
            </button>
            <button type="button" className="link" onClick={() => setRefusal('')}>
              Leave it unlinked
            </button>
          </div>
        </div>
      )}
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
      {/* `!error` as well as the count: the catch sets rows to [] so the page
          stays usable rather than stuck on "Loading...", which without this
          guard renders the failure and "Nothing waiting to be filed."
          together -- telling the operator both that the page failed and that
          there is nothing to do. */}
      {!loading && !error && rows.length === 0 && (
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
