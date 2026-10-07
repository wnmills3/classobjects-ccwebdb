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
 * then nothing happens at all, so a save the user walked away from never
 * rewrites the parent's row behind the closed dialog (`useMounted`).
 *
 * `setError` is for what the dialog refuses itself, before asking the
 * server: a blank title, a price that is not an amount. `mounted` is for a
 * `request` of more than one step: after the first answer it must look
 * before sending a write the user may have walked away from.
 */
export function useGuardedSave(onSaved) {
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const mounted = useMounted()

  async function send(request) {
    setSaving(true)
    setError('')
    try {
      const saved = await request()
      if (!mounted.current) return
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
