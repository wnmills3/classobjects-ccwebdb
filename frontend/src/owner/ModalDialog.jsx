import { useEffect, useRef } from 'react'

/**
 * The console's one modal shell: a native `<dialog>` opened over whatever is
 * behind it.
 *
 * Modal rather than merely floating, and the reason is the same everywhere it
 * is used: a form rendered in the page's flow lands under a full table and
 * the pager, below the fold with nothing scrolling to it, so clicking a row
 * looks like it did nothing at all. Scrolling to it instead would cost the
 * reader their place in the table. Being modal also stops the table behind it
 * being re-sorted, re-filtered or clicked into a second form while one is
 * open.
 *
 * Mounted only while something is open: the parent's state says what that is,
 * and the dialog follows it rather than keeping an `open` of its own able to
 * disagree. Escape is intercepted and routed through `onClose` for that same
 * reason -- left to the browser, the element closes itself while the parent
 * still thinks it is open, and clicking the same row again changes no state
 * and so reopens nothing.
 */
export default function ModalDialog({ label, onClose, children }) {
  const ref = useRef(null)

  useEffect(() => {
    const dialog = ref.current
    dialog.showModal()
    return () => dialog.close()
  }, [])

  return (
    <dialog
      ref={ref}
      className="edit-dialog"
      aria-label={label}
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
    >
      {children}
    </dialog>
  )
}
