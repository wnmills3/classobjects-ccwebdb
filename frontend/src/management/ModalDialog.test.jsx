import { fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { HelpBar, HelpProvider } from './HelpBar'
import HelpScope from './HelpScope'
import ModalDialog from './ModalDialog'
import { FIELD_HELP } from './fieldHelp'
import { renderWithProviders } from '../test/helpers'

// The console shell: its own band below the page, and a dialog opened over it.
function Console() {
  return (
    <HelpProvider>
      <p>Page behind</p>
      <ModalDialog label="Edit item" onClose={vi.fn()}>
        <HelpScope>
          <label data-help="grade_designation">
            Grade designation
            <input />
          </label>
        </HelpScope>
      </ModalDialog>
      <HelpBar />
    </HelpProvider>
  )
}

describe('ModalDialog Escape', () => {
  it('closes only the dialog on top when one is opened inside another', () => {
    const closeOuter = vi.fn()
    const closeInner = vi.fn()
    renderWithProviders(
      <ModalDialog label="Edit item" onClose={closeOuter}>
        <ModalDialog label="Obverse" onClose={closeInner}>
          <p>Enlarged</p>
        </ModalDialog>
      </ModalDialog>,
    )
    const inner = screen.getByRole('dialog', { name: 'Obverse', hidden: true })
    // Escape reaches a native dialog as its `cancel` event, which does not
    // bubble in the page; React still offers it to the dialog around it.
    fireEvent(inner, new Event('cancel', { cancelable: true }))
    expect(closeInner).toHaveBeenCalledTimes(1)
    expect(closeOuter).not.toHaveBeenCalled()

    const outer = screen.getByRole('dialog', { name: 'Edit item', hidden: true })
    fireEvent(outer, new Event('cancel', { cancelable: true }))
    expect(closeOuter).toHaveBeenCalledTimes(1)
  })
})

describe('ModalDialog help band', () => {
  it('explains a focused field at the bottom of the dialog, not behind it', async () => {
    const user = userEvent.setup()
    renderWithProviders(<Console />)
    const [inDialog, behind] = screen.getAllByRole('contentinfo', {
      name: 'Field help',
      hidden: true,
    })

    await user.click(screen.getByLabelText('Grade designation', { selector: 'input' }))

    const help = FIELD_HELP.grade_designation
    expect(within(inDialog).getByText(help.title)).toBeInTheDocument()
    expect(inDialog.closest('dialog')).not.toBeNull()
    // The console's own band, covered by the modal, is left alone.
    expect(within(behind).queryByText(help.title)).toBeNull()
  })

  it('keeps the band outside the part of the dialog that scrolls', () => {
    renderWithProviders(<Console />)
    const dialog = screen.getByRole('dialog', { name: 'Edit item', hidden: true })
    const band = within(dialog).getByRole('contentinfo', { hidden: true })
    expect(band.closest('.edit-dialog-body')).toBeNull()
    expect(dialog.querySelector('.edit-dialog-body')).not.toBeNull()
  })
})
