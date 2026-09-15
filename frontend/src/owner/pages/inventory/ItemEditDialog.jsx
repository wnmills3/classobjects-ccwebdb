import { useEffect, useRef } from 'react'

import ItemEditForm from './ItemEditForm'

/**
 * The edit form, opened over the results rather than below them.
 *
 * Rendered in the page's flow, the form landed after a full page of rows and
 * the pager -- below the fold, with nothing scrolling to it -- so a click on
 * an item code looked like it did nothing at all. Scrolling down to it would
 * have fixed that and lost the reader's place in the table instead.
 *
 * Modal, not merely floating: while an item is open the table behind it
 * cannot be re-sorted, re-filtered or clicked into a second form, which is
 * the same race ReviewPane closes off by hiding those controls.
 *
 * Mounted only while an item is being edited; the parent's state says what
 * is open, and the dialog follows it rather than keeping an `open` of its
 * own able to disagree.
 */
export default function ItemEditDialog({ itemId, onSaved, onClose }) {
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
      aria-label="Edit item"
      // Escape. Left to the browser, the dialog closes itself while `itemId`
      // is still set, and clicking the same code again changes no state and
      // so reopens nothing.
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
    >
      <ItemEditForm itemId={itemId} onSaved={onSaved} onClose={onClose} />
    </dialog>
  )
}
