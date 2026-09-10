import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: { receiveItems: vi.fn(), listStorageLocations: vi.fn() },
}))

import { api } from '../../api'
import ReceiptPanel from './ReceiptPanel'
import { renderWithProviders } from '../../../test/helpers'

const LOCATIONS = [{ id: 3, label: 'Safe deposit box', kind: 'safe_deposit_box' }]

beforeEach(() => {
  vi.clearAllMocks()
  api.listStorageLocations.mockResolvedValue(LOCATIONS)
  api.receiveItems.mockResolvedValue({ received: 2 })
})

describe('ReceiptPanel', () => {
  it('sends one request for several items, not one each', async () => {
    // A box of twenty coins is one transaction. Twenty requests would leave a
    // partial state nobody can describe if the tenth failed.
    renderWithProviders(<ReceiptPanel itemIds={[412, 413]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalledTimes(1))
    expect(api.receiveItems.mock.calls[0][0].item_ids).toEqual([412, 413])
  })

  it('sends the outcome the pressed button names', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /missing/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.receiveItems.mock.calls[0][0].outcome).toBe('missing')
  })

  it('defaults the arrival date to today but lets it be changed', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    const field = await screen.findByLabelText(/arrived/i)
    expect(field).toHaveValue(new Date().toISOString().slice(0, 10))

    await userEvent.clear(field)
    await userEvent.type(field, '2026-09-04')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.receiveItems.mock.calls[0][0].arrived_on).toBe('2026-09-04')
  })

  it('reports a refused receipt without clearing what was typed', async () => {
    // A 409 means the wrong row, or a double submit. Making the operator
    // retype the note they just wrote turns a recoverable mistake into a
    // reason to skip the note next time.
    api.receiveItems.mockRejectedValue(
      Object.assign(new Error('Already received: [CC-000412]'), { status: 409 }),
    )
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)

    const note = await screen.findByLabelText(/note/i)
    await userEvent.type(note, 'edge knock')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    expect(await screen.findByText(/already received/i)).toBeInTheDocument()
    expect(note).toHaveValue('edge knock')
  })

  it('does nothing at all when no item is selected', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[]} onDone={vi.fn()} />)
    expect(await screen.findByRole('button', { name: /^receive$/i })).toBeDisabled()
  })
})
