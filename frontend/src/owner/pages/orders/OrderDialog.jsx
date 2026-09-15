import { useEffect, useRef } from 'react'

/**
 * A modal dialog for the Orders page. Mounted only while open; the parent's
 * state says what is open. Escape closes through `onClose`, so the parent's
 * state never disagrees with the element.
 */
export default function OrderDialog({ label, onClose, children }) {
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
