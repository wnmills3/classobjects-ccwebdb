import userEvent from '@testing-library/user-event'
import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ConfirmDialog from './ConfirmDialog'
import { renderWithProviders } from '../test/helpers'

function confirm(props = {}) {
  const onConfirm = vi.fn()
  const onCancel = vi.fn()
  renderWithProviders(
    <ConfirmDialog
      question="End the listing?"
      confirmLabel="End listing"
      busyLabel="Ending..."
      cancelLabel="Keep it"
      busy={false}
      onConfirm={onConfirm}
      onCancel={onCancel}
      {...props}
    >
      <p>It comes off the shop.</p>
    </ConfirmDialog>,
  )
  return { onConfirm, onCancel }
}

describe('ConfirmDialog', () => {
  it('confirms and backs out with its two buttons', async () => {
    const user = userEvent.setup()
    const { onConfirm, onCancel } = confirm()

    await user.click(screen.getByRole('button', { name: 'End listing', hidden: true }))
    await user.click(screen.getByRole('button', { name: 'Keep it', hidden: true }))

    expect(onConfirm).toHaveBeenCalledTimes(1)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('has no help band: a question has no field to explain', () => {
    confirm()
    expect(
      screen.getByRole('dialog', { name: 'End the listing?', hidden: true }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('contentinfo', { hidden: true })).toBeNull()
  })

  it('keeps the confirm button live, so an incomplete answer is explained on press', () => {
    // A greyed-out button says nothing about why. The caller's `onConfirm`
    // names what is missing instead, so nothing here can switch it off.
    confirm({ disabled: true })
    expect(
      screen.getByRole('button', { name: 'End listing', hidden: true }),
    ).toBeEnabled()
  })

  it('holds the confirm button while the request is out', () => {
    confirm({ busy: true })
    expect(
      screen.getByRole('button', { name: 'Ending...', hidden: true }),
    ).toBeDisabled()
  })
})
