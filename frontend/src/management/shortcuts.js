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

/** The attributes an accelerated control carries; none for one without a letter. */
export const accel = (key) =>
  key ? { accessKey: key, 'aria-keyshortcuts': `Alt+${key.toUpperCase()}` } : {}

//: Every mounted `useSaveShortcut`, oldest first. Only the last one answers.
const saveHandlers = []

function handleSaveKey(e) {
  if (!(e.ctrlKey || e.metaKey)) return
  if (e.key !== 's' && e.key !== 'S' && e.key !== 'Enter') return
  e.preventDefault()
  const top = saveHandlers.at(-1).current
  if (top.enabled) top.onSave()
}

/**
 * Ctrl+S or Ctrl+Enter (Cmd on macOS) saves while the editor is open.
 *
 * On `document` rather than the form, since a listener on the form would miss
 * the shortcut before focus has entered it. Edit windows nest -- the item
 * editor opens the offer dialog over itself -- so the editors register on a
 * stack and only the one opened last answers: Ctrl+S in the offer dialog
 * offers, and does not also save the edit form behind it. The top one takes
 * the key even while it is disabled, so a busy dialog never lets it fall
 * through to the window underneath.
 *
 * The latest `onSave` is read through a ref, so an editor registers once
 * rather than on every render.
 */
export function useSaveShortcut(onSave, enabled) {
  const latest = useRef({ onSave, enabled })
  useEffect(() => {
    latest.current = { onSave, enabled }
  })

  useEffect(() => {
    saveHandlers.push(latest)
    if (saveHandlers.length === 1) document.addEventListener('keydown', handleSaveKey)
    return () => {
      saveHandlers.splice(saveHandlers.indexOf(latest), 1)
      if (saveHandlers.length === 0) {
        document.removeEventListener('keydown', handleSaveKey)
      }
    }
  }, [])
}
