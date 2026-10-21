import { useState } from 'react'

import { useMounted } from './useMounted'

/**
 * A dialog's save: its error line, its "Saving..." flag, and the request
 * itself, guarded against the dialog closing while the request is in flight.
 *
 * `send(request)` runs `request` -- an async function that resolves to what
 * was saved -- and hands the answer to `onSaved`. A refusal, or anything
 * `request` throws, becomes `error`. Cancel (and Escape, which `ModalDialog`
 * routes to `onClose`) can unmount the dialog before the answer arrives;
 * then the dialog's own state is left alone and `onSaved` is not called, since
 * it closes a dialog that is already gone and may close the next one
 * (`useMounted`).
 *
 * Closing the dialog does not take the request back, though: the server has
 * saved it, and a row left at its old version refuses the next edit as
 * changed by someone else. `onLateSaved`, when given, is handed what was saved
 * after the dialog has gone, for the parent to put the row right without
 * touching which dialog is open.
 *
 * `setError` is for what the dialog refuses itself, before asking the
 * server: a blank title, a price that is not an amount. `mounted` is for a
 * `request` of more than one step: after the first answer it must look
 * before sending a write the user may have walked away from.
 */
export function useGuardedSave(onSaved, onLateSaved) {
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const mounted = useMounted()

  async function send(request) {
    setSaving(true)
    setError('')
    try {
      const saved = await request()
      if (!mounted.current) {
        onLateSaved?.(saved)
        return
      }
      onSaved(saved)
    } catch (err) {
      if (!mounted.current) return
      setError(err.message)
    } finally {
      if (mounted.current) setSaving(false)
    }
  }

  return { error, setError, saving, send, mounted }
}
