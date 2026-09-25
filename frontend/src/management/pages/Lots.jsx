import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { AccessLabel } from '../AccessLabel'
import { api } from '../api'
import ModalDialog from '../ModalDialog'
import OfferDialog from './inventory/OfferDialog'
import { accel, useSaveShortcut } from '../shortcuts'
import { UNKNOWN } from './listing-labels'

/**
 * The Lots page: groups of coins being put together to sell as one thing.
 *
 * A lot is assembled here, offered from here, and read here long after it
 * sold -- a sold lot is the record of which coins went out together, which is
 * why the history below never hides one.
 *
 * **Coins go in from the inventory pages, not from here.** Adding a coin
 * means finding it, and finding it is what the Coins and Currency pages
 * already do, with their filters, their paging and a selection that survives
 * both; `inventory/BulkEditBar.jsx` turns that selection into a lot or adds
 * it to one. A coin picker inside this page would be a second, worse
 * inventory search, kept in step with the first by hand. Taking a coin *out*
 * is here, because that is a decision made looking at the group.
 *
 * Money is shown exactly as it arrives: a decimal string, never parsed into a
 * JavaScript number, because a float cannot hold cents exactly. `cost_basis`
 * and `value` are the API's own running totals over the members
 * (`SalesLotOut`), so nothing is summed here either.
 *
 * `value` is a **floor**, not an estimate: an unvalued coin contributes
 * nothing to it, so `unvalued_count` is shown beside it whenever there is
 * one. Reading 1400.00 as the worth of a group when two of its five coins
 * have never been valued is the mistake that figure invites.
 *
 * A failed load shows the error and leaves the page standing, per 872e219 --
 * an operator who has lost the whole page cannot react to what it says.
 */

/** Lot statuses, in the order a lot moves through them. */
const STATUS_LABEL = {
  assembling: 'Assembling',
  offered: 'Offered',
  sold: 'Sold',
  dissolved: 'Dissolved',
}

/**
 * Alt+letter for the fields of the lot form, as every other console edit
 * window has (`docs/system-administration.md`). No letter is D, E or F:
 * Chrome and Edge keep those for the address bar and menus on Windows. Save
 * is V, the same letter it is in the item editor and the platform form.
 */
const KEYS = {
  title: 't',
  description: 'i',
  save: 'v',
}

/**
 * The window that starts a lot, and the window that renames one.
 *
 * One component for both because the fields are the same two and the only
 * difference is which call they are sent to. A lot starts empty --
 * `SalesLotIn` takes a title and a description and nothing else, and
 * membership is a PATCH -- so there is nothing else this form could ask for.
 */
function LotForm({ lot, onSaved, onClose }) {
  const [form, setForm] = useState({
    title: lot?.title ?? '',
    description: lot?.description ?? '',
  })
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  // Guards save()'s continuation once the request settles: Cancel (and
  // Escape, which ModalDialog routes to onClose) can unmount this form while
  // a save is still in flight.
  //
  // The setup ARMS it; only the cleanup disarms it. The console runs in
  // StrictMode (`management/main.jsx`), where React runs every effect setup,
  // cleanup, setup on mount: a ref only initialised at `useRef(true)` would
  // be left false by that first cleanup for the rest of the dialog's life,
  // and a save that succeeded would never close it.
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  // `save` is a function declaration below, hoisted for the whole component
  // scope. Disabled while a save is in flight, so holding Ctrl+S cannot start
  // a second lot behind the first.
  useSaveShortcut(save, !saving)

  async function save() {
    const title = form.title.trim()
    if (title === '') {
      // Said here rather than left to the API, whose refusal for a blank
      // title is a schema complaint about `min_length` -- true, and no help
      // to someone who cleared the field.
      setError('A lot needs a title: it is what the offer and the shop call it.')
      return
    }
    setSaving(true)
    setError('')
    try {
      const saved = lot
        ? await api.updateLot(lot.id, {
            title,
            // Sent as typed, including empty. `description` is NOT NULL on
            // the row; an empty string is how the wording is cleared.
            description: form.description,
            // The version the form loaded, as a number: `SalesLotUpdate.version`
            // is an `int` and Pydantic v2 does not coerce "3".
            version: lot.version,
          })
        : await api.createLot({ title, description: form.description })
      if (!mounted.current) return
      onSaved(saved)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  const label = lot ? `Edit the lot ${lot.title}` : 'Start a new lot'

  return (
    <ModalDialog label={label} onClose={onClose}>
      <h2>{label}</h2>
      {error && <p className="error">{error}</p>}
      <div className="filter-grid">
        <label>
          <AccessLabel text="Title" accessKey={KEYS.title} />
          <input value={form.title} onChange={set('title')} {...accel(KEYS.title)} />
        </label>
        <label>
          <AccessLabel text="Description" accessKey={KEYS.description} />
          <textarea
            rows={3}
            value={form.description}
            onChange={set('description')}
            {...accel(KEYS.description)}
          />
        </label>
      </div>
      <div className="row">
        <button disabled={saving} onClick={save} {...accel(KEYS.save)}>
          <AccessLabel text={saving ? 'Saving...' : 'Save'} accessKey={KEYS.save} />
        </button>
        <button className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </ModalDialog>
  )
}

/**
 * The question asked before a lot is discarded.
 *
 * Only a lot that was never offered can be discarded at all -- `delete_lot`
 * refuses the rest -- so nothing here can destroy a record of what was sold.
 * It can still destroy an afternoon of assembling, and the wording names the
 * lot and how many coins are in it so a person with two lots open knows which
 * one this is.
 */
function DiscardConfirm({ lot, busy, onConfirm, onCancel }) {
  const question = `Discard the lot ${lot.title}?`
  return (
    <ModalDialog label={question} onClose={onCancel}>
      <h2>{question}</h2>
      <p>
        The group goes; the {lot.members.length} coin(s) in it do not. Each one stays in
        inventory exactly as it is and can be sold on its own or grouped again.
      </p>
      <div className="row">
        <button disabled={busy} onClick={onConfirm}>
          {busy ? 'Discarding...' : 'Discard the lot'}
        </button>
        <button className="link" onClick={onCancel}>
          Keep assembling
        </button>
      </div>
    </ModalDialog>
  )
}

//: At most this many open lots are read at once -- far beyond any real
//: afternoon's assembling, and the API's own ceiling.
const OPEN_LIMIT = 500

/** The Lots page: what is being assembled, and what has already gone out. */
export default function Lots() {
  const [lots, setLots] = useState(null)
  // Every lot that is not assembling, and how many of those the page shows:
  // the history is paged (newest first), the lots still being assembled
  // never are -- an old open lot must not fall off the page.
  const [pastPage, setPastPage] = useState({ shown: 0, total: 0 })
  // A load failed. Kept apart from `refusal` below, which is a *write* the
  // API turned down: returning the error instead of the page unmounted
  // everything the operator needed in order to react to it (872e219).
  const [error, setError] = useState('')
  const [refusal, setRefusal] = useState('')
  // A one-line confirmation of what was just done, cleared by the next action
  // so it cannot outlive the lot it described.
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  // Bumped to ask for the list again after a write that can change lots this
  // page did not touch -- offering one, or starting another from it.
  const [reloads, setReloads] = useState(0)
  // `{ lot }` while the wording window is open; `{ lot: null }` for a new one.
  const [editing, setEditing] = useState(null)
  const [offering, setOffering] = useState(null)
  const [discarding, setDiscarding] = useState(null)

  useEffect(() => {
    let cancelled = false
    // Two reads: every open lot, whatever its age, and the newest page of
    // everything for the history below it. Merged, the open ones from the
    // first read -- the second may have cut some of them off.
    Promise.all([
      api.listLots({ status: 'assembling', limit: OPEN_LIMIT }),
      api.listLots(),
    ])
      .then(([open, page]) => {
        if (cancelled) return
        const past = (page?.lots ?? []).filter((lot) => lot.status !== 'assembling')
        const openCount = (page?.lots ?? []).length - past.length
        const stillOpen = (open?.lots ?? []).filter(
          (lot) => lot.status === 'assembling',
        )
        setLots([...stillOpen, ...past])
        setPastPage({
          shown: past.length,
          total: Math.max(0, (page?.total ?? 0) - (open?.total ?? openCount)),
        })
        setError('')
      })
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [reloads])

  /** Put one lot the API just returned back in the list, in place. */
  function replace(saved) {
    setLots((current) => (current ?? []).map((l) => (l.id === saved.id ? saved : l)))
  }

  function start() {
    setRefusal('')
    setNotice('')
    setEditing({ lot: null })
  }

  function saved(lot, wasNew) {
    setEditing(null)
    setRefusal('')
    if (wasNew) {
      setNotice(`${lot.title} is assembling. Add coins to it from the inventory pages.`)
      setReloads((n) => n + 1)
      return
    }
    replace(lot)
    setNotice('')
  }

  async function removeMember(lot, member) {
    setBusy(true)
    setRefusal('')
    setNotice('')
    try {
      // The whole change in one body, with the version the page loaded: a
      // membership move alone would leave `sales_lot.version` where it was,
      // which is why `update_sales_lot` touches the row itself.
      const after = await api.updateLot(lot.id, {
        remove_item_ids: [member.inventory_item_id],
        version: lot.version,
      })
      replace(after)
      setNotice(`${member.item_code} is out of ${lot.title}.`)
    } catch (err) {
      setRefusal(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function discard(lot) {
    setBusy(true)
    setRefusal('')
    setNotice('')
    try {
      await api.deleteLot(lot.id)
      setDiscarding(null)
      setNotice(`${lot.title} is discarded. Its coins are untouched.`)
      setReloads((n) => n + 1)
    } catch (err) {
      setRefusal(err.message)
      // The question is answered either way: the refusal belongs on the page
      // behind it, where the lot it is about still is.
      setDiscarding(null)
    } finally {
      setBusy(false)
    }
  }

  /**
   * Start a new lot from a dissolved one: the same wording, the same coins.
   *
   * Two calls rather than one, because that is what the API is: a lot starts
   * empty and membership is a PATCH. A dissolved lot is never revived --
   * `offering_writes._end` says so -- so this is genuinely a new lot that
   * happens to describe the same group.
   *
   * The second call can be refused on its own: a coin of a dissolved lot may
   * already be on offer by itself, and a PATCH is all or nothing, so none of
   * them goes in. The new lot then exists and is empty, which is exactly what
   * the message says rather than leaving the operator to find out.
   */
  async function reoffer(lot) {
    setBusy(true)
    setRefusal('')
    setNotice('')
    let created = null
    try {
      created = await api.createLot({ title: lot.title, description: lot.description })
      const ids = lot.members.map((member) => member.inventory_item_id)
      if (ids.length > 0) {
        await api.updateLot(created.id, {
          add_item_ids: ids,
          version: created.version,
        })
      }
      setNotice(`${lot.title} is assembling again, with ${ids.length} coin(s).`)
    } catch (err) {
      setRefusal(
        created === null
          ? err.message
          : `${lot.title} was started again but is empty: ${err.message}`,
      )
    } finally {
      setBusy(false)
      setReloads((n) => n + 1)
    }
  }

  function offered(listings) {
    setOffering(null)
    setRefusal('')
    setNotice(`Offered as listing #${listings.map((l) => l.id).join(', #')}.`)
    setReloads((n) => n + 1)
  }

  const assembling = (lots ?? []).filter((lot) => lot.status === 'assembling')
  const history = (lots ?? []).filter((lot) => lot.status !== 'assembling')

  return (
    <section>
      <h1>Sales lots</h1>
      <p className="muted">
        A lot is a group of coins sold as one thing. Coins go in from the{' '}
        <Link to="/inventory/coins">Coins</Link> and{' '}
        <Link to="/inventory/currency">Currency</Link> pages -- select them there and
        choose &quot;Group into lot...&quot;. They come out here.
      </p>
      {error && <p className="error">{error}</p>}
      {refusal && <p className="error">{refusal}</p>}
      {notice && <p className="notice">{notice}</p>}

      <div className="row">
        <button onClick={start}>New lot...</button>
      </div>

      {lots === null && !error && <p className="muted">Loading...</p>}

      <h2>Assembling</h2>
      {lots !== null && assembling.length === 0 && (
        <p className="muted">No lot is being assembled.</p>
      )}
      {assembling.map((lot) => (
        <article key={lot.id} className="lot">
          <h3>{lot.title}</h3>
          {lot.description && <p>{lot.description}</p>}
          {/* `table`, the console's own class. `specs` is the SHOP's
              stylesheet (`store/styles.css`), which the console entry never
              loads -- the bundle split is the point of that file -- so this
              summary rendered unstyled. */}
          <table className="table">
            <tbody>
              <tr>
                <th>Coins</th>
                <td>{lot.members.length}</td>
              </tr>
              <tr>
                <th>Cost basis</th>
                <td>{lot.cost_basis}</td>
              </tr>
              <tr>
                <th>Value</th>
                <td>{lot.value}</td>
              </tr>
              {/* Only when there is one. A floor is not an estimate, and
                  which it is depends entirely on this number. */}
              {lot.unvalued_count > 0 && (
                <tr>
                  <th>Not yet valued</th>
                  <td>{lot.unvalued_count} of the coins above</td>
                </tr>
              )}
            </tbody>
          </table>
          {lot.members.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Title</th>
                  <th>Cost</th>
                  <th>Value</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {lot.members.map((member) => (
                  <tr key={member.inventory_item_id}>
                    <td className="mono">{member.item_code}</td>
                    <td>{member.title}</td>
                    <td>{member.cost_basis}</td>
                    {/* Blank rather than zero: "worth nothing" and "nobody
                        has valued it" are different facts. */}
                    <td>{member.value ?? UNKNOWN}</td>
                    <td>
                      <button
                        className="link"
                        disabled={busy}
                        onClick={() => removeMember(lot, member)}
                      >
                        Remove {member.item_code}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="row">
            {/* An empty lot cannot be offered: `offering_writes._lot_members`
                raises `EmptyLot` and the API answers 422. Said by the button
                rather than by a refusal after the dialog was filled in. */}
            <button
              disabled={busy || lot.members.length === 0}
              onClick={() => {
                setRefusal('')
                setNotice('')
                setOffering(lot)
              }}
            >
              Offer for sale...
            </button>
            <button
              className="link"
              onClick={() => {
                setRefusal('')
                setNotice('')
                setEditing({ lot })
              }}
            >
              Edit wording...
            </button>
            <button
              className="link"
              disabled={busy}
              onClick={() => {
                setRefusal('')
                setNotice('')
                setDiscarding(lot)
              }}
            >
              Discard...
            </button>
          </div>
        </article>
      ))}

      <h2>Offered, sold and dissolved</h2>
      {lots !== null && pastPage.total > pastPage.shown && (
        <p className="muted">
          Showing the newest {pastPage.shown} of {pastPage.total} lots.
        </p>
      )}
      {lots !== null && history.length === 0 && (
        <p className="muted">No lot has been offered yet.</p>
      )}
      {history.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Lot</th>
              <th>Status</th>
              <th>Coins</th>
              <th>Cost basis</th>
              <th>Value</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {history.map((lot) => (
              <tr key={lot.id}>
                <td>{lot.title}</td>
                <td>{STATUS_LABEL[lot.status] ?? lot.status}</td>
                {/* Every membership the lot ever held, which is what the API
                    sends for a lot past `assembling`: ending one releases its
                    memberships rather than deleting them, precisely so this
                    stays answerable. */}
                <td>{lot.members.length}</td>
                <td>{lot.cost_basis}</td>
                <td>{lot.value}</td>
                <td>
                  {/* A dissolved lot is the only one worth offering again:
                      its coins are free, and nothing brings the lot itself
                      back. A sold one's coins are gone. */}
                  {lot.status === 'dissolved' && (
                    <button
                      className="link"
                      disabled={busy}
                      onClick={() => reoffer(lot)}
                    >
                      Re-offer as a lot
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing !== null && (
        <LotForm
          lot={editing.lot}
          onSaved={(lot) => saved(lot, editing.lot === null)}
          onClose={() => setEditing(null)}
        />
      )}
      {offering !== null && (
        <OfferDialog
          lot={offering}
          onOffered={offered}
          onClose={() => setOffering(null)}
        />
      )}
      {discarding !== null && (
        <DiscardConfirm
          lot={discarding}
          busy={busy}
          onConfirm={() => discard(discarding)}
          onCancel={() => setDiscarding(null)}
        />
      )}
    </section>
  )
}
