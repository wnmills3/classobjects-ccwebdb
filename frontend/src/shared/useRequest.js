import { useCallback, useEffect, useEffectEvent, useState } from 'react'

/**
 * Load something when a component mounts, and again whenever `key` changes.
 *
 * `useRequest(key, fetcher)` calls `fetcher()` -- which returns a promise --
 * and returns `{ data, error, busy, reload }`:
 *
 * - `data` is what the latest successful request resolved to. It is kept
 *   while a newer request is out, so a page can go on showing the previous
 *   results under a "Loading..."; a caller that must not show stale data
 *   checks `busy`. Undefined while `key` is null.
 * - `error` is the message of the request for the current `key` if it
 *   failed, else ''. Another key's failure is never reported: it is '' while
 *   a new key's request is out, and while `key` is null.
 * - `busy` is true while no request for the current `key` has settled.
 * - `reload()` asks again for the same `key`.
 *
 * `key` is compared with `===`, so it is a string, a number or null: build it
 * from everything the request depends on (`JSON.stringify` of the filters,
 * say). A null `key` makes no request at all.
 *
 * An answer that arrives after `key` has moved on, or after the component has
 * unmounted, is dropped -- a slow response for an old filter never lands on
 * top of a newer one. `fetcher` is read when the request is made, so it may
 * close over the latest props without being part of `key`.
 */
export function useRequest(key, fetcher) {
  const [state, setState] = useState({ key: undefined, data: undefined, error: '' })
  const [round, setRound] = useState(0)
  // Called now, inside the effect, so it runs with this render's `fetcher`.
  // A fetcher that throws before returning a promise is reported the same
  // way as one that rejects.
  const request = useEffectEvent(() => {
    try {
      return Promise.resolve(fetcher())
    } catch (err) {
      return Promise.reject(err)
    }
  })

  useEffect(() => {
    if (key === null) return undefined
    let cancelled = false
    request().then(
      (data) => {
        if (!cancelled) setState({ key, data, error: '' })
      },
      (err) => {
        if (!cancelled) {
          setState((s) => ({ key, data: s.data, error: err?.message ?? String(err) }))
        }
      },
    )
    return () => {
      cancelled = true
    }
    // `round` is here only to repeat the request on reload().
  }, [key, round])

  const reload = useCallback(() => {
    setState((s) => ({ ...s, key: undefined }))
    setRound((n) => n + 1)
  }, [])

  const settled = state.key === key
  return {
    data: key === null ? undefined : state.data,
    error: key !== null && settled ? state.error : '',
    busy: key !== null && !settled,
    reload,
  }
}
