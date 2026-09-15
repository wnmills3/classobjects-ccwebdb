import { useEffect, useRef } from 'react'

/**
 * Keyboard accelerators for the console's edit windows.
 *
 * Fields use the browser's own `accesskey` (Alt+letter in Chrome and Edge),
 * with the letter underlined in the label so the shortcut is discoverable and
 * `aria-keyshortcuts` announcing it. Letters avoid D, E and F, which those
 * browsers keep for the address bar and menus on Windows.
 *
 * The label component itself, `AccessLabel`, lives in `./AccessLabel.jsx` --
 * it renders JSX, so it stays out of this plain module.
 */

/** The attributes an accelerated control carries. */
export const accel = (key) => ({
  accessKey: key,
  'aria-keyshortcuts': `Alt+${key.toUpperCase()}`,
})

/**
 * Ctrl+S or Ctrl+Enter (Cmd on macOS) saves while the editor is open.
 *
 * On `document` rather than the form: the edit windows are modal, so one is
 * open at a time, and a listener on the form would miss the shortcut before
 * focus has entered it. The latest `onSave` is read through a ref, so the
 * listener is attached once rather than on every render.
 */
export function useSaveShortcut(onSave, enabled) {
  const latest = useRef({ onSave, enabled })
  useEffect(() => {
    latest.current = { onSave, enabled }
  })

  useEffect(() => {
    function handle(e) {
      if (!(e.ctrlKey || e.metaKey)) return
      if (e.key !== 's' && e.key !== 'S' && e.key !== 'Enter') return
      e.preventDefault()
      if (latest.current.enabled) latest.current.onSave()
    }
    document.addEventListener('keydown', handle)
    return () => document.removeEventListener('keydown', handle)
  }, [])
}
