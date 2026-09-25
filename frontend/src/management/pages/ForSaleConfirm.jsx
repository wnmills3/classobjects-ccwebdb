import ModalDialog from '../ModalDialog'

/**
 * The question asked before a receipt changes a coin someone is buying.
 *
 * Shown from the server's refusal rather than from a loaded item: receiving
 * works on a batch and holds no `sale_state` of its own, so the 409 is what
 * says which items are affected. The message is passed through verbatim --
 * `app.sale_state.refusal` already names each item and each listing or order,
 * and rewording it here would mean maintaining the same sentence twice.
 *
 * The consequence is spelled out because it is not obvious: an outcome that
 * is not "received" says the coin will not be delivered, so the backend also
 * ends the listing offering it. One click with no question is how a live
 * offer disappears by accident.
 */
export default function ForSaleConfirm({ detail, outcome, busy, onConfirm, onCancel }) {
  const question = `Record "${outcome}" for an item that is for sale?`
  return (
    <ModalDialog label={question} onClose={onCancel}>
      <h2>{question}</h2>
      <p>{detail}</p>
      <p>
        Recording this says the coin will not be delivered, so it is also{' '}
        <strong>withdrawn from sale</strong>: any listing offering it is ended. Nothing
        brings that listing back; offering the item again makes a new one.
      </p>
      <div className="row">
        <button disabled={busy} onClick={onConfirm}>
          {busy ? 'Recording...' : 'Record it anyway'}
        </button>
        <button className="link" onClick={onCancel}>
          Leave it on sale
        </button>
      </div>
    </ModalDialog>
  )
}
