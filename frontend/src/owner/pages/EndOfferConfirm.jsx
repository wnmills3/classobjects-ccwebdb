import ModalDialog from '../ModalDialog'

/**
 * The question asked before an offer is ended, wherever End is offered.
 *
 * Ending is safe -- `offering_writes.end_offer` releases the claim, resumes
 * whatever store listing was paused for this offer, and touches nothing else
 * -- but safe is not the same as undoable: nothing brings the ended listing
 * back, and on a platform the listing number is gone with it. One click with
 * no question is how the wrong row gets ended.
 *
 * The wording names the listing, the item and the platform rather than saying
 * "are you sure": a person who has two offers open needs to know which one
 * this is, and the page behind the dialog is covered while it is open.
 */
export default function EndOfferConfirm({ listing, busy, onConfirm, onCancel }) {
  const question = `End listing #${listing.id} for ${listing.item_code} on ${listing.venue_name}?`
  return (
    <ModalDialog label={question} onClose={onCancel}>
      <h2>{question}</h2>
      <p>
        {listing.item_code} is withdrawn from {listing.venue_name} at {listing.price}{' '}
        {listing.currency} -- not recorded as sold. Any web store listing paused for
        this offer goes back on sale. Nothing brings this listing back; offering the
        item again makes a new one.
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
