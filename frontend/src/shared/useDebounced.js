import { useEffect, useState } from 'react'

/**
 * `value`, once it has stopped changing for `delay` milliseconds.
 *
 * For a search box that asks the server: typing "morgan" is one request for
 * "morgan", not six for "m", "mo", "mor" and the rest. Starts at `value`, so
 * the first render asks for the initial value at once.
 */
export function useDebounced(value, delay) {
  const [settled, setSettled] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])

  return settled
}
