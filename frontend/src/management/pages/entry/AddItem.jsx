import { useRef, useState } from 'react'

import { api } from '../../api'
import { ReferenceSelect } from '../../../shared/reference'
import ItemEditDialog from '../inventory/ItemEditDialog'

//: What the next piece of one purchase shares with the last, copied by "Add
//: another like it". The pieces entered one after another usually came from
//: one listing, at one price and one shipping charge, and are put away in one
//: place, and are mostly of one grade and strike; what varies piece to
//: piece -- the grade designation (but a set's, see `SET_ONLY`), serial,
//: certificate, the years, the description -- is left for the editor.
const SHARED_ON_REPEAT = [
  'item_kind',
  'source_title',
  'item_cost',
  'shipping_cost',
  'sellers_item_id',
  'listing_url',
  'status',
  'storage_location_id',
  'country',
  'denomination',
  'series',
  // A run of sets, one Proof Set after another.
  'set_form',
  // A run of bullion, one Silver Eagle or Goldback after another.
  'bullion_form',
  'series_year',
  'series_letter',
  'seal_color',
  'fed_district',
  'note_type',
  'signature_combination',
  'grading_service',
  'metal',
  'mint',
  'grade',
  'strike_type',
  'grade_designation',
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
const COIN_ONLY = new Set(['metal', 'mint', 'bullion_form', 'strike_type'])
//: Copied only from a set: a run of sets is bought as one designation
//: (PR69 DCAM Proof Sets), where a loose piece's is judged on its own.
const SET_ONLY = new Set(['grade_designation'])

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
  const set = item.item_kind === 'set'
  const derived = item.derived ?? {}
  return Object.fromEntries(
    SHARED_ON_REPEAT.filter((key) => !(note ? COIN_ONLY : NOTE_ONLY).has(key))
      .filter((key) => set || !SET_ONLY.has(key))
      .filter((key) => !(key in derived) && !(`${key}_id` in derived))
      .map((key) => [key, item[key]])
      .filter(([, value]) => value !== null && value !== undefined && value !== '')
      .filter(([key, value]) => key !== 'status' || ENTRY_STATUSES.has(value)),
  )
}

/**
 * The tax `item` was recorded with, as a create takes it.
 *
 * The next piece came from the same listing and was taxed the same way, so
 * it takes this one's rate and shipping-tax setting over the purchase's
 * defaults: "No sales tax charged" ticked in this item's editor (a zero
 * rate) stays ticked on the next. A value the item does not hold is left
 * to the purchase's defaults.
 */
function taxOf(item) {
  return Object.fromEntries(
    ['tax_rate', 'tax_includes_shipping']
      .map((key) => [key, item[key]])
      .filter(([, value]) => value !== null && value !== undefined),
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
 * saved was never entered. "Add another like it" is offered whenever the
 * purchase holds an item, and starts the next piece from what that item
 * shares with it (`SHARED_ON_REPEAT`, less what a rule filled -- see
 * `sharedWith`), and with its tax (`taxOf`).
 *
 * `items` is the purchase's items, oldest first, and `likeId` the one last
 * opened on the page -- from its table, or entered here, which `onEntered`
 * reports. That is the item the next is made like; with none opened, or one
 * no longer on the purchase, it is the latest. `defaults` is the purchase's
 * tax values, sent with every item entered on it; `orderUrl` the lot page
 * the server names for it (`lot_page`), sent as the item's listing, and
 * null when there is none.
 */
export default function AddItem({
  purchaseOrderId,
  orderUrl = null,
  defaults,
  items = [],
  likeId = null,
  onEntered,
  onChanged,
  disabledReason = '',
}) {
  const [kind, setKind] = useState('coin')
  const [title, setTitle] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // The item just created and open in the editor, until it is saved or closed.
  const [entering, setEntering] = useState(null)
  // Items closed without a save. `items` goes on holding one until the
  // purchase is read again, and it is not there to be copied.
  const [abandonedIds, setAbandonedIds] = useState([])
  // Whether the editor has written anything to the item it has open: a
  // save that got part of the way leaves a real item behind.
  const written = useRef(false)

  const kept = items.filter((item) => !abandonedIds.includes(item.id))
  // The item "Add another like it" starts from.
  const like = kept.find((item) => item.id === likeId) ?? kept.at(-1) ?? null

  async function create(fields) {
    setBusy(true)
    try {
      const created = await api.createInventoryItem({
        purchase_order_id: purchaseOrderId,
        ...(orderUrl && { listing_url: orderUrl }),
        // The purchase's tax defaults, resolved once for every item entered
        // on it -- not something asked item by item. A piece made like
        // another brings that one's tax in `fields` instead.
        tax_rate: defaults?.tax_rate ?? null,
        tax_includes_shipping: defaults?.tax_includes_shipping ?? null,
        ...fields,
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
    // The purchase's own reason is printed above the form.
    if (disabledReason) return
    if (!title.trim()) {
      setError('Give the item a title: what the seller called it.')
      return
    }
    // Nearly everything bought is American: the country starts as the
    // United States, to be changed or emptied in the editor. A default, not
    // a fact about the piece -- which is why it is set here, where the item
    // is made, and not filled in by a rule.
    create({ item_kind: kind, source_title: title.trim(), country: DEFAULT_COUNTRY })
  }

  async function addAnother() {
    if (disabledReason) return
    setBusy(true)
    try {
      // Read again: the editor may have changed what the next piece shares.
      const saved = await api.getInventoryItem(like.id)
      await create({ ...sharedWith(saved), ...taxOf(saved) })
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  function saved() {
    onEntered?.(entering.id)
    setEntering(null)
    onChanged?.()
  }

  // Closed without a save: the row made for it was never an entry. Unless
  // a save wrote some of it -- then it is an item, and it stays.
  async function abandoned() {
    const item = entering
    setEntering(null)
    if (written.current) {
      onEntered?.(item.id)
      onChanged?.()
      return
    }
    setAbandonedIds((ids) => [...ids, item.id])
    try {
      await api.deleteInventoryItem(item.id)
    } catch (err) {
      // Still an item, and still on the purchase.
      setAbandonedIds((ids) => ids.filter((id) => id !== item.id))
      setError(`${item.item_code} was not removed: ${err.message}`)
    }
    onChanged?.()
  }

  // Held only while a request is out or the editor is open. A reason the
  // purchase gives, or a missing title, is said instead, and the press sends
  // nothing.
  const blocked = busy || Boolean(entering)

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
          <button type="submit" data-help="add_item" disabled={blocked}>
            {busy ? 'Adding...' : 'Add item'}
          </button>
          {like && (
            <button
              type="button"
              data-help="add_another"
              disabled={blocked}
              onClick={addAnother}
            >
              Add another like {like.item_code}
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
