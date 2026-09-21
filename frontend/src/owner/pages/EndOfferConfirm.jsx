import ModalDialog from '../ModalDialog'
import { isLot, subjectOf } from './listing-labels'

/**
 * The question asked before an offer is ended, wherever End is offered.
 *
 * Ending is safe -- `offering_writes.end_offer` releases the claim and
 * touches nothing it does not have to -- but safe is not the same as
 * undoable: nothing brings the ended listing back, and on a platform the
 * listing number is gone with it. One click with no question is how the
 * wrong row gets ended.
 *
 * The Listings page offers End on both an active row and a paused one, with
 * this same dialog, and `end_offer` does something different for each:
 * ending an active listing resumes any store listing that was paused for it
 * (`paused_by_listing_id` pointing here), while ending a paused row -- which
 * is itself the store listing set aside for an offer elsewhere -- resumes
 * nothing: that row is the one being destroyed, and the offer that paused it
 * is untouched. The wording below says whichever of those is true rather
 * than a sentence written for one case and shown for both.
 *
 * The wording names the listing, the item and the platform rather than saying
 * "are you sure": a person who has two offers open needs to know which one
 * this is, and the page behind the dialog is covered while it is open.
 *
 * **The subject is `subjectOf`, not `item_code`.** A lot listing has no item
 * code, so this asked "End listing #7 for null on eBay?" and then reported
 * "null is withdrawn from eBay at 1000.00" -- which defeats the whole reason
 * the wording names anything, at the moment it matters. A lot is named the
 * way the catalogue names it: its own title, with how many coins are in it.
 *
 * A lot also ends differently, and the difference is worth a sentence.
 * `offering_writes._end` dissolves the lot along with the listing and
 * releases every membership, and "a dissolved lot never comes back": the
 * coins go back to being sold on their own, and grouping them again starts a
 * new lot with a new id. That is a bigger thing than withdrawing one item,
 * and it is exactly what a confirmation is for.
 */
export default function EndOfferConfirm({ listing, busy, onConfirm, onCancel }) {
  const isPaused = listing.status === 'paused'
  const subject = subjectOf(listing)
  const grouped = isLot(listing)
  const question = `End listing #${listing.id} for ${subject} on ${listing.venue_name}?`
  return (
    <ModalDialog label={question} onClose={onCancel}>
      <h2>{question}</h2>
      <p>
        {subject} is withdrawn from {listing.venue_name} at {listing.price}{' '}
        {listing.currency} -- not recorded as sold.{' '}
        {isPaused
          ? 'It was set aside for an offer elsewhere, and that offer is not affected by ending this one.'
          : 'Any web store listing paused for this offer goes back on sale.'}{' '}
        {grouped
          ? 'The lot is dissolved with the listing and its coins are released, each back to being sold on its own. Nothing brings the lot back; grouping the same coins again starts a new one.'
          : 'Nothing brings this listing back; offering the item again makes a new one.'}
      </p>
      <div className="row">
        <button disabled={busy} onClick={onConfirm}>
          {busy ? 'Ending...' : 'End listing'}
        </button>
        <button className="link" onClick={onCancel}>
          Keep it offered
        </button>
      </div>
    </ModalDialog>
  )
}
