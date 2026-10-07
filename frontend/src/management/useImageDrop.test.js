import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { isImageFile, useImageDrop } from './useImageDrop'

const photo = (name) => new File(['x'], name, { type: 'image/jpeg' })
const sheet = (name) => new File(['x'], name, { type: 'application/pdf' })

/** A drag or drop event as React hands it over, carrying these files. */
function dragOf(...files) {
  return { preventDefault: vi.fn(), dataTransfer: { files } }
}

/** A paste event whose clipboard holds these items. */
function pasteOf(...items) {
  return { preventDefault: vi.fn(), clipboardData: { items } }
}

/** A clipboard item of kind `file` that yields `file`. */
function fileItem(file) {
  return { kind: 'file', type: file.type, getAsFile: () => file }
}

function mount(props = {}) {
  const onImages = vi.fn()
  const hook = renderHook((p) => useImageDrop({ onImages, ...p }), {
    initialProps: props,
  })
  return { ...hook, onImages }
}

describe('isImageFile', () => {
  it('goes by the MIME type, for a file or a clipboard item alike', () => {
    expect(isImageFile(photo('a.jpg'))).toBe(true)
    expect(isImageFile({ kind: 'file', type: 'image/png' })).toBe(true)
    expect(isImageFile(sheet('a.pdf'))).toBe(false)
    expect(isImageFile({ kind: 'string', type: 'text/plain' })).toBe(false)
    expect(isImageFile(null)).toBe(false)
    expect(isImageFile({})).toBe(false)
  })
})

describe('useImageDrop', () => {
  it('takes the files chosen in the file box and clears the box', () => {
    const { result, onImages } = mount()
    const a = photo('obverse.jpg')
    const b = photo('reverse.jpg')
    const event = { target: { files: [a, b], value: 'C:\\fakepath\\obverse.jpg' } }

    act(() => result.current.onFileInput(event))

    expect(onImages).toHaveBeenCalledTimes(1)
    expect(onImages).toHaveBeenCalledWith([a, b])
    // Cleared, so the same file can be chosen again.
    expect(event.target.value).toBe('')
    expect(result.current.pickError).toBe('')
  })

  it('takes dropped photographs', () => {
    const { result, onImages } = mount()
    const a = photo('obverse.jpg')
    const drop = dragOf(a)

    act(() => result.current.target.onDrop(drop))

    expect(drop.preventDefault).toHaveBeenCalled()
    expect(onImages).toHaveBeenCalledWith([a])
  })

  it('refuses a whole drop that holds something other than a photograph', () => {
    const { result, onImages } = mount()

    act(() =>
      result.current.target.onDrop(dragOf(photo('obverse.jpg'), sheet('invoice.pdf'))),
    )

    // Not the photograph alone: the batch is refused, and the culprit named.
    expect(onImages).not.toHaveBeenCalled()
    expect(result.current.pickError).toBe(
      'invoice.pdf is not an image -- only a photograph can be added here.',
    )
  })

  it('names each file that is not a photograph', () => {
    const { result } = mount()
    act(() => result.current.target.onDrop(dragOf(sheet('a.pdf'), sheet('b.pdf'))))
    expect(result.current.pickError).toBe(
      'a.pdf, b.pdf are not images -- only a photograph can be added here.',
    )
  })

  it('drops the refusal once photographs are added', () => {
    const { result, onImages } = mount()
    act(() => result.current.target.onDrop(dragOf(sheet('a.pdf'))))
    expect(result.current.pickError).not.toBe('')

    act(() => result.current.target.onDrop(dragOf(photo('obverse.jpg'))))
    expect(result.current.pickError).toBe('')
    expect(onImages).toHaveBeenCalledTimes(1)
  })

  it('does nothing for an empty choice', () => {
    const { result, onImages } = mount()
    act(() => result.current.onFileInput({ target: { files: [], value: '' } }))
    act(() => result.current.target.onDrop({ preventDefault: vi.fn() }))
    expect(onImages).not.toHaveBeenCalled()
    expect(result.current.pickError).toBe('')
  })

  it('keeps the browser from opening a dragged image in the tab', () => {
    const { result } = mount()
    const over = dragOf()
    act(() => result.current.target.onDragOver(over))
    expect(over.preventDefault).toHaveBeenCalledTimes(1)
  })

  describe('whether a drag is over the target', () => {
    it('stays active while the pointer crosses the target’s own children', () => {
      const { result } = mount()
      // Onto the target, then onto a child of it: two enters, one leave.
      act(() => result.current.target.onDragEnter(dragOf()))
      act(() => result.current.target.onDragEnter(dragOf()))
      act(() => result.current.target.onDragLeave(dragOf()))
      expect(result.current.dragActive).toBe(true)

      act(() => result.current.target.onDragLeave(dragOf()))
      expect(result.current.dragActive).toBe(false)
    })

    it('ends with the drop, however deep the pointer was', () => {
      const { result } = mount()
      act(() => result.current.target.onDragEnter(dragOf()))
      act(() => result.current.target.onDragEnter(dragOf()))
      act(() => result.current.target.onDrop(dragOf(photo('a.jpg'))))
      expect(result.current.dragActive).toBe(false)

      // The count went back to zero too: one enter and one leave end it.
      act(() => result.current.target.onDragEnter(dragOf()))
      act(() => result.current.target.onDragLeave(dragOf()))
      expect(result.current.dragActive).toBe(false)
    })

    it.each(['dragend', 'drop'])(
      'ends when the drag is cancelled elsewhere (%s on the window)',
      (type) => {
        const { result } = mount()
        act(() => result.current.target.onDragEnter(dragOf()))
        expect(result.current.dragActive).toBe(true)

        act(() => {
          window.dispatchEvent(new Event(type))
        })
        expect(result.current.dragActive).toBe(false)
      },
    )

    it('stops listening on the window once unmounted', () => {
      const removed = vi.spyOn(window, 'removeEventListener')
      const { unmount } = mount()
      unmount()
      expect(removed.mock.calls.map(([type]) => type).sort()).toEqual([
        'dragend',
        'drop',
      ])
      removed.mockRestore()
    })
  })

  describe('pasting', () => {
    it('takes a pasted picture and keeps it out of the field under it', () => {
      const { result, onImages } = mount()
      const shot = photo('clipboard.png')
      const paste = pasteOf({ kind: 'string', type: 'text/plain' }, fileItem(shot))

      act(() => result.current.target.onPaste(paste))

      expect(onImages).toHaveBeenCalledWith([shot])
      expect(paste.preventDefault).toHaveBeenCalledTimes(1)
    })

    it('leaves pasted text alone to land where it was aimed', () => {
      const { result, onImages } = mount()
      const paste = pasteOf({ kind: 'string', type: 'text/plain' })

      act(() => result.current.target.onPaste(paste))

      expect(onImages).not.toHaveBeenCalled()
      expect(paste.preventDefault).not.toHaveBeenCalled()
    })

    it('skips a pasted file that is not a picture, without complaint', () => {
      const { result, onImages } = mount()
      const paste = pasteOf(fileItem(sheet('invoice.pdf')))

      act(() => result.current.target.onPaste(paste))

      expect(onImages).not.toHaveBeenCalled()
      expect(paste.preventDefault).not.toHaveBeenCalled()
      expect(result.current.pickError).toBe('')
    })
  })

  describe('when disabled', () => {
    it('shows no drag and takes no drop, but still keeps the image out of the tab', () => {
      const { result, onImages } = mount({ disabled: true })
      act(() => result.current.target.onDragEnter(dragOf()))
      expect(result.current.dragActive).toBe(false)

      const drop = dragOf(photo('a.jpg'))
      act(() => result.current.target.onDrop(drop))
      expect(onImages).not.toHaveBeenCalled()
      expect(drop.preventDefault).toHaveBeenCalledTimes(1)
    })

    it('leaves a pasted picture alone', () => {
      const { result, onImages } = mount({ disabled: true })
      const paste = pasteOf(fileItem(photo('clipboard.png')))

      act(() => result.current.target.onPaste(paste))

      expect(onImages).not.toHaveBeenCalled()
      expect(paste.preventDefault).not.toHaveBeenCalled()
    })

    it('takes a drop again once enabled', () => {
      const { result, rerender, onImages } = mount({ disabled: true })
      rerender({ disabled: false })
      const a = photo('a.jpg')
      act(() => result.current.target.onDrop(dragOf(a)))
      expect(onImages).toHaveBeenCalledWith([a])
    })
  })
})
