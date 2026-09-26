import ModalDialog from './ModalDialog'

/**
 * The console's question before something that cannot be undone.
 *
 * `question` is both the dialog's accessible name and its heading, and names
 * the thing about to change rather than asking "are you sure": the page
 * behind is covered while the dialog is open. `children` says what the
 * action does. The confirm button reads `confirmLabel`, or `busyLabel` while
 * the request is out, and is disabled then and whenever `disabled` says the
 * answer is not complete yet; the other button, `cancelLabel`, backs out.
 */
export default function ConfirmDialog({
  question,
  confirmLabel,
  busyLabel,
  cancelLabel,
  busy,
  disabled = false,
  onConfirm,
  onCancel,
  children,
}) {
  return (
    <ModalDialog label={question} onClose={onCancel}>
      <h2>{question}</h2>
      {children}
      <div className="row">
        <button disabled={busy || disabled} onClick={onConfirm}>
          {busy ? busyLabel : confirmLabel}
        </button>
        <button className="link" onClick={onCancel}>
          {cancelLabel}
        </button>
      </div>
    </ModalDialog>
  )
}
