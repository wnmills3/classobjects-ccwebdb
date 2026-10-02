import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { SaveButton, SaveShortcut } from './SaveButton'

describe('SaveButton', () => {
  it('names Ctrl+S as its shortcut, not an Alt letter', () => {
    render(<SaveButton onClick={vi.fn()} />)
    const button = screen.getByRole('button', { name: 'Save' })
    expect(button).toHaveAttribute('aria-keyshortcuts', 'Control+S')
    expect(button).not.toHaveAttribute('accesskey')
    // Shown beside it: an underlined letter alone would read as Alt+S.
    expect(screen.getByText('Ctrl+S')).toBeInTheDocument()
  })

  it('takes its own label, and says when it is saving', () => {
    const { rerender } = render(<SaveButton label="Save order" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Save order' })).toBeEnabled()
    rerender(<SaveButton label="Save order" saving disabled onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled()
  })
})

describe('SaveButton: the underlined S', () => {
  it('underlines the S that Ctrl+S names, keeping its name', () => {
    render(<SaveButton label="Save order" onClick={vi.fn()} />)
    const button = screen.getByRole('button', { name: 'Save order' })
    expect(button.querySelector('u')).toHaveTextContent(/^S$/)
  })
})

describe('SaveShortcut', () => {
  it('saves on Ctrl+S while it is mounted, and not after', () => {
    const onSave = vi.fn()
    const { unmount } = render(<SaveShortcut onSave={onSave} />)
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    expect(onSave).toHaveBeenCalledTimes(1)

    unmount()
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    expect(onSave).toHaveBeenCalledTimes(1)
  })
})
