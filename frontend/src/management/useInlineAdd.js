import { useCallback, useLayoutEffect, useRef, useState } from 'react'

/**
 * The small form a picker opens from its "+ Add ..." option: the vendor,
 * seller and storage location pickers' shared state and behaviour.
 *
 * `blank` is the empty draft. `ready(draft)` says whether the draft holds
 * enough to send. `create(draft)` sends it and resolves to the row made;
 * `onCreated(row)` then takes it -- the picker adds it to its list and
 * selects it. A refusal stays in `error`, with the form open and the draft
 * as typed.
 *
 * `wrapper` is the `ref` of the form's wrapper, which then hears its boxes'
 * keys with `onKeyDown`. The form sits inside a larger `<form>`: without
 * it, Enter in a box submits that outer form -- for whatever was already
 * picked -- instead of adding what is being typed. A button keeps its own
 * Enter: swallowing it would cancel Cancel's own activation and add the row
 * the user was abandoning.
 *
 * A listener the wrapper is given, not an `onKeyDown` written on it: the
 * wrapper is not a control, and is not made to look like one.
 */
export function useInlineAdd({ blank, ready, create, onCreated }) {
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(blank)
  const [error, setError] = useState('')

  // A ref, not state: Enter held down asks again before the next render.
  const sending = useRef(false)

  async function add() {
    // One request at a time: a second behind the first would make the row
    // twice, or be refused for a row the first had just made.
    if (sending.current) return
    sending.current = true
    try {
      const created = await create(draft)
      onCreated(created)
      setAdding(false)
      setDraft(blank)
      setError('')
    } catch (err) {
      setError(err.message)
    } finally {
      sending.current = false
    }
  }

  function onKeyDown(e) {
    if (e.key !== 'Enter' || e.target.tagName === 'BUTTON') return
    e.preventDefault()
    if (ready(draft)) add()
  }

  // The wrapper hears its boxes' keys through a listener of its own, which
  // is put on once and so must reach this render's `onKeyDown` -- the one
  // that reads the draft as it now stands -- through a ref.
  const latestKeyDown = useRef(onKeyDown)
  useLayoutEffect(() => {
    latestKeyDown.current = onKeyDown
  })
  const hearKey = useCallback((e) => latestKeyDown.current(e), [])
  const heard = useRef(null)
  const wrapper = useCallback(
    (element) => {
      heard.current?.removeEventListener('keydown', hearKey)
      heard.current = element
      element?.addEventListener('keydown', hearKey)
    },
    [hearKey],
  )

  return {
    adding,
    setAdding,
    draft,
    setDraft,
    error,
    add,
    onKeyDown,
    wrapper,
    ready: ready(draft),
  }
}
