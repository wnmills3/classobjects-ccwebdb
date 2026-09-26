import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AccessLabel } from './AccessLabel'
import { accel, useSaveShortcut } from './shortcuts'

function Probe({ onSave, enabled }) {
  useSaveShortcut(onSave, enabled)
  return <p>probe</p>
}

describe('AccessLabel', () => {
  it('underlines the access key letter and keeps the full text', () => {
    const { container } = render(<AccessLabel text="Shipping" accessKey="h" />)
    expect(container.textContent).toBe('Shipping')
    expect(container.querySelector('u').textContent).toBe('h')
  })

  it('renders plain text when the letter is absent', () => {
    const { container } = render(<AccessLabel text="Grade" accessKey="z" />)
    expect(container.querySelector('u')).toBeNull()
  })
})

describe('accel', () => {
  it('gives a control its access key and announces it', () => {
    expect(accel('h')).toEqual({ accessKey: 'h', 'aria-keyshortcuts': 'Alt+H' })
  })

  it('gives a control with no letter nothing', () => {
    expect(accel(null)).toEqual({})
  })
})

describe('useSaveShortcut', () => {
  it('saves on Ctrl+S and Ctrl+Enter, and stops the browser saving the page', () => {
    const onSave = vi.fn()
    render(<Probe onSave={onSave} enabled />)
    const event = new KeyboardEvent('keydown', {
      key: 's',
      ctrlKey: true,
      cancelable: true,
    })
    document.dispatchEvent(event)
    fireEvent.keyDown(document, { key: 'Enter', ctrlKey: true })
    expect(onSave).toHaveBeenCalledTimes(2)
    expect(event.defaultPrevented).toBe(true)
    expect(screen.getByText('probe')).toBeInTheDocument()
  })

  it('does nothing when disabled or without the modifier', () => {
    const onSave = vi.fn()
    const { rerender } = render(<Probe onSave={onSave} enabled={false} />)
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    rerender(<Probe onSave={onSave} enabled />)
    fireEvent.keyDown(document, { key: 's' })
    expect(onSave).not.toHaveBeenCalled()
  })

  it('answers only in the window opened last, even while that one is busy', () => {
    const under = vi.fn()
    const over = vi.fn()
    const { rerender } = render(
      <>
        <Probe onSave={under} enabled />
      </>,
    )
    rerender(
      <>
        <Probe onSave={under} enabled />
        <Probe onSave={over} enabled={false} />
      </>,
    )
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    expect(under).not.toHaveBeenCalled()
    expect(over).not.toHaveBeenCalled()

    rerender(
      <>
        <Probe onSave={under} enabled />
      </>,
    )
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    expect(under).toHaveBeenCalledTimes(1)
  })
})
