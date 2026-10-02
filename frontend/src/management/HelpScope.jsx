import { useState } from 'react'

import { FIELD_HELP } from './fieldHelp'
import { useHelpBand } from './help-context'

/**
 * Makes a form's fields explain themselves when they have focus.
 *
 * Wrap a form in it and mark each field's label (or the element wrapping a
 * group of radios, or a box named through `htmlFor`) with
 * `data-help="<key>"`, a key of `FIELD_HELP`. Focus landing anywhere inside
 * such an element shows that field's title and text. A button or a link
 * marked the same way is also explained while the pointer is over it, since
 * clicking it to give it focus would press it.
 *
 * **Where it shows:** in the console's help band at the bottom of the window
 * (`HelpBar`), when there is one -- the console shell always has one. Outside
 * the shell, as when a form is rendered on its own in a component test, it
 * shows in an area of its own below the form instead.
 *
 * Nothing is added inside the labels themselves: a control placed in a label
 * with no `for` can take the label from its field (a "?" button does), and
 * this needs none.
 *
 * The last field explained stays shown when focus moves to one with no help,
 * rather than the explanation flickering away between fields. Scopes nest:
 * the innermost one that finds help for the focused field shows it, and the
 * event goes no further.
 */
export default function HelpScope({ children }) {
  const band = useHelpBand()
  const [localField, setLocalField] = useState(null)

  function explain(e, element) {
    const key = element?.closest?.('[data-help]')?.dataset.help
    if (!key || !FIELD_HELP[key]) return
    if (band) band.setField(key)
    else setLocalField(key)
    e.stopPropagation()
  }

  const onFocus = (e) => explain(e, e.target)
  // A button or a link cannot be clicked into to read about it: the click
  // presses it. The pointer resting on one explains it instead.
  const onMouseOver = (e) => explain(e, e.target.closest?.('button, a'))

  if (band) {
    return (
      <div className="help-scope" onFocus={onFocus} onMouseOver={onMouseOver}>
        {children}
      </div>
    )
  }

  const help = localField ? FIELD_HELP[localField] : null
  return (
    <div className="help-scope" onFocus={onFocus} onMouseOver={onMouseOver}>
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
