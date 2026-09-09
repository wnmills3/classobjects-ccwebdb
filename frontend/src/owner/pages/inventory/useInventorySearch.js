import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../../../shared/api'
import { PAGE_SIZE } from './specs'

/**
 * The search, driven by the URL.
 *
 * The URL is the source of truth, so a filtered view can be bookmarked,
 * shared with someone, or survive a reload.
 *
 * The result is stored *with the query that produced it*, so "still loading"
 * is derived -- it is exactly "what is displayed does not match what is being
 * asked for". A busy flag would be a second piece of state saying the same
 * thing, able to disagree with the first.
 */
export function useInventorySearch(view) {
  const [params, setParams] = useSearchParams()
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  const current = Object.fromEntries(params.entries())
  const offset = Number(current.offset ?? 0)

  // A plain string, so the dependency is a simple expression the linter and
  // React can both reason about.
  const query = params.toString()

  useEffect(() => {
    // Not only tidiness: typing in the search box fires a request per
    // keystroke, and without this an early slow response can land after a
    // later fast one and overwrite newer results with older ones.
    let cancelled = false

    api
      .searchInventory(view, {
        ...Object.fromEntries(new URLSearchParams(query)),
        facets: true,
        limit: PAGE_SIZE,
      })
      .then((body) => {
        if (cancelled) return
        setResult({ query, body })
        setError('')
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })

    return () => {
      cancelled = true
    }
  }, [view, query])

  function apply(changes) {
    const next = { ...current, ...changes }
    // Any change to the filters returns to the first page: staying on page 7
    // of a result set that no longer has one is disorienting.
    if (!('offset' in changes)) delete next.offset
    Object.keys(next).forEach((k) => {
      if (next[k] === '' || next[k] === null || next[k] === undefined) delete next[k]
    })
    setParams(next)
  }

  return {
    current,
    apply,
    clear: () => setParams({}),
    page: result?.body,
    busy: result?.query !== query,
    error,
    offset,
  }
}
