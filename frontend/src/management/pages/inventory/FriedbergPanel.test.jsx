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
  friedberg_number: 'FR-TEST-9',
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
    renderWithProviders(<FriedbergPanel item={PROPOSED} onChanged={vi.fn()} />)
    expect(screen.getByText('FR-TEST-9')).toBeInTheDocument()
    expect(screen.getByText(/proposed, not yet verified/i)).toBeInTheDocument()
  })

  it('confirms the attached number, then asks the editor to reload', async () => {
    const onChanged = vi.fn()
    renderWithProviders(<FriedbergPanel item={PROPOSED} onChanged={onChanged} />)
    await userEvent.click(screen.getByRole('button', { name: /^confirm$/i }))

    // The same catalog row, now as confirmed -- not a new lookup.
    await waitFor(() =>
      expect(api.attachFriedberg).toHaveBeenCalledWith(412, {
        friedberg_id: 9,
        status: 'confirmed',
      }),
    )
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('offers no Confirm once the number is confirmed', () => {
    renderWithProviders(
      <FriedbergPanel
        item={{ ...PROPOSED, friedberg_status: 'confirmed', friedberg_verified: true }}
        onChanged={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: /^confirm$/i })).not.toBeInTheDocument()
    expect(screen.getByText(/confirmed, verified/i)).toBeInTheDocument()
  })

  it('clears the number off the note', async () => {
    const onChanged = vi.fn()
    renderWithProviders(<FriedbergPanel item={PROPOSED} onChanged={onChanged} />)
    await userEvent.click(screen.getByRole('button', { name: /^clear$/i }))
    await waitFor(() => expect(api.clearFriedberg).toHaveBeenCalledWith(412))
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
  })

  it('shows a failure instead of reloading as if it worked', async () => {
    const onChanged = vi.fn()
    api.clearFriedberg.mockRejectedValue(new Error('Inventory item not found'))
    renderWithProviders(<FriedbergPanel item={PROPOSED} onChanged={onChanged} />)
    await userEvent.click(screen.getByRole('button', { name: /^clear$/i }))
    expect(await screen.findByText('Inventory item not found')).toBeInTheDocument()
    expect(onChanged).not.toHaveBeenCalled()
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
    renderWithProviders(<FriedbergPanel item={note} onChanged={vi.fn()} />)
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
