import { useEffect, useState } from 'react'

import { api } from '../../api'
import EndOfferConfirm from '../EndOfferConfirm'
import {
  FORMATS,
  ON_OFFER,
  STATUSES,
  UNKNOWN,
  isLot,
  labelFor,
  subjectOf,
} from '../listing-labels'
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
 *
 * `onChanged` is called after an offer is made or ended: both change what the
 * server says about the item -- `sale_state`, which decides whether the editor
 * asks for a change to be acknowledged -- and the editor above would otherwise
 * carry on with what it read before.
 */
export default function OffersPanel({ item, onChanged }) {
  const [listings, setListings] = useState(null)
  // The codes of the platforms that are the business's own store. `null` until
  // they are known -- see `heldElsewhere` below, which reads that as "not
  // known to be held anywhere else" rather than as "nowhere".
  const [ownStore, setOwnStore] = useState(null)
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

  // Which platforms are the business's own store. `ListingOut` says which
  // platform a listing is on but not whether that platform is the shop, and
  // the difference decides whether this item may be offered at all -- so it
  // is read from the platforms themselves rather than guessed from a code.
  useEffect(() => {
    let cancelled = false
    api
      .listSalesVenues()
      .then((venues) => {
        if (cancelled) return
        setOwnStore(new Set(venues.filter((v) => v.is_own_store).map((v) => v.code)))
      })
      // Left unknown rather than assumed empty, and said out loud: a platform
      // list that did not arrive must not quietly decide that this item can
      // be offered nowhere.
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [])

  async function end() {
    setBusy(true)
    setError('')
    try {
      await api.endListing(ending.id)
      setEnding(null)
      setReloads((n) => n + 1)
      onChanged?.()
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

  // An item held on ANOTHER platform is the one case the writer refuses
  // outright -- "is active on eBay, listing #14: end it first" -- and the only
  // one worth hiding the button for.
  //
  // The shop is not such a case, and this is the flow the spec designs
  // (`docs/specs/selling-design.md`): offering an item that is active in the
  // web store PAUSES that store listing with `paused_by_listing_id` set, and
  // ending the new offer brings it back. Hiding the button here would make
  // moving an item from the shop to eBay an End followed by an Offer, which
  // destroys the very store listing the pause exists to preserve. Offering a
  // shop item in the shop again is refused too, but per item and by the
  // server -- `OfferDialog` shows that refusal in place, with the prices
  // already typed still there.
  //
  // Until the platforms are known, nothing is known to hold it elsewhere: the
  // button shows, and a platform that cannot take the item refuses it by name.
  const heldElsewhere =
    ownStore !== null &&
    listings.some((l) => ON_OFFER.includes(l.status) && !ownStore.has(l.venue))

  // A coin inside an offered lot is the other case the writer refuses
  // outright -- "is in lot #3, which is offered: end or dissolve the lot
  // before offering it on its own" (`offering_writes._refuse_grouped`).
  //
  // Unlike the rule above this one does not depend on the platform, and the
  // shop is NOT an exception to it. The shop exception exists because
  // offering an item that is active in the web store pauses that store
  // listing and ending the offer brings it back. A lot listing cannot take
  // part in that: pausing it would pause every member's claim, and settling
  // the new sale would dissolve the lot and leave every other coin listed
  // with nothing offering it. So the lot is refused on every platform, the
  // shop included, and the button must not appear for any of them.
  //
  // Read off the listing rather than off the item: the panel already has the
  // rows, and `sales_lot_id` is the same field `ck_listing_item_xor_lot`
  // makes exclusive with `inventory_item_id`. It also needs no `ownStore`,
  // so it is right from the first render rather than once platforms arrive.
  const heldInLot = listings.some((l) => ON_OFFER.includes(l.status) && isLot(l))

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
              <th>Item or lot</th>
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
                {/* What this offer is *of*. Every row on this panel used to
                    be this one coin, so there was nothing to say; a lot
                    listing offers a group, and without this the group's
                    price reads as the coin's -- "1000.00" against a coin
                    the owner paid 500 for. `subjectOf` names a lot by its
                    title and size, the same way the Listings page, the end
                    confirmation and the record-sale dialog all name one. */}
                <td>{subjectOf(l)}</td>
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
      {!heldElsewhere && !heldInLot && (
        <button onClick={() => setOffering(true)}>Offer for sale...</button>
      )}
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
            onChanged?.()
          }}
          onClose={() => setOffering(false)}
        />
      )}
    </div>
  )
}
