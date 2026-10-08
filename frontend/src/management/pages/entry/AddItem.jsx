import { useRef, useState } from 'react'

import { api } from '../../api'
import { ReferenceSelect } from '../../../shared/reference'
import ItemEditDialog from '../inventory/ItemEditDialog'

//: What the next piece of one purchase shares with the last, copied by "Add
//: another like it". The pieces entered one after another usually came from
//: one listing and are put away in one place; what varies piece to piece --
//: grade, serial, certificate, cost, the years -- is left for the editor.
const SHARED_ON_REPEAT = [
  'item_kind',
  'source_title',
  'sellers_item_id',
  'listing_url',
  'status',
  'storage_location_id',
  'country',
  'denomination',
  'series',
  'series_year',
  'series_letter',
  'seal_color',
  'fed_district',
  'note_type',
  'signature_combination',
  'grading_service',
  'metal',
  'mint',
]

//: The country a new item starts with.
const DEFAULT_COUNTRY = 'US'

//: Sent only for the kind that has them: a note's fields on anything else,
//: or a coin's on a note, is refused.
const NOTE_ONLY = new Set([
  'series_year',
  'series_letter',
  'seal_color',
  'fed_district',
  'note_type',
  'signature_combination',
])
const COIN_ONLY = new Set(['metal', 'mint'])

//: The statuses an item may be entered with; the server refuses any other.
const ENTRY_STATUSES = new Set(['ordered', 'received'])

/**
 * The fields of `item` the next piece shares, as a create takes them.
 *
 * A value a rule filled on `item` (`item.derived`, by column) is left out:
 * it followed from that piece's own year or serial, which the next piece
 * does not share, and sent here it would be taken as typed by a person and
 * held against whatever the next piece's facts decide. Left out, the rules
 * fill it for the next piece from its own facts. A status an item cannot be
 * entered with is left out too, and the next piece starts as ordered.
 */
function sharedWith(item) {
  const note = item.item_kind === 'currency'
  const derived = item.derived ?? {}
  return Object.fromEntries(
    SHARED_ON_REPEAT.filter((key) => !(note ? COIN_ONLY : NOTE_ONLY).has(key))
      .filter((key) => !(key in derived) && !(`${key}_id` in derived))
      .map((key) => [key, item[key]])
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .filter(([key, value]) => key !== 'status' || ENTRY_STATUSES.has(value)),
  )
}

/**
 * Enter an item on a purchase, in the item editor itself.
 *
 * There is one form for an item, the editor: entering one and correcting
 * one must not differ in what they offer or in what Suggest description
 * writes. So this asks only for what the server needs to make the row --
 * its kind and a title -- creates it, and opens the editor on it, where the
 * photographs, the Friedberg number, the errors and everything else are.
 *
 * Closing the editor without saving removes the row again: an item nobody
 * saved was never entered. "Add another like it" starts the next piece from
 * what the last one shares with it (`SHARED_ON_REPEAT`, less what a rule
 * filled -- see `sharedWith`).
 *
 * `defaults` is the purchase's tax values, sent with every item entered on
 * it; `orderUrl` its lot page, offered as the item's listing.
 */
export default function AddItem({
  purchaseOrderId,
  orderUrl = null,
  defaults,
  onChanged,
  disabledReason = '',
}) {
  const [kind, setKind] = useState('coin')
  const [title, setTitle] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // The item just created and open in the editor, until it is saved or closed.
  const [entering, setEntering] = useState(null)
  // The last item saved here, which "Add another like it" starts from.
  const [last, setLast] = useState(null)
  // Whether the editor has written anything to the item it has open: a
  // save that got part of the way leaves a real item behind.
  const written = useRef(false)

  async function create(fields) {
    setBusy(true)
    try {
      const created = await api.createInventoryItem({
        purchase_order_id: purchaseOrderId,
        ...(orderUrl && { listing_url: orderUrl }),
        ...fields,
        // The purchase's tax defaults, resolved once for every item entered
        // on it -- not something asked item by item.
        tax_rate: defaults?.tax_rate ?? null,
        tax_includes_shipping: defaults?.tax_includes_shipping ?? null,
      })
      setError('')
      setTitle('')
      written.current = false
      setEntering(created)
      onChanged?.()
    } catch (err) {
      // Kept in place: a refusal must not throw away what was typed.
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  function add(e) {
    e.preventDefault()
    if (!title.trim() || disabledReason) return
    // Nearly everything bought is American: the country starts as the
    // United States, to be changed or emptied in the editor. A default, not
    // a fact about the piece -- which is why it is set here, where the item
    // is made, and not filled in by a rule.
    create({ item_kind: kind, source_title: title.trim(), country: DEFAULT_COUNTRY })
  }

  async function addAnother() {
    setBusy(true)
    try {
      // Read again: the editor may have changed what the next piece shares.
      const saved = await api.getInventoryItem(last.id)
      await create(sharedWith(saved))
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  function saved() {
    setLast(entering)
    setEntering(null)
    onChanged?.()
  }

  // Closed without a save: the row made for it was never an entry. Unless
  // a save wrote some of it -- then it is an item, and it stays.
  async function abandoned() {
    const item = entering
    setEntering(null)
    if (written.current) {
      setLast(item)
      onChanged?.()
      return
    }
    try {
      await api.deleteInventoryItem(item.id)
    } catch (err) {
      setError(`${item.item_code} was not removed: ${err.message}`)
    }
    onChanged?.()
  }

  const blocked = busy || Boolean(disabledReason) || Boolean(entering)

  return (
    <div className="admin-form">
      <h3>New item</h3>
      {error && <p className="error">{error}</p>}
      {disabledReason && <p className="error">{disabledReason}</p>}
      <form className="form-grid" onSubmit={add}>
        <label data-help="item_kind">
          Kind
          <ReferenceSelect
            table="item_kind"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            allowAdd={false}
            allowBlank={false}
          />
        </label>
        <label data-help="source_title">
          Title{/* */}
          <input
            type="text"
            value={title}
            maxLength={500}
            placeholder="What the seller called it"
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <div className="row">
          <button
            type="submit"
            data-help="add_item"
            disabled={blocked || !title.trim()}
          >
            {busy ? 'Adding...' : 'Add item'}
          </button>
          {last && (
            <button
              type="button"
              data-help="add_another"
              disabled={blocked}
              onClick={addAnother}
            >
              Add another like {last.item_code}
            </button>
          )}
        </div>
      </form>
      <p className="muted">
        The item opens in the editor, where its details, photographs and description are
        entered. Close the editor without saving and the item is not kept.
      </p>
      {entering && (
        <ItemEditDialog
          key={entering.id}
          itemId={entering.id}
          onSaved={saved}
          onChanged={() => {
            written.current = true
            onChanged?.()
          }}
          onClose={abandoned}
        />
      )}
    </div>
  )
}
