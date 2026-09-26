import { useEffect, useRef } from 'react'

/**
 * A ref that is true while the component is mounted.
 *
 * The console's forms read it after an awaited request: Cancel (and Escape,
 * which `ModalDialog` routes to `onClose`) can unmount a dialog while its
 * request is still in flight, and what the answer would do -- close the
 * dialog, tell the parent, turn "Saving..." back into "Save" -- must not
 * happen behind a window the user has already left.
 *
 * The effect's setup ARMS it; only the cleanup disarms it. The console runs
 * in StrictMode (`management/main.jsx`), where React runs every effect setup,
 * cleanup, setup on mount: a ref only initialized at `useRef(true)` would be
 * left false by that first cleanup for the rest of the component's life, and
 * a save that succeeded would never close its dialog.
 */
export function useMounted() {
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  return mounted
}
