import { StrictMode } from 'react'
import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { useGuardedSave } from './useGuardedSave'

/** A request whose answer the test gives when it chooses. */
function pending() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { request: () => promise, resolve, reject }
}

describe('useGuardedSave', () => {
  it('hands what was saved to onSaved, saving only while the request is out', async () => {
    const onSaved = vi.fn()
    const { result } = renderHook(() => useGuardedSave(onSaved))
    const { request, resolve } = pending()
    expect(result.current.saving).toBe(false)

    let sent
    act(() => {
      sent = result.current.send(request)
    })
    expect(result.current.saving).toBe(true)
    expect(onSaved).not.toHaveBeenCalled()

    await act(async () => {
      resolve({ id: 7 })
      await sent
    })
    expect(onSaved).toHaveBeenCalledTimes(1)
    expect(onSaved).toHaveBeenCalledWith({ id: 7 })
    expect(result.current.saving).toBe(false)
    expect(result.current.error).toBe('')
  })

  it('shows a refusal and tells nobody it saved', async () => {
    const onSaved = vi.fn()
    const { result } = renderHook(() => useGuardedSave(onSaved))

    await act(async () => {
      await result.current.send(() => Promise.reject(new Error('Already listed')))
    })
    expect(result.current.error).toBe('Already listed')
    expect(onSaved).not.toHaveBeenCalled()
    expect(result.current.saving).toBe(false)
  })

  it('shows what the request itself throws, as it shows a refusal', async () => {
    const onSaved = vi.fn()
    const { result } = renderHook(() => useGuardedSave(onSaved))

    await act(async () => {
      await result.current.send(async () => {
        throw new Error('No item with code CC-000009.')
      })
    })
    expect(result.current.error).toBe('No item with code CC-000009.')
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('clears the last error when the next save starts', async () => {
    const { result } = renderHook(() => useGuardedSave(vi.fn()))
    act(() => result.current.setError('A lot needs a title.'))
    expect(result.current.error).toBe('A lot needs a title.')

    const { request, resolve } = pending()
    let sent
    act(() => {
      sent = result.current.send(request)
    })
    expect(result.current.error).toBe('')
    await act(async () => {
      resolve({})
      await sent
    })
  })

  it('does nothing with an answer that arrives after the dialog closed', async () => {
    const onSaved = vi.fn()
    const { result, unmount } = renderHook(() => useGuardedSave(onSaved))
    const { request, resolve } = pending()
    let sent
    act(() => {
      sent = result.current.send(request)
    })
    const { mounted } = result.current
    expect(mounted.current).toBe(true)

    unmount()
    expect(mounted.current).toBe(false)
    await act(async () => {
      resolve({ id: 7 })
      await sent
    })
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('hands a late answer to onLateSaved, and only a late one', async () => {
    // The server saved it whether or not the dialog stayed: the parent puts
    // the row right, without closing whatever dialog is open by then.
    const onSaved = vi.fn()
    const onLateSaved = vi.fn()
    const { result, unmount } = renderHook(() => useGuardedSave(onSaved, onLateSaved))

    // In time: onSaved alone.
    await act(async () => {
      await result.current.send(() => Promise.resolve({ id: 6 }))
    })
    expect(onSaved).toHaveBeenCalledWith({ id: 6 })
    expect(onLateSaved).not.toHaveBeenCalled()

    // Late: onLateSaved alone.
    const { request, resolve } = pending()
    let sent
    act(() => {
      sent = result.current.send(request)
    })
    unmount()
    await act(async () => {
      resolve({ id: 7, version: 2 })
      await sent
    })
    expect(onLateSaved).toHaveBeenCalledTimes(1)
    expect(onLateSaved).toHaveBeenCalledWith({ id: 7, version: 2 })
    expect(onSaved).toHaveBeenCalledTimes(1)
  })

  it('tells onLateSaved nothing of a refusal after the dialog closed', async () => {
    const onLateSaved = vi.fn()
    const { result, unmount } = renderHook(() => useGuardedSave(vi.fn(), onLateSaved))
    const { request, reject } = pending()
    let sent
    act(() => {
      sent = result.current.send(request)
    })
    unmount()
    await act(async () => {
      reject(new Error('too late'))
      await sent
    })
    expect(onLateSaved).not.toHaveBeenCalled()
  })

  it('does nothing with a refusal that arrives after the dialog closed', async () => {
    const onSaved = vi.fn()
    const { result, unmount } = renderHook(() => useGuardedSave(onSaved))
    const { request, reject } = pending()
    let sent
    act(() => {
      sent = result.current.send(request)
    })
    unmount()

    // Resolves quietly: a state change after unmount would be an error here.
    await act(async () => {
      reject(new Error('too late'))
      await sent
    })
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('still reports a save in StrictMode, where effects run twice on mount', async () => {
    // The console runs in StrictMode: a guard armed only at first render
    // would be left disarmed there, and no save would ever close its dialog.
    const onSaved = vi.fn()
    const { result } = renderHook(() => useGuardedSave(onSaved), {
      wrapper: StrictMode,
    })

    await act(async () => {
      await result.current.send(() => Promise.resolve({ id: 3 }))
    })
    expect(onSaved).toHaveBeenCalledWith({ id: 3 })
    expect(result.current.saving).toBe(false)
  })
})
