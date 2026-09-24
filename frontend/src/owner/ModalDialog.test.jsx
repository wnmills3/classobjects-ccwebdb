import { screen, within } from '@testing-library/react'
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
