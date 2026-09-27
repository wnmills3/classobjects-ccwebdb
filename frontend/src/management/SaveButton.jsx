import { AccessLabel } from './AccessLabel'
import { useSaveShortcut } from './shortcuts'

/**
 * The console's Save button: Ctrl+S (Cmd+S on macOS) is its shortcut on
 * every form, named to assistive technology, shown beside it, and marked by
 * the underlined S of its label.
 *
 * It only says so. The form's own `useSaveShortcut`, or a `SaveShortcut`
 * rendered while the form is open, is what answers the key -- a form knows
 * when it has something to save, and the button does not.
 */
export function SaveButton({ label = 'Save', saving = false, ...button }) {
  return (
    <>
      <button type="button" {...button} aria-keyshortcuts="Control+S" title="Ctrl+S">
        {/* The S that Ctrl+S names is underlined. An underline elsewhere
            means Alt plus the letter, so the hint beside the button says
            which key goes with this one. */}
        <AccessLabel text={saving ? 'Saving...' : label} accessKey="s" />
      </button>
      <span className="key-hint" aria-hidden="true">
        Ctrl+S
      </span>
    </>
  )
}

/**
 * Ctrl+S saves for as long as this is rendered.
 *
 * For a form that is not a component of its own -- an inline row edit -- or
 * one whose component stays mounted while closed: rendered only while the
 * form is open, it never holds the key for a form nobody can see, which
 * would stop Ctrl+S reaching the form beneath it.
 */
export function SaveShortcut({ onSave, enabled = true }) {
  useSaveShortcut(onSave, enabled)
  return null
}
