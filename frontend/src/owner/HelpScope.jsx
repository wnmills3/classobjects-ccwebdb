import { useState } from 'react'

import { FIELD_HELP } from './fieldHelp'

/**
 * A form's help area: explains whichever field has focus.
 *
 * Wrap a form in it and mark each field's label with `data-help="<key>"`
 * (a key of `FIELD_HELP`). Focus landing anywhere inside such a label --
 * its input, select or picker -- shows that field's title and text in the
 * area below the form. Nothing is added inside the labels themselves: a
 * control placed in a label with no `for` can take the label from its
 * field (measured 2026-09-23 with a "?" button), and this needs none.
 *
 * The last field explained stays shown when focus leaves, rather than the
 * area flickering empty between fields. A focused field with no help
 * leaves it as it was.
 *
 * Scopes nest: the innermost one that finds help for the focused field
 * shows it, and the event goes no further -- the Friedberg lookup inside
 * the item editor explains its own fields in its own area.
 */
export default function HelpScope({ children }) {
  const [field, setField] = useState(null)

  function onFocus(e) {
    const key = e.target.closest?.('[data-help]')?.dataset.help
    if (!key || !FIELD_HELP[key]) return
    setField(key)
    e.stopPropagation()
  }

  const help = field ? FIELD_HELP[field] : null
  return (
    <div className="help-scope" onFocus={onFocus}>
      {children}
      <div className="help-area" aria-live="polite">
        {help ? (
          <>
            <strong>{help.title}</strong> {help.text}
          </>
        ) : (
          <span className="muted">Click in a field to see what it means.</span>
        )}
      </div>
    </div>
  )
}
