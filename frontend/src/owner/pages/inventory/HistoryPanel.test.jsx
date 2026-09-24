import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getItemHistory: vi.fn(),
  },
}))

import { api } from '../../api'
import HistoryPanel from './HistoryPanel'
import { renderWithProviders } from '../../../test/helpers'

const EDIT = {
  kind: 'field',
  field: 'source_title',
  old_value: 'Morgan',
  new_value: '1881-S Morgan Dollar',
  by: 'Test Admin',
  at: '2026-09-23T20:00:00Z',
  note: null,
  arrived_on: null,
}

const RECEIVED = {
  kind: 'status',
  field: 'status',
  old_value: 'Ordered',
  new_value: 'Received',
  by: null,
  at: '2026-09-18T12:00:00Z',
  note: 'box opened',
  arrived_on: '2026-09-16',
}

const ATTRIBUTES = {
  ...EDIT,
  field: 'attributes',
  old_value: [],
  new_value: ['First Strike', 'Star Note'],
}

function rowsOf() {
  return within(screen.getByRole('table')).getAllByRole('row').slice(1)
}

describe('HistoryPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('lists each entry with what changed, from what, to what and who', async () => {
    api.getItemHistory.mockResolvedValue([EDIT, RECEIVED, ATTRIBUTES])
    renderWithProviders(<HistoryPanel itemId={7} version={1} />)

    await waitFor(() => expect(rowsOf()).toHaveLength(3))
    expect(api.getItemHistory).toHaveBeenCalledWith(7)
    const [edit, received, attributes] = rowsOf().map((r) =>
      within(r)
        .getAllByRole('cell')
        .slice(1)
        .map((c) => c.textContent),
    )
    // A field by its help title, the way the editor names it.
    expect(edit).toEqual(['Test Admin', 'Title', 'Morgan', '1881-S Morgan Dollar', ''])
    expect(received).toEqual([
      'unknown',
      'Status',
      'Ordered',
      'Received',
      'box opened; arrived 2026-09-16',
    ])
    expect(attributes.slice(2, 4)).toEqual(['(none)', 'First Strike, Star Note'])
  })

  it('reads the history again when the item version moves', async () => {
    api.getItemHistory
      .mockResolvedValueOnce([RECEIVED])
      .mockResolvedValueOnce([EDIT, RECEIVED])
    const { rerender } = renderWithProviders(<HistoryPanel itemId={7} version={1} />)
    await waitFor(() => expect(rowsOf()).toHaveLength(1))

    rerender(<HistoryPanel itemId={7} version={2} />)
    await waitFor(() => expect(rowsOf()).toHaveLength(2))
    expect(api.getItemHistory).toHaveBeenCalledTimes(2)
  })

  it('says so when nothing is logged', async () => {
    api.getItemHistory.mockResolvedValue([])
    renderWithProviders(<HistoryPanel itemId={7} version={1} />)
    expect(await screen.findByText('Nothing logged yet.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows the recent entries first and the rest on request', async () => {
    const many = Array.from({ length: 25 }, (_, n) => ({
      ...EDIT,
      at: `2026-09-23T20:${String(n).padStart(2, '0')}:00Z`,
    }))
    api.getItemHistory.mockResolvedValue(many)
    renderWithProviders(<HistoryPanel itemId={7} version={1} />)

    await waitFor(() => expect(rowsOf()).toHaveLength(20))
    await userEvent.click(screen.getByRole('button', { name: 'Show all 25' }))
    expect(rowsOf()).toHaveLength(25)
  })

  it('shows a failure to read rather than an empty history', async () => {
    api.getItemHistory.mockRejectedValue(new Error('Server unavailable'))
    renderWithProviders(<HistoryPanel itemId={7} version={1} />)
    expect(await screen.findByText('Server unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Nothing logged yet.')).not.toBeInTheDocument()
  })
})
