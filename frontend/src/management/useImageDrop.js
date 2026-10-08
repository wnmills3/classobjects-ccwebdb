import { useEffect, useRef, useState } from 'react'

/**
 * True for a `File` or a `DataTransferItem`/clipboard item alike -- both
 * carry a MIME `type`, which is all this ever needs to tell a photograph
 * from anything else dropped or pasted.
 */
export function isImageFile(file) {
  return typeof file?.type === 'string' && file.type.startsWith('image/')
}

/**
 * The drop target's `dragover`. Without this the browser's own default takes
 * over: dropping an image on the page opens it in the tab instead of
 * reaching the target.
 */
function onDragOver(e) {
  e.preventDefault()
}

/**
 * A place photographs are added by choosing, dropping or pasting them: the
 * drop target's handlers, whether a drag is over it, and why a drop or a
 * paste was refused.
 *
 * All three ways in funnel into `onImages(files)`, called with photographs
 * only. A file that is not one refuses the whole drop or paste with a
 * message naming it (`pickError`), rather than silently skipping it; a
 * choice from the file box never reaches that branch, since its
 * `accept="image/*"` already keeps a non-image out of `e.target.files`.
 *
 * `disabled` turns the target off: nothing is taken, and a paste is left
 * alone to land wherever it was aimed.
 *
 * Returns `dragActive`, `pickError`, `target` (the props to spread on the
 * drop target) and `onFileInput` (the file box's `onChange`).
 */
export function useImageDrop({ onImages, disabled = false }) {
  const [dragActive, setDragActive] = useState(false)
  const [pickError, setPickError] = useState('')
  // A nesting count, not a flag: entering the drop target's own children
  // (the label, the input, the help text) fires a dragenter on the child and
  // a dragleave on the target itself, since only the topmost element under
  // the pointer counts as "current" -- a flag would blink the hint off for
  // every child crossed. Active while the count is above zero; a drop or a
  // cancelled drag (see the window listener below) resets it to zero rather
  // than trusting the count to unwind on its own.
  const dragDepth = useRef(0)

  function addFiles(fileList) {
    const files = Array.from(fileList ?? []).filter(Boolean)
    if (files.length === 0) return
    const notImages = files.filter((file) => !isImageFile(file))
    if (notImages.length > 0) {
      const names = notImages.map((file) => file.name).join(', ')
      setPickError(
        `${names} ${notImages.length > 1 ? 'are not images' : 'is not an image'} -- ` +
          'only a photograph can be added here.',
      )
      return
    }
    setPickError('')
    onImages(files)
  }

  function onFileInput(e) {
    addFiles(e.target.files)
    // Cleared at once: the browser would otherwise show "N files chosen" for
    // only the files picked just now, misstating a batch that also holds an
    // earlier drop or paste -- and leaving a chosen file in place stops it
    // being chosen again.
    e.target.value = ''
  }

  function onDragEnter(e) {
    e.preventDefault()
    if (disabled) return
    dragDepth.current += 1
    setDragActive(true)
  }

  function onDragLeave(e) {
    e.preventDefault()
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragActive(false)
  }

  function onDrop(e) {
    e.preventDefault()
    dragDepth.current = 0
    setDragActive(false)
    if (disabled) return
    addFiles(e.dataTransfer?.files)
  }

  function onPaste(e) {
    if (disabled) return
    const items = e.clipboardData?.items
    if (!items) return
    const imageFiles = Array.from(items)
      .filter((item) => item.kind === 'file' && isImageFile(item))
      .map((item) => item.getAsFile())
      .filter(Boolean)
    // Nothing to add: leave the event alone so plain text still pastes
    // normally wherever this was actually aimed.
    if (imageFiles.length === 0) return
    e.preventDefault()
    addFiles(imageFiles)
  }

  // A drag cancelled outright -- Escape, or a drop outside the browser
  // window -- can leave the target's own dragleave never firing, since the
  // pointer never crosses its boundary again to trigger one. Both `dragend`
  // (fired on the source once the operation ends) and `drop` (fired wherever
  // it actually lands) are caught at the window regardless of where that
  // is, as a backstop for the per-target handlers above.
  useEffect(() => {
    function reset() {
      dragDepth.current = 0
      setDragActive(false)
    }
    window.addEventListener('dragend', reset)
    window.addEventListener('drop', reset)
    return () => {
      window.removeEventListener('dragend', reset)
      window.removeEventListener('drop', reset)
    }
  }, [])

  return {
    dragActive,
    pickError,
    target: { onDragEnter, onDragOver, onDragLeave, onDrop, onPaste },
    onFileInput,
  }
}
