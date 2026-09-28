import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    attachFriedberg: vi.fn(),
    clearFriedberg: vi.fn(),
    getSignatureChoices: vi.fn(),
    searchFriedberg: vi.fn(),
    createFriedbergNumber: vi.fn(),
  },
}))

import { api } from '../../api'
import FriedbergPanel from './FriedbergPanel'
import { renderWithProviders } from '../../../test/helpers'

// Synthetic numbers only, per CLAUDE.md's ban on shipping a publisher's
// Friedberg arrangement.
const PROPOSED = {
  id: 412,
  item_kind: 'currency',
  friedberg_id: 9,
  friedberg_number: '9901',
  friedberg_status: 'proposed',
  friedberg_verified: false,
  attributes: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.attachFriedberg.mockResolvedValue({})
  api.clearFriedberg.mockResolvedValue(null)
  api.getSignatureChoices.mockResolvedValue({ values: [] })
  api.searchFriedberg.mockResolvedValue([])
})

describe('FriedbergPanel', () => {
  it('shows the attached number and its status', () => {
    renderWithProviders(<FriedbergPanel item={PROPOSED} onHold={vi.fn()} />)
    expect(screen.getByText('9901')).toBeInTheDocument()
    expect(screen.getByText(/proposed, not yet verified/i)).toBeInTheDocument()
  })

  it('holds a confirmation for the editor to save', async () => {
    const onHold = vi.fn()
    renderWithProviders(<FriedbergPanel item={PROPOSED} onHold={onHold} />)
    await userEvent.click(screen.getByRole('button', { name: /^confirm$/i }))

    // The same catalog row, now as confirmed -- held, not sent.
    expect(onHold).toHaveBeenCalledWith({
      action: 'attach',
      friedberg_id: 9,
      status: 'confirmed',
      fr_number: '9901',
    })
    expect(api.attachFriedberg).not.toHaveBeenCalled()
  })

  it('offers no Confirm once the number is confirmed', () => {
    renderWithProviders(
      <FriedbergPanel
        item={{ ...PROPOSED, friedberg_status: 'confirmed', friedberg_verified: true }}
        onHold={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: /^confirm$/i })).not.toBeInTheDocument()
    expect(screen.getByText(/confirmed, verified/i)).toBeInTheDocument()
  })

  it('holds clearing the number for the editor to save', async () => {
    const onHold = vi.fn()
    renderWithProviders(<FriedbergPanel item={PROPOSED} onHold={onHold} />)
    await userEvent.click(screen.getByRole('button', { name: /^clear$/i }))
    expect(onHold).toHaveBeenCalledWith({ action: 'clear' })
    expect(api.clearFriedberg).not.toHaveBeenCalled()
  })

  it('shows a held number as not saved yet, and undoes it', async () => {
    const onUndo = vi.fn()
    renderWithProviders(
      <FriedbergPanel
        item={PROPOSED}
        onHold={vi.fn()}
        onUndo={onUndo}
        pending={{
          action: 'attach',
          friedberg_id: 10,
          status: 'proposed',
          fr_number: '9902',
        }}
      />,
    )
    expect(screen.getByText(/9902/)).toBeInTheDocument()
    expect(screen.getByText(/not saved yet/i)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /^undo$/i }))
    expect(onUndo).toHaveBeenCalled()
  })

  it('shows a held clearing, and why it failed if it did', () => {
    renderWithProviders(
      <FriedbergPanel
        item={PROPOSED}
        onHold={vi.fn()}
        onUndo={vi.fn()}
        pending={{ action: 'clear', error: 'Inventory item not found' }}
      />,
    )
    expect(screen.getByText(/cleared when you save/i)).toBeInTheDocument()
    expect(screen.getByText('Inventory item not found')).toBeInTheDocument()
  })

  it('one press of Look up searches with what the note records', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => ({}))
    const note = {
      ...PROPOSED,
      friedberg_id: null,
      friedberg_number: null,
      friedberg_status: 'unknown',
      friedberg_verified: null,
      fed_district: 'B',
    }
    renderWithProviders(<FriedbergPanel item={note} onHold={vi.fn()} />)
    expect(screen.getByText(/no number attached/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^clear$/i })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    // Would fail the old two-press flow: no second Look up is pressed here,
    // and no search field is shown to fill in (2026-09-23).
    await waitFor(() =>
      expect(api.searchFriedberg).toHaveBeenCalledWith({ district_letter: 'B' }),
    )
    expect(screen.queryByLabelText(/series year/i)).toBeNull()
    // Nothing in the catalog: the web search opens on its own.
    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        expect.stringContaining('https://www.google.com/ai?q='),
        'friedberg-web-search',
        expect.stringContaining('popup'),
      ),
    )
    open.mockRestore()
  })
})
