import { useEffect, useState } from 'react'

import { api } from '../../api'
import EndOfferConfirm from '../EndOfferConfirm'
import { FORMATS, ON_OFFER, STATUSES, UNKNOWN, labelFor } from '../listing-labels'
import OfferDialog from './OfferDialog'
import { date } from '../../../shared/format'

/**
 * Where this item is offered, where it has been, and the two things that may
 * be done about it.
 *
 * Beside the sale history and for the same reason: a sale is what happened,
 * an offer is what is being asked. Both are read from the server rather than
 * kept in the editor's draft -- this panel never sets a listing's status.
 * `app/offering_writes.py` is the only writer of status and of the claim that
 * keeps an item offered in one place at a time, so ending an offer goes
 * through its own endpoint, which also resumes the store listing that was
 * paused for it.
 *
 * Money is shown exactly as it arrives -- a decimal string with the currency
 * the API sent -- and never parsed into a number.
 */
export default function OffersPanel({ item }) {
  const [listings, setListings] = useState(null)
  const [error, setError] = useState('')
  const [ending, setEnding] = useState(null)
  const [busy, setBusy] = useState(false)
  const [offering, setOffering] = useState(false)
  // Bumped to read the offers again after a write: ending one offer can
  // resume another, and both rows are on this panel.
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let cancelled = false
    api
      // Ended ones included: where this item has been offered before, and for
      // how much, is half of what this panel is for.
      .listListings({ item_id: item.id, status: 'all' })
      .then((rows) => !cancelled && setListings(rows))
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [item.id, reloads])

  async function end() {
    setBusy(true)
    setError('')
    try {
      await api.endListing(ending.id)
      setEnding(null)
      setReloads((n) => n + 1)
    } catch (err) {
      setError(err.message)
      setEnding(null)
    } finally {
      setBusy(false)
    }
  }

  if (listings === null) {
    return (
      <div className="offers-panel">
        <h3>Offers</h3>
        {error ? <p className="error">{error}</p> : <p className="muted">Loading...</p>}
      </div>
    )
  }

  // Nothing may be offered while a listing holds the item -- the writer
  // refuses a second offer -- so the button that would start one is shown
  // only when nothing holds it.
  const held = listings.some((l) => ON_OFFER.includes(l.status))

  return (
    <div className="offers-panel">
      <h3>Offers</h3>
      {error && <p className="error">{error}</p>}
      {listings.length === 0 && <p className="muted">Not offered anywhere yet.</p>}
      {listings.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Price</th>
              <th>Format</th>
              <th>Status</th>
              <th>Listed</th>
              <th>Ended</th>
              <th>Link</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {listings.map((l) => (
              <tr key={l.id} className={l.status === 'active' ? '' : 'muted'}>
                <td>{l.venue_name}</td>
                <td>
                  {l.price} {l.currency}
                </td>
                <td>{labelFor(FORMATS, l.format)}</td>
                <td>{labelFor(STATUSES, l.status)}</td>
                <td>{date(l.listed_at)}</td>
                <td>{l.ended_at ? date(l.ended_at) : UNKNOWN}</td>
                <td>
                  {l.external_url && (
                    <a href={l.external_url} target="_blank" rel="noreferrer">
                      {l.external_id ?? 'Listing'}
                    </a>
                  )}
                </td>
                <td>
                  {/* Only the active one. An ended offer is history, and a
                      paused row is a store listing set aside for an offer
                      elsewhere -- ending that one from here would destroy the
                      listing the running offer is meant to come back to. */}
                  {l.status === 'active' && (
                    <button className="link" onClick={() => setEnding(l)}>
                      End
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {!held && <button onClick={() => setOffering(true)}>Offer for sale...</button>}
      {ending !== null && (
        <EndOfferConfirm
          listing={ending}
          busy={busy}
          onConfirm={end}
          onCancel={() => setEnding(null)}
        />
      )}
      {offering && (
        <OfferDialog
          items={[item]}
          onOffered={() => {
            setOffering(false)
            setReloads((n) => n + 1)
          }}
          onClose={() => setOffering(false)}
        />
      )}
    </div>
  )
}
