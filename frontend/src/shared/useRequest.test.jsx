import { act, renderHook, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { useRequest } from './useRequest'

/** A promise and the functions that settle it, for ordering answers by hand. */
function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useRequest', () => {
  it('is busy until the answer arrives, then holds it', async () => {
    const answer = deferred()
    const { result } = renderHook(() => useRequest('a', () => answer.promise))
    expect(result.current).toMatchObject({ data: undefined, error: '', busy: true })

    await act(async () => answer.resolve(['row']))

    expect(result.current).toMatchObject({ data: ['row'], error: '', busy: false })
  })

  it('reports a failure by its message', async () => {
    const { result } = renderHook(() =>
      useRequest('a', () => Promise.reject(new Error('service unavailable'))),
    )
    await waitFor(() => expect(result.current.busy).toBe(false))
    expect(result.current.error).toBe('service unavailable')
  })

  it('reports a fetcher that throws before returning a promise', async () => {
    const { result } = renderHook(() =>
      useRequest('a', () => {
        throw new Error('bad arguments')
      }),
    )
    await waitFor(() => expect(result.current.error).toBe('bad arguments'))
  })

  it('asks again when the key changes, keeping the old data while it does', async () => {
    const fetcher = vi.fn((key) => Promise.resolve(`page ${key}`))
    const { result, rerender } = renderHook(
      ({ key }) => useRequest(key, () => fetcher(key)),
      {
        initialProps: { key: 1 },
      },
    )
    await waitFor(() => expect(result.current.data).toBe('page 1'))

    rerender({ key: 2 })
    expect(result.current).toMatchObject({ data: 'page 1', busy: true })
    await waitFor(() =>
      expect(result.current).toMatchObject({ data: 'page 2', busy: false }),
    )
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('drops an answer that arrives after the key has moved on', async () => {
    // The slow answer for the old filter must not land on top of the new one.
    const slow = deferred()
    const fast = deferred()
    const answers = { old: slow.promise, new: fast.promise }
    const fetcher = vi.fn((key) => answers[key])
    const { result, rerender } = renderHook(
      ({ key }) => useRequest(key, () => fetcher(key)),
      {
        initialProps: { key: 'old' },
      },
    )
    rerender({ key: 'new' })

    await act(async () => fast.resolve('new results'))
    await act(async () => slow.resolve('old results'))

    expect(result.current).toMatchObject({ data: 'new results', busy: false })
    // Each request was made with its own render's fetcher.
    expect(fetcher.mock.calls).toEqual([['old'], ['new']])
  })

  it('makes no request for a null key', () => {
    const fetcher = vi.fn()
    const { result } = renderHook(() => useRequest(null, fetcher))
    expect(fetcher).not.toHaveBeenCalled()
    expect(result.current.busy).toBe(false)
  })

  it("does not report an earlier key's failure for the current key", async () => {
    const next = deferred()
    const answers = {
      bad: () => Promise.reject(new Error('order 999 not found')),
      good: () => next.promise,
    }
    const { result, rerender } = renderHook(
      ({ key }) => useRequest(key, answers[key] ?? (() => Promise.resolve())),
      { initialProps: { key: 'bad' } },
    )
    await waitFor(() => expect(result.current.error).toBe('order 999 not found'))

    // A null key asks nothing, so there is nothing to report at all.
    rerender({ key: null })
    expect(result.current).toMatchObject({ data: undefined, error: '', busy: false })

    // A new key is in flight: the old failure is not its failure.
    rerender({ key: 'good' })
    expect(result.current).toMatchObject({ error: '', busy: true })
    await act(async () => next.resolve('rows'))
    expect(result.current).toMatchObject({ data: 'rows', error: '', busy: false })
  })

  it('reports no data for a null key, even after an earlier answer', async () => {
    const { result, rerender } = renderHook(
      ({ key }) => useRequest(key, () => Promise.resolve(`page ${key}`)),
      { initialProps: { key: 1 } },
    )
    await waitFor(() => expect(result.current.data).toBe('page 1'))

    rerender({ key: null })
    expect(result.current).toMatchObject({ data: undefined, error: '', busy: false })
  })

  it('asks again for the same key on reload', async () => {
    let n = 0
    const { result } = renderHook(() => useRequest('a', () => Promise.resolve(++n)))
    await waitFor(() => expect(result.current.data).toBe(1))

    act(() => result.current.reload())
    expect(result.current.busy).toBe(true)

    await waitFor(() => expect(result.current).toMatchObject({ data: 2, busy: false }))
  })

  it('lands one answer under StrictMode', async () => {
    // StrictMode runs the effect, its cleanup, and the effect again. The first
    // run's answer is dropped by its cleanup; the second one's is kept.
    const fetcher = vi.fn(() => Promise.resolve('rows'))
    const { result } = renderHook(() => useRequest('a', fetcher), {
      wrapper: StrictMode,
    })
    await waitFor(() =>
      expect(result.current).toMatchObject({ data: 'rows', busy: false }),
    )
  })
})
