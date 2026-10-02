import { useState } from 'react'

import ModalDialog from './ModalDialog'

/**
 * A thumbnail that opens its large rendition in a dialog when clicked.
 *
 * `src` is the small picture shown in place; `largeSrc` is what the dialog
 * shows -- the API's `image_url`, the web rendition, never the thumbnail
 * scaled up. The dialog closes on its Cancel button or on Escape, which
 * `ModalDialog` routes through the same `onClose`. Opened from inside another
 * dialog (the item editor), it sits on top of it and Escape closes only this
 * one.
 *
 * The picture is a real button, so it is reachable by keyboard; its name says
 * what clicking does, and the image keeps `alt` as its own. With no
 * `largeSrc` there is nothing to enlarge and it is a plain image.
 */
export default function EnlargeableImage({ src, largeSrc, alt }) {
  const [open, setOpen] = useState(false)
  if (!largeSrc) return <img src={src} alt={alt} />
  return (
    <>
      <button
        type="button"
        className="image-enlarge"
        aria-label={`Enlarge ${alt}`}
        title="Click to enlarge"
        onClick={() => setOpen(true)}
      >
        <img src={src} alt={alt} />
      </button>
      {open && (
        <ModalDialog label={alt} onClose={() => setOpen(false)}>
          <div className="image-dialog">
            <img src={largeSrc} alt={alt} />
            <div className="row">
              <button type="button" onClick={() => setOpen(false)}>
                Cancel
              </button>
            </div>
          </div>
        </ModalDialog>
      )}
    </>
  )
}
