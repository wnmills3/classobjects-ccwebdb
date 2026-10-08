import { useState } from 'react'

import { api } from '../../api'
import ForSaleNotice from '../ForSaleNotice'
import HelpScope from '../../HelpScope'
import LocationSelect from '../../LocationSelect'
import ModalDialog from '../../ModalDialog'
import OfferDialog from './OfferDialog'
import { useMounted } from '../../useMounted'
import { useRequest } from '../../../shared/useRequest'
import { ASSEMBLING_LOTS } from '../assembling-lots'

/**
 * Set one field across a selection, or offer it for sale.
 *
 * One field at a time on purpose. The bulk case is "these twenty are all
 * 1964" -- a form offering every field at once invites setting four of them
 * across twenty coins from one glance at one coin.
 *
 * The server applies it in a single transaction, so a rejected edit changes
 * nothing and there is never a half-applied selection to report.
 *
 * A selection holding items that are up for sale is refused, naming them; a
 * box then appears to change them anyway.
 *
 * "Offer for sale..." works on the same selection but needs more than its
 * ids: a price, a title and a cost basis per item. Those come from `rows`,
 * the page of results on screen. A selection survives paging, so an id with
 * no row on this page cannot be priced -- the dialog says how many of those
 * there are rather than quietly offering fewer items than were selected.
 *
 * "Group into lot..." needs **only the ids**, so it takes the whole selection
 * including the off-page part of it. `POST`/`PATCH /api/sales-lots` name
 * items by id and ask for nothing else about them, so there is nothing here
 * that an id with no row on this page would be missing -- and dropping those
 * ids to match the offer path would silently build a smaller lot than the one
 * that was selected.
 */

//: The refusal the server gives a change to items that are for sale.
const FOR_SALE = 'For sale'

//: The help topic for a field where it is not the field's own name: the
//: bar's Year is the one-year box the item editor explains as `year`.
const HELP_FOR = { year_start: 'year' }

const BULK_FIELDS = [
  //: Coins only: a note's year is its series year, and it holds no other.
  ['Year', 'year_start', 'number', 'coins'],
  ['Grade', 'grade', 'text'],
  ['Country', 'country', 'text'],
  ['Denomination', 'denomination', 'text'],
  //: Coins only. Paper has no metal, and `metal` is a coin-view column and
  //: filter in `inventory_search` -- it does not exist on the currency view.
  ['Metal', 'metal', 'text', 'coins'],
  //: Where the selection is kept: a storage location picked, or added, from
  //: the same picker the item editor uses. Each item's move is recorded.
  ['Location', 'storage_location_id', 'location'],
]

/**
 * "Group into lot...": the selection becomes a new lot, or joins one.
 *
 * **Two calls for a new lot, not one.** `SalesLotIn` is `extra="forbid"` and
 * holds a title and a description: a lot "begins `assembling` and empty;
 * members are a PATCH" (its own docstring), so a POST carrying
 * `add_item_ids` is a 422, not a shortcut. The membership PATCH that follows
 * is itself all-or-nothing, so a refused one leaves the new lot standing and
 * empty -- which is what the message then says, rather than leaving the
 * operator to discover it on the Lots page.
 *
 * Adding to a lot that is already assembling is the same PATCH against an
 * existing id, and is here rather than on the Lots page for the reason that
 * page gives: this is where the coins are, with the filters and the paging
 * that found them.
 *
 * A failed load of the assembling lots leaves the dialog usable -- starting a
 * new lot needs none of them -- rather than replacing it with an error.
 */
function GroupIntoLot({ ids, codes, onGrouped, onClose }) {
  const assembling = useRequest('assembling', () => api.listLots(ASSEMBLING_LOTS))
  const lots = assembling.data?.lots ?? null
  // 'new', or the id of an assembling lot as the select's string value.
  const [target, setTarget] = useState('new')
  const [title, setTitle] = useState('')
  const [groupError, setError] = useState('')
  const error = groupError || assembling.error
  const [busy, setBusy] = useState(false)
  // The new lot this dialog made whose coins were then refused: asked again
  // under the same title, it is that lot that is filled, not a second one.
  const [started, setStarted] = useState(null)

  // Guards group()'s continuation once the request settles: Cancel (and
  // Escape, which ModalDialog routes to onClose) can unmount this dialog
  // while a write is still in flight.
  const mounted = useMounted()

  async function group() {
    const wanted = title.trim()
    if (target === 'new' && wanted === '') {
      // Said here rather than left to the API, whose refusal for a blank
      // title is a schema complaint about `min_length`.
      setError('A lot needs a title: it is what the offer and the shop call it.')
      return
    }
    setBusy(true)
    setError('')
    let lot = lots?.find((row) => String(row.id) === target) ?? null
    try {
      if (target === 'new') {
        lot =
          started?.title === wanted
            ? started
            : await api.createLot({ title: wanted, description: '' })
        if (mounted.current) setStarted(lot)
      }
      await api.updateLot(lot.id, { add_item_ids: ids, version: lot.version })
      if (!mounted.current) return
      onGrouped(lot.title)
    } catch (err) {
      if (!mounted.current) return
      setError(
        target === 'new' && lot !== null
          ? `${lot.title} was started but is empty: ${err.message}`
          : err.message,
      )
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const label = `Group ${ids.length} item(s) into a lot`
  const open = lots ?? []
  const action = target === 'new' ? 'Create lot' : 'Add to lot'
  //: Selected ids this page has not loaded a row for, and so cannot name.
  const offPage = ids.length - codes.length

  // What the dialog holds, set inside its help scope below.
  const body = (
    <>
      <h2>{label}</h2>
      {error && <p className="error">{error}</p>}
      {codes.length > 0 && (
        <p className="muted">
          {codes.join(', ')}
          {/* The heading counts the whole selection and this line can only
              name the rows this page has loaded. Without the difference
              spelled out, "Group 4 item(s) into a lot" over two codes reads
              as two. The off-page ids DO go into the lot -- grouping needs
              only ids -- which is the opposite of what the offer dialog says
              about its own skipped rows, so this says which it is. */}
          {offPage > 0 &&
            ` and ${offPage} more not on this page, which ${offPage === 1 ? 'is' : 'are'} grouped too.`}
        </p>
      )}
      <div className="filter-grid">
        <label data-help="lot_target">
          Lot
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="new">A new lot</option>
            {open.map((lot) => (
              <option key={lot.id} value={String(lot.id)}>
                {lot.title} ({lot.members.length} so far)
              </option>
            ))}
          </select>
        </label>
        {target === 'new' && (
          <label data-help="lot_title">
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
        )}
      </div>
      <div className="row">
        <button disabled={busy} onClick={group}>
          {busy ? 'Grouping...' : action}
        </button>
        <button className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </>
  )

  return (
    <ModalDialog label={label} onClose={onClose}>
      <HelpScope>{body}</HelpScope>
    </ModalDialog>
  )
}

/**
 * The bar shown while anything is selected; nothing when `ids` is empty.
 *
 * `ids` is the whole selection and `rows` the page on screen, so the offer
 * dialog gets only the selected rows this page holds while a field change
 * and a grouping take every id. `view` ('coins' or 'currency') decides
 * which fields are offered. `onApplied` runs after a field change is saved,
 * `onOffered` with the ids that were offered, and `onClear` drops the
 * selection.
 */
export default function BulkEditBar({
  ids,
  rows = [],
  onApplied,
  onOffered,
  onClear,
  view,
}) {
  // The first field this view offers: Year is a coin's, so a currency
  // selection starts on Grade.
  const [field, setField] = useState(
    () => BULK_FIELDS.find(([, , , onlyView]) => !onlyView || onlyView === view)[1],
  )
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // The selection the server said holds items for sale, and the one the
  // operator then agreed to change. Each is kept as the selection it was
  // about, so neither the notice nor the agreement carries over to another:
  // items added since were never named.
  const selection = ids.join(',')
  const [forSaleIn, setForSaleIn] = useState(null)
  const [acknowledgedFor, setAcknowledgedFor] = useState(null)
  const forSale = forSaleIn === selection
  const acknowledged = acknowledgedFor === selection
  const [offering, setOffering] = useState(false)
  const [grouping, setGrouping] = useState(false)
  // What the last grouping did, so the bar says so where the selection is.
  const [grouped, setGrouped] = useState('')

  if (ids.length === 0) return null

  const chosen = rows.filter((row) => ids.includes(row.id))

  // A field marked for one view is offered only there; everything else is
  // shared. `view` is the config's own name for the page ('coins' | 'currency').
  const fields = BULK_FIELDS.filter(
    ([, , , onlyView]) => !onlyView || onlyView === view,
  )
  const type = fields.find(([, key]) => key === field)?.[2] ?? 'text'
  // The value box explains the field chosen, to the `HelpScope` the page
  // puts around this bar.
  const topic = HELP_FOR[field] ?? field

  async function apply() {
    setBusy(true)
    try {
      // A year and a location's id go as numbers; a code goes as typed.
      const changes = { [field]: type === 'text' ? value : Number(value) }
      if (acknowledged) changes.acknowledge_for_sale = true
      await api.bulkEditInventory(ids, changes)
      setError('')
      setValue('')
      setForSaleIn(null)
      setAcknowledgedFor(null)
      onApplied?.()
    } catch (err) {
      setError(err.message)
      if (err.message.startsWith(FOR_SALE)) setForSaleIn(selection)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bulk-bar">
      <strong>{ids.length} selected</strong>

      <select
        aria-label="Field to change"
        data-help="bulk_field"
        value={field}
        onChange={(e) => {
          // A year typed for one field is not a location's id for another.
          setField(e.target.value)
          setValue('')
        }}
      >
        {fields.map(([label, key]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>

      {type === 'location' ? (
        <span data-help={topic}>
          <LocationSelect value={value} onChange={setValue} />
        </span>
      ) : (
        <input
          type={type}
          data-help={topic}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="New value"
        />
      )}

      <button disabled={busy || value === ''} onClick={apply}>
        {busy ? 'Applying...' : `Apply to ${ids.length}`}
      </button>
      <button disabled={chosen.length === 0} onClick={() => setOffering(true)}>
        Offer for sale...
      </button>
      <button
        onClick={() => {
          setGrouped('')
          setGrouping(true)
        }}
      >
        Group into lot...
      </button>
      <button className="link" onClick={onClear}>
        Clear selection
      </button>

      <ForSaleNotice
        show={forSale}
        heading="Some of the selected items are for sale"
        checked={acknowledged}
        onChange={(agreed) => setAcknowledgedFor(agreed ? selection : null)}
        action="Change the items for sale too"
      />
      {error && <span className="error">{error}</span>}
      {grouped && <span className="notice">{grouped}</span>}

      {grouping && (
        <GroupIntoLot
          ids={ids}
          codes={chosen.map((row) => row.item_code)}
          onGrouped={(title) => {
            setGrouping(false)
            // The selection is deliberately NOT cleared. Grouping changes no
            // item's disposition -- a coin in an assembling lot is still in
            // stock and still editable -- so the rows the operator was
            // working through are still the rows they were working through.
            setGrouped(`${ids.length} item(s) are in ${title}.`)
          }}
          onClose={() => setGrouping(false)}
        />
      )}

      {offering && (
        <OfferDialog
          items={chosen}
          skipped={ids.length - chosen.length}
          // Named, not the bulk edit's own callback: only the items that were
          // actually offered are done with. The rest of the selection is the
          // off-page rows the dialog has just said were NOT offered, and
          // dropping those would undo the selection the operator still has to
          // deal with. The parent reads the page again either way -- every
          // offered row has a status the table does not know about.
          onOffered={() => {
            setOffering(false)
            onOffered?.(chosen.map((row) => row.id))
          }}
          onClose={() => setOffering(false)}
        />
      )}
    </div>
  )
}
