import userEvent from '@testing-library/user-event'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: { bulkEditInventory: vi.fn() },
}))

import { api } from '../../api'
import BulkEditBar from './BulkEditBar'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('BulkEditBar', () => {
  it('offers to change items for sale only once the server names them', async () => {
    const user = userEvent.setup()
    const onApplied = vi.fn()
    api.bulkEditInventory
      .mockRejectedValueOnce(
        new Error('For sale -- CC-000001: listing #3 at 189.00. A change shows ...'),
      )
      .mockResolvedValueOnce({ updated: 2 })
    render(<BulkEditBar ids={[1, 2]} onApplied={onApplied} onClear={vi.fn()} />)

    expect(screen.queryByRole('checkbox')).toBeNull()
    await user.type(screen.getByPlaceholderText('New value'), '1964')
    await user.click(screen.getByRole('button', { name: 'Apply to 2' }))
    expect(api.bulkEditInventory).toHaveBeenLastCalledWith([1, 2], { year_start: 1964 })
    expect(await screen.findByText(/CC-000001/)).toBeVisible()

    await user.click(
      screen.getByRole('checkbox', { name: 'Change the items for sale too' }),
    )
    await user.click(screen.getByRole('button', { name: 'Apply to 2' }))
    expect(api.bulkEditInventory).toHaveBeenLastCalledWith([1, 2], {
      year_start: 1964,
      acknowledge_for_sale: true,
    })
    expect(onApplied).toHaveBeenCalled()
    expect(screen.queryByRole('checkbox')).toBeNull()
  })

  it('does not offer it for another refusal', async () => {
    const user = userEvent.setup()
    api.bulkEditInventory.mockRejectedValueOnce(new Error('Unknown grade: zz'))
    render(<BulkEditBar ids={[1]} onApplied={vi.fn()} onClear={vi.fn()} />)
    await user.type(screen.getByPlaceholderText('New value'), '1964')
    await user.click(screen.getByRole('button', { name: 'Apply to 1' }))
    expect(await screen.findByText('Unknown grade: zz')).toBeVisible()
    expect(screen.queryByRole('checkbox')).toBeNull()
  })
})
