import { useEffect, useState } from 'react'

import { AccessLabel } from '../AccessLabel'
import { api } from '../api'
import ConfirmDialog from '../ConfirmDialog'
import ModalDialog from '../ModalDialog'
import SettlementGrid from './SettlementGrid'
import { RESULT_LABEL } from './auction-labels'
import { accel, useSaveShortcut } from '../shortcuts'
import { subjectOf, UNKNOWN } from './listing-labels'
import { date } from '../../shared/format'
import { useMounted } from '../useMounted'
import { useSalesVenues } from '../useSalesVenues'
import { useRequest } from '../../shared/useRequest'
import { ASSEMBLING_LOTS } from './assembling-lots'
import { orNull } from '../../shared/text'

/**
 * The Auctions page: running an auction from draft to settled.
 *
 * `GET /api/auctions` (`routers.auctions.list_auctions`) already returns
 * every auction with every lot it holds -- there is no `GET /auctions/{id}`
 * -- so this page loads the list once and every write replaces the one row
 * it touched in place, the same pattern `Listings.jsx` and `Lots.jsx` use
 * for their own single-list pages.
 *
 * **Two rules the API enforces that this page does not let the owner walk
 * into blindly** (the Task 6 brief): a lot's number and reserve can only be
 * edited while the auction is `draft`, `scheduled` or `consigned`
 * (`app.auctions.refuse_unless_lot_editable`), so the inputs below are
 * disabled outside that window rather than left live for a refusal to
 * explain; and cancelling or removing a lot from an auction whose items are
 * at an auction house (`auction.consigned_on is not None`) requires a return
 * location, so `ReturnLocationConfirm` below holds its confirm button
 * disabled until one is chosen, exactly as the API would refuse without it.
 *
 * Money is shown and sent exactly as the API carries it: a decimal string,
 * never parsed into a JavaScript number. The settlement grid itself is
 * `SettlementGrid.jsx`, rendered here once the auction is `closed` -- the
 * only status `app.auctions.settle` accepts.
 *
 * A load failure shows the error and leaves the page standing, per 872e219 --
 * an operator who has lost the whole page cannot react to what it says.
 */

/** `AuctionStatus`, in the order an auction moves through them. */
const AUCTION_STATUS_LABEL = {
  draft: 'Draft',
  scheduled: 'Scheduled',
  consigned: 'Consigned',
  closed: 'Closed',
  settled: 'Settled',
  cancelled: 'Cancelled',
}

//: `app.auctions._LOTS_REMOVABLE` -- the identical boundary
//: `refuse_unless_lot_editable` and `remove_lot` both use: a lot number, a
//: reserve and the lot itself may all still change before the sale has run.
const LOT_EDITABLE_STATUSES = ['draft', 'scheduled', 'consigned']

//: `app.auctions.add_lot` accepts only these two -- adding lots stops the
//: moment the sale is scheduled to close, well before it actually closes.
const LOTS_ADDABLE_STATUSES = ['draft', 'scheduled']

const CREATE_KEYS = { venue: 'p', title: 't', externalId: 'n', save: 'v' }

/** Starting a new auction: its platform, its title, and its own sale number. */
function AuctionForm({ venues, onSaved, onClose }) {
  const [form, setForm] = useState({
    venue: '',
    title: '',
    external_id: '',
    starts_at: '',
    ends_at: '',
    notes: '',
  })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  // Guards save()'s continuation once the request settles.
  const mounted = useMounted()

  useSaveShortcut(save, !saving)

  async function save() {
    if (form.venue === '') {
      setError('Choose a platform to run this auction on.')
      return
    }
    const title = form.title.trim()
    if (title === '') {
      setError('An auction needs a title.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const saved = await api.createAuction({
        venue: form.venue,
        title,
        external_id: orNull(form.external_id),
        starts_at: orNull(form.starts_at),
        ends_at: orNull(form.ends_at),
        notes: orNull(form.notes),
      })
      if (!mounted.current) return
      onSaved(saved)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  return (
    <ModalDialog label="Start a new auction" onClose={onClose}>
      <h2>Start a new auction</h2>
      {error && <p className="error">{error}</p>}
      <div className="filter-grid">
        <label>
          <AccessLabel text="Platform" accessKey={CREATE_KEYS.venue} />
          <select
            value={form.venue}
            onChange={set('venue')}
            {...accel(CREATE_KEYS.venue)}
          >
            <option value="">Choose a platform</option>
            {venues.map((v) => (
              <option key={v.code} value={v.code}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <AccessLabel text="Title" accessKey={CREATE_KEYS.title} />
          <input
            value={form.title}
            onChange={set('title')}
            {...accel(CREATE_KEYS.title)}
          />
        </label>
        <label>
          <AccessLabel text="Sale number" accessKey={CREATE_KEYS.externalId} />
          <input
            value={form.external_id}
            onChange={set('external_id')}
            placeholder="the house's own number for the sale"
            {...accel(CREATE_KEYS.externalId)}
          />
        </label>
        <label>
          Starts
          <input
            value={form.starts_at}
            onChange={set('starts_at')}
            placeholder="2026-10-01T18:00"
          />
        </label>
        <label>
          Ends
          <input
            value={form.ends_at}
            onChange={set('ends_at')}
            placeholder="2026-10-01T20:00"
          />
        </label>
        <label>
          Notes
          <textarea rows={2} value={form.notes} onChange={set('notes')} />
        </label>
      </div>
      <div className="row">
        <button disabled={saving} onClick={save} {...accel(CREATE_KEYS.save)}>
          <AccessLabel
            text={saving ? 'Saving...' : 'Save'}
            accessKey={CREATE_KEYS.save}
          />
        </button>
        <button className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </ModalDialog>
  )
}

const ADD_LOT_KEYS = { lotNumber: 'l' }

/**
 * Resolve an item code (`CC-######`, as the owner reads it off the coin,
 * never a raw database id -- every other item-selection surface in this
 * console, `OfferDialog.jsx`, `Lots.jsx`, `ItemFinder.jsx`, shows the code,
 * not an id) to the id `AuctionLotIn.item_id` actually needs.
 *
 * `GET /api/inventory/{view}/search` filters `item_code` with `ilike`
 * (`inventory_search.py`), already reachable through `api.searchInventory`
 * -- no new endpoint. It takes a view, though, and a code alone does not
 * say whether the item is a coin or a note, so this tries `coins` first and
 * falls back to `currency`. Null when neither view has an exact match
 * (case-insensitive, since `ilike` itself is): the caller names the code
 * that came back empty rather than a raw id nobody typed.
 */
async function findItemIdByCode(code) {
  for (const view of ['coins', 'currency']) {
    const page = await api.searchInventory(view, { item_code: code })
    const match = (page?.rows ?? []).find(
      (row) => String(row.item_code).toLowerCase() === code.toLowerCase(),
    )
    if (match) return match.id
  }
  return null
}

/**
 * Add a lot to an auction: an assembled lot, or a single item as a lot of one.
 *
 * `AuctionLotIn._one_subject` takes exactly one of `item_id` and `lot_id`
 * (spec, *Three kinds of lot*), so this offers the choice as a radio rather
 * than two forms -- the same subject a single price, title and listing
 * number describe either way.
 */
function AddLotDialog({ auction, onSaved, onClose }) {
  const [mode, setMode] = useState('item')
  const [lotNumber, setLotNumber] = useState('')
  const [itemCode, setItemCode] = useState('')
  const [lotId, setLotId] = useState('')
  const [reserve, setReserve] = useState('')
  const [price, setPrice] = useState('')
  const open = useRequest('assembling', () => api.listLots(ASSEMBLING_LOTS))
  const assemblingLots = open.data?.lots ?? []
  const [saveError, setError] = useState('')
  const error = saveError || open.error
  const [saving, setSaving] = useState(false)

  const mounted = useMounted()

  useSaveShortcut(save, !saving)

  async function save() {
    const number = lotNumber.trim()
    if (number === '') {
      setError('A lot number is required.')
      return
    }
    if (mode === 'item' && itemCode.trim() === '') {
      setError('Enter the item code to add.')
      return
    }
    if (mode === 'lot' && lotId === '') {
      setError('Choose the lot to add.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const payload = {
        lot_number: number,
        reserve: orNull(reserve),
        price: orNull(price),
      }
      if (mode === 'item') {
        const code = itemCode.trim()
        const itemId = await findItemIdByCode(code)
        if (!mounted.current) return
        if (itemId === null) {
          setError(`No item with code ${code}.`)
          return
        }
        payload.item_id = itemId
      } else {
        payload.lot_id = Number(lotId)
      }
      const saved = await api.addAuctionLot(auction.id, payload)
      if (!mounted.current) return
      onSaved(saved)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  const label = `Add a lot to ${auction.title}`

  return (
    <ModalDialog label={label} onClose={onClose}>
      <h2>{label}</h2>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <label className="checkbox">
          <input
            type="radio"
            name="lot-subject"
            value="item"
            checked={mode === 'item'}
            onChange={() => setMode('item')}
          />
          Single item
        </label>
        <label className="checkbox">
          <input
            type="radio"
            name="lot-subject"
            value="lot"
            checked={mode === 'lot'}
            onChange={() => setMode('lot')}
          />
          Assembled lot
        </label>
      </div>
      <div className="filter-grid">
        <label>
          <AccessLabel text="Lot number" accessKey={ADD_LOT_KEYS.lotNumber} />
          <input
            value={lotNumber}
            onChange={(e) => setLotNumber(e.target.value)}
            {...accel(ADD_LOT_KEYS.lotNumber)}
          />
        </label>
        {mode === 'item' ? (
          <label>
            Item code
            <input
              value={itemCode}
              onChange={(e) => setItemCode(e.target.value)}
              placeholder="CC-000123"
            />
          </label>
        ) : (
          <label>
            Lot
            <select value={lotId} onChange={(e) => setLotId(e.target.value)}>
              <option value="">Choose a lot</option>
              {assemblingLots.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.title}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          Reserve
          <input
            inputMode="decimal"
            value={reserve}
            onChange={(e) => setReserve(e.target.value)}
          />
        </label>
        <label>
          Starting bid
          <input
            inputMode="decimal"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
          />
        </label>
      </div>
      <div className="row">
        <button disabled={saving} onClick={save}>
          {saving ? 'Adding...' : 'Add lot'}
        </button>
        <button className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </ModalDialog>
  )
}

/** Marking an auction consigned: when custody moved to the house. */
function ConsignDialog({ auction, onSaved, onClose }) {
  const [onDate, setOnDate] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const mounted = useMounted()

  useSaveShortcut(save, !saving)

  async function save() {
    const trimmed = onDate.trim()
    if (trimmed === '') {
      setError('Enter the date custody moved to the house.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const saved = await api.consignAuction(auction.id, { on_date: trimmed })
      if (!mounted.current) return
      onSaved(saved)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  const label = `Mark ${auction.title} consigned`

  return (
    <ModalDialog label={label} onClose={onClose}>
      <h2>{label}</h2>
      {error && <p className="error">{error}</p>}
      <label>
        Consigned on
        <input type="date" value={onDate} onChange={(e) => setOnDate(e.target.value)} />
      </label>
      <div className="row">
        <button disabled={saving} onClick={save}>
          {saving ? 'Saving...' : 'Mark consigned'}
        </button>
        <button className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </ModalDialog>
  )
}

/**
 * The question asked before a lot is pulled out of its auction, or the whole
 * auction is cancelled.
 *
 * `returned_to_location_id` is required whenever `auction.consigned_on` is
 * set -- the items physically left the premises and something has to say
 * where they came back to -- so the picker appears then, and the confirm
 * button stays disabled until a location is chosen, the same way this page
 * never lets the owner submit a request the API would only refuse for a
 * missing field. `onConfirm` is called with the location id, or null when
 * none was needed.
 */
function ReturnLocationConfirm({
  auction,
  locations,
  question,
  confirmLabel,
  busyLabel,
  busy,
  onConfirm,
  onCancel,
  children,
}) {
  const [locationId, setLocationId] = useState('')
  const needsLocation = auction.consigned_on != null
  return (
    <ConfirmDialog
      question={question}
      confirmLabel={confirmLabel}
      busyLabel={busyLabel}
      cancelLabel="Keep it"
      busy={busy}
      disabled={needsLocation && locationId === ''}
      onConfirm={() => onConfirm(needsLocation ? Number(locationId) : null)}
      onCancel={onCancel}
    >
      {children}
      {needsLocation && (
        <label>
          Return items to
          <select
            aria-label="Return items to"
            value={locationId}
            onChange={(e) => setLocationId(e.target.value)}
          >
            <option value="">Choose a location</option>
            {locations.map((loc) => (
              <option key={loc.id} value={loc.id}>
                {loc.label}
              </option>
            ))}
          </select>
        </label>
      )}
    </ConfirmDialog>
  )
}

/**
 * One auction: its own wording, the transitions it may make now, its lot
 * table, and -- once closed -- the settlement grid.
 *
 * Owns its own busy/refusal/notice state rather than lifting it to the list
 * page, the same separation `RecordSaleDialog.jsx` keeps from `Listings.jsx`:
 * this is the one place these particular writes happen, and the list page
 * only ever needs the auction this replaced.
 */
function AuctionDetail({ auction, venues, locations, onChanged }) {
  const [refusal, setRefusal] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [lotDrafts, setLotDrafts] = useState({})
  const [addingLot, setAddingLot] = useState(false)
  const [consigning, setConsigning] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [removing, setRemoving] = useState(null)

  // Guards every async continuation below: switching to a different auction
  // remounts this component (`key={auction.id}` in `Auctions`, below), which
  // can happen while one of these requests is still in flight.
  const mounted = useMounted()

  const venue = venues.find((v) => v.code === auction.venue) ?? null
  const isAuctionHouse = venue?.kind === 'auction_house'
  const editable = LOT_EDITABLE_STATUSES.includes(auction.status)
  const addable = LOTS_ADDABLE_STATUSES.includes(auction.status)
  const cancellable = !['settled', 'cancelled'].includes(auction.status)

  async function run(action, doneNotice) {
    setBusy(true)
    setRefusal('')
    setNotice('')
    try {
      const saved = await action()
      if (!mounted.current) return
      onChanged(saved)
      setNotice(doneNotice)
    } catch (err) {
      if (!mounted.current) return
      setRefusal(err.message)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  function draftFor(lot) {
    return (
      lotDrafts[lot.id] ?? { lot_number: lot.lot_number, reserve: lot.reserve ?? '' }
    )
  }
  function setDraft(lot, field, value) {
    setLotDrafts((current) => ({
      ...current,
      [lot.id]: { ...draftFor(lot), [field]: value },
    }))
  }

  async function saveLot(lot) {
    const draft = draftFor(lot)
    setBusy(true)
    setRefusal('')
    setNotice('')
    try {
      const saved = await api.updateAuctionLot(auction.id, lot.id, {
        lot_number: draft.lot_number.trim(),
        reserve: orNull(draft.reserve),
      })
      if (!mounted.current) return
      onChanged(saved)
      setLotDrafts((current) => {
        const next = { ...current }
        delete next[lot.id]
        return next
      })
      setNotice(`Lot ${draft.lot_number.trim()} saved.`)
    } catch (err) {
      if (!mounted.current) return
      setRefusal(err.message)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  async function removeLot(lot, locationId) {
    setBusy(true)
    setRefusal('')
    setNotice('')
    try {
      const saved = await api.removeAuctionLot(auction.id, lot.id, locationId)
      if (!mounted.current) return
      onChanged(saved)
      setRemoving(null)
      setNotice(`Lot ${lot.lot_number} removed.`)
    } catch (err) {
      if (!mounted.current) return
      setRefusal(err.message)
      setRemoving(null)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  async function cancelThisAuction(locationId) {
    setBusy(true)
    setRefusal('')
    setNotice('')
    try {
      const saved = await api.cancelAuction(
        auction.id,
        locationId ? { returned_to_location_id: locationId } : {},
      )
      if (!mounted.current) return
      onChanged(saved)
      setCancelling(false)
      setNotice('Auction cancelled.')
    } catch (err) {
      if (!mounted.current) return
      setRefusal(err.message)
      setCancelling(false)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  function lotAdded(saved) {
    setAddingLot(false)
    onChanged(saved)
    setNotice('Lot added.')
  }

  function consigned(saved) {
    setConsigning(false)
    onChanged(saved)
    setNotice('Marked consigned.')
  }

  function settled(result) {
    onChanged(result.auction)
    setNotice(`Settled: ${result.orders.length} order(s) recorded.`)
  }

  return (
    <section className="auction-detail">
      <h2>{auction.title}</h2>
      {refusal && <p className="error">{refusal}</p>}
      {notice && <p className="notice">{notice}</p>}
      <dl className="summary">
        <div>
          <dt>Platform</dt>
          <dd>{auction.venue_name}</dd>
        </div>
        <div>
          <dt>Sale number</dt>
          <dd>{auction.external_id ?? UNKNOWN}</dd>
        </div>
        <div>
          <dt>Starts</dt>
          <dd>{date(auction.starts_at) || UNKNOWN}</dd>
        </div>
        <div>
          <dt>Ends</dt>
          <dd>{date(auction.ends_at) || UNKNOWN}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{AUCTION_STATUS_LABEL[auction.status] ?? auction.status}</dd>
        </div>
      </dl>

      <div className="row">
        {auction.status === 'draft' && (
          <button
            disabled={busy}
            onClick={() =>
              run(() => api.scheduleAuction(auction.id), 'Auction scheduled.')
            }
          >
            Schedule
          </button>
        )}
        {auction.status === 'scheduled' && isAuctionHouse && (
          <button disabled={busy} onClick={() => setConsigning(true)}>
            Mark consigned...
          </button>
        )}
        {(auction.status === 'scheduled' || auction.status === 'consigned') && (
          <button
            disabled={busy}
            onClick={() => run(() => api.closeAuction(auction.id), 'Auction closed.')}
          >
            Close
          </button>
        )}
        {cancellable && (
          <button className="link" disabled={busy} onClick={() => setCancelling(true)}>
            Cancel...
          </button>
        )}
      </div>

      <h3>Lots</h3>
      {addable && (
        <div className="row">
          <button disabled={busy} onClick={() => setAddingLot(true)}>
            Add lot...
          </button>
        </div>
      )}
      {auction.lots.length === 0 && <p className="muted">No lot has been added yet.</p>}
      {auction.lots.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Lot #</th>
              <th>Item</th>
              <th>Reserve</th>
              <th>Result</th>
              <th>Hammer price</th>
              <th>Buyer</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {auction.lots.map((lot) => {
              const draft = draftFor(lot)
              const subject = subjectOf(lot.listing)
              return (
                <tr key={lot.id}>
                  <td>
                    <input
                      aria-label={`Lot number for ${subject}`}
                      value={draft.lot_number}
                      disabled={!editable}
                      onChange={(e) => setDraft(lot, 'lot_number', e.target.value)}
                    />
                  </td>
                  <td>{subject}</td>
                  <td>
                    <input
                      inputMode="decimal"
                      aria-label={`Reserve for ${subject}`}
                      value={draft.reserve}
                      disabled={!editable}
                      onChange={(e) => setDraft(lot, 'reserve', e.target.value)}
                    />
                  </td>
                  <td>{lot.result ? RESULT_LABEL[lot.result] : UNKNOWN}</td>
                  <td>{lot.hammer_price ?? UNKNOWN}</td>
                  <td>{lot.buyer ?? UNKNOWN}</td>
                  <td>
                    {editable && (
                      <>
                        <button
                          className="link"
                          disabled={busy}
                          onClick={() => saveLot(lot)}
                        >
                          Save
                        </button>
                        <button
                          className="link"
                          disabled={busy}
                          onClick={() => setRemoving(lot)}
                        >
                          Remove...
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {auction.status === 'closed' && (
        <SettlementGrid
          auction={auction}
          isAuctionHouse={isAuctionHouse}
          locations={locations}
          onSettled={settled}
        />
      )}

      {addingLot && (
        <AddLotDialog
          auction={auction}
          onSaved={lotAdded}
          onClose={() => setAddingLot(false)}
        />
      )}
      {consigning && (
        <ConsignDialog
          auction={auction}
          onSaved={consigned}
          onClose={() => setConsigning(false)}
        />
      )}
      {cancelling && (
        <ReturnLocationConfirm
          auction={auction}
          locations={locations}
          question={`Cancel ${auction.title}?`}
          confirmLabel="Cancel auction"
          busyLabel="Cancelling..."
          busy={busy}
          onConfirm={cancelThisAuction}
          onCancel={() => setCancelling(false)}
        >
          <p>
            Every lot still in this auction is withdrawn, the same way ending any offer
            is. Nothing brings a cancelled auction back.
          </p>
        </ReturnLocationConfirm>
      )}
      {removing && (
        <ReturnLocationConfirm
          auction={auction}
          locations={locations}
          question={`Remove lot ${removing.lot_number} from ${auction.title}?`}
          confirmLabel="Remove lot"
          busyLabel="Removing..."
          busy={busy}
          onConfirm={(locationId) => removeLot(removing, locationId)}
          onCancel={() => setRemoving(null)}
        >
          <p>
            {subjectOf(removing.listing)} is withdrawn from the sale. Nothing brings
            this lot number back; adding the same items again is a new lot.
          </p>
        </ReturnLocationConfirm>
      )}
    </section>
  )
}

/** The Auctions page: every sale event, and the one selected for detail. */
export default function Auctions() {
  const [auctions, setAuctions] = useState(null)
  const { venues, error: venuesError } = useSalesVenues()
  const storage = useRequest('locations', () => api.listStorageLocations())
  const locations = storage.data ?? []
  const [auctionsError, setAuctionsError] = useState('')
  const [notice, setNotice] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [creating, setCreating] = useState(false)

  // Set in place by every write below, so held here rather than in a
  // `useRequest`, which would own it.
  useEffect(() => {
    let cancelled = false
    api
      .listAuctions()
      .then((page) => !cancelled && setAuctions(page?.auctions ?? []))
      .catch((err) => !cancelled && setAuctionsError(err.message))
    return () => {
      cancelled = true
    }
  }, [])

  function replace(saved) {
    setAuctions((current) =>
      (current ?? []).map((a) => (a.id === saved.id ? saved : a)),
    )
  }

  function created(saved) {
    setCreating(false)
    setAuctions((current) => [saved, ...(current ?? [])])
    setSelectedId(saved.id)
    setNotice(`${saved.title} started as a draft.`)
  }

  // Any of the three loads failing is said at the top, and the page keeps
  // its own shell either way -- per 872e219, losing the whole page (the
  // heading, "New auction...", any open dialog) is not an acceptable answer
  // to a load failure the owner still needs to see and react to.
  const error = auctionsError || venuesError || storage.error
  const selected = (auctions ?? []).find((a) => a.id === selectedId) ?? null

  return (
    <section>
      <h1>Auctions</h1>
      <p className="muted">
        Running an auction: schedule it, mark it consigned at an auction house, close it
        once the sale has happened, and settle every lot&apos;s result at once.
      </p>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}

      <div className="row">
        <button onClick={() => setCreating(true)}>New auction...</button>
      </div>

      {auctions === null && !error && <p className="muted">Loading...</p>}

      {auctions !== null && (
        <table className="table">
          <thead>
            <tr>
              <th>Platform</th>
              <th>Auction</th>
              <th>Status</th>
              <th>Lots</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {auctions.map((a) => (
              <tr key={a.id}>
                <td>{a.venue_name}</td>
                <td>{a.title}</td>
                <td>{AUCTION_STATUS_LABEL[a.status] ?? a.status}</td>
                <td>{a.lots.length}</td>
                <td>
                  <button
                    className="link"
                    onClick={() => {
                      setNotice('')
                      setSelectedId(a.id)
                    }}
                  >
                    View
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {auctions !== null && auctions.length === 0 && (
        <p className="muted">No auction has been started yet.</p>
      )}

      {selected && (
        <AuctionDetail
          key={selected.id}
          auction={selected}
          venues={venues}
          locations={locations}
          onChanged={replace}
        />
      )}

      {creating && (
        <AuctionForm
          venues={venues}
          onSaved={created}
          onClose={() => setCreating(false)}
        />
      )}
    </section>
  )
}
