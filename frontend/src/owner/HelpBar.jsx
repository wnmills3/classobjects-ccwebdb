import { useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'

import { FIELD_HELP } from './fieldHelp'
import { HelpContext } from './help-context'

/**
 * The console's help band: always at the bottom of the window, explaining
 * whichever field has focus in any form above it.
 *
 * The console is laid out as a column the height of the window -- menu,
 * then the page, then this band -- and only the page scrolls, so a form
 * always fits between the menu and the band and the explanation never
 * scrolls out of sight (owner's request, 2026-09-23).
 *
 * `HelpScope`s inside the page set the field. The explanation is keyed to
 * the page it came from and cleared on moving to another, so a field on the
 * last page is never explained under this one.
 */
export function HelpProvider({ children }) {
  const { pathname } = useLocation()
  const [shown, setShown] = useState({ pathname, field: null })
  const field = shown.pathname === pathname ? shown.field : null

  const value = useMemo(
    () => ({
      field,
      setField: (next) => setShown({ pathname, field: next }),
    }),
    [field, pathname],
  )
  return <HelpContext.Provider value={value}>{children}</HelpContext.Provider>
}

/** The band itself, rendered once by the console shell below its content. */
export function HelpBar() {
  return (
    <HelpContext.Consumer>
      {(band) => {
        const help = band?.field ? FIELD_HELP[band.field] : null
        return (
          <footer className="help-bar" aria-live="polite" aria-label="Field help">
            {help ? (
              <>
                <strong>{help.title}</strong> {help.text}
              </>
            ) : (
              <span className="muted">Click in a field to see what it means.</span>
            )}
          </footer>
        )
      }}
    </HelpContext.Consumer>
  )
}
