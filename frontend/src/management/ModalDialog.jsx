import { useEffect, useMemo, useRef, useState } from 'react'

import { HelpBar } from './HelpBar'
import { HelpContext } from './help-context'

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
 * and so reopens nothing. One dialog may open another from inside itself (a
 * confirmation, an enlarged photograph); Escape then closes the one on top
 * and leaves the one beneath open.
 *
 * **It has a help band of its own** at its bottom. A modal covers the
 * console's band, so the forms inside -- the item editor above all -- would
 * otherwise explain their fields to a band nobody can see. The dialog
 * provides its own help context, the nearest one to every
 * `HelpScope` inside it, and renders the same `HelpBar` below a body that
 * scrolls on its own, so the explanation never scrolls out of sight.
 */
export default function ModalDialog({ label, onClose, children }) {
  const ref = useRef(null)
  const [field, setField] = useState(null)
  const help = useMemo(() => ({ field, setField }), [field])

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
        // A dialog opened from inside this one is its child in React's tree,
        // and React hands that dialog's Escape up to this handler as well:
        // only an Escape aimed at this dialog closes it.
        if (e.target !== e.currentTarget) return
        e.preventDefault()
        onClose()
      }}
    >
      <HelpContext.Provider value={help}>
        <div className="edit-dialog-body">{children}</div>
        <HelpBar />
      </HelpContext.Provider>
    </dialog>
  )
}
