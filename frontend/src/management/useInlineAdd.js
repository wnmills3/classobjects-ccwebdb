import { useState } from 'react'

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
 * `onKeyDown` goes on the form's wrapper. The form sits inside a larger
 * `<form>`: without it, Enter in a box submits that outer form -- for
 * whatever was already picked -- instead of adding what is being typed. A
 * button keeps its own Enter: swallowing it would cancel Cancel's own
 * activation and add the row the user was abandoning.
 */
export function useInlineAdd({ blank, ready, create, onCreated }) {
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(blank)
  const [error, setError] = useState('')

  async function add() {
    try {
      const created = await create(draft)
      onCreated(created)
      setAdding(false)
      setDraft(blank)
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }

  function onKeyDown(e) {
    if (e.key !== 'Enter' || e.target.tagName === 'BUTTON') return
    e.preventDefault()
    if (ready(draft)) add()
  }

  return {
    adding,
    setAdding,
    draft,
    setDraft,
    error,
    add,
    onKeyDown,
    ready: ready(draft),
  }
}
