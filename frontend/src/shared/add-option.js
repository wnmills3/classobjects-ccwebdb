import { useCallback, useLayoutEffect, useRef } from 'react'

//: The value of the option that opens a picker's inline add form.
export const ADD = '__add__'

//: Keys that step a closed select through its options, and which way.
const STEPS = {
  ArrowDown: 1,
  ArrowRight: 1,
  PageDown: 1,
  Home: 1,
  ArrowUp: -1,
  ArrowLeft: -1,
  PageUp: -1,
  End: -1,
}

//: How long after a key a change still counts as that key's. A closed
//: select changes in the same breath as the arrow; with its list open an
//: arrow only moves the highlight, and the change comes with Enter or a
//: click, much later.
const SAME_KEYSTROKE_MS = 100

/**
 * Keeps the arrow keys from opening a picker's add form.
 *
 * The Add option sits at the top of the list, under "--". A closed select
 * takes each arrow as a choice, so stepping down from "--" would choose Add
 * and open its form, when the person was only on the way to a value. An
 * arrow steps over it instead; choosing it from the open list, with Enter or
 * the mouse, still opens the form.
 *
 * Put `onKeyDown` on the select. When its value becomes `ADD`, call
 * `steppedOver(select)`: `undefined` means Add was chosen, so open the form;
 * otherwise it is the value the arrow was heading for, or `null` when there
 * is none that way and the select should stay as it is.
 */
export function useAddOption() {
  const last = useRef(null)

  function onKeyDown(e) {
    const step = STEPS[e.key]
    last.current = step ? { step, at: performance.now() } : null
  }

  function steppedOver(select) {
    const key = last.current
    last.current = null
    if (!key || performance.now() - key.at > SAME_KEYSTROKE_MS) return undefined
    const options = [...select.options]
    let index = options.findIndex((option) => option.value === ADD) + key.step
    while (index >= 0 && index < options.length) {
      if (!options[index].disabled) return options[index].value
      index += key.step
    }
    return null
  }

  return { onKeyDown, steppedOver }
}

/**
 * Whether a key is the Escape that cancels an inline add form -- and if so,
 * keeps it from going any further. Unhandled, the Escape would reach the
 * dialog the form sits in and close that, losing everything typed in it.
 */
export function isCancellingEscape(e) {
  if (e.key !== 'Escape') return false
  e.preventDefault()
  e.stopPropagation()
  return true
}

/**
 * The `ref` for an inline add form's wrapper: Escape inside it calls
 * `cancel` and closes nothing else.
 *
 * A listener the wrapper is given, not an `onKeyDown` written on it: the
 * wrapper is not a control, and is not made to look like one.
 */
export function useEscapeCancels(cancel) {
  const latest = useRef(cancel)
  useLayoutEffect(() => {
    latest.current = cancel
  })
  const hear = useCallback((e) => {
    if (isCancellingEscape(e)) latest.current()
  }, [])
  const heard = useRef(null)
  return useCallback(
    (element) => {
      heard.current?.removeEventListener('keydown', hear)
      heard.current = element
      element?.addEventListener('keydown', hear)
    },
    [hear],
  )
}
