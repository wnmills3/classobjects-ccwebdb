import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({ api: { listOrderChanges: vi.fn() } }))

import { api } from '../../api'
import OrderHistory from './OrderHistory'
import { describeChange } from './describeChange'

const at = '2026-09-15T14:40:00Z'
const row = (id, change, extra = {}) => ({
  id,
  change,
  changed_at: at,
  changed_by_email: 'admin@example.com',
  listing_id: 3,
  listing_title: 'Morgan',
  from_value: null,
  to_value: null,
  ...extra,
})

describe('OrderHistory', () => {
  it('describes each kind of change', () => {
    expect(describeChange(row(1, 'quantity', { from_value: '2', to_value: '3' }))).toBe(
      'Morgan quantity 2 -> 3',
    )
    expect(
      describeChange(
        row(2, 'unit_price', { from_value: '189.00', to_value: '150.00' }),
      ),
    ).toBe('Morgan price $189.00 -> $150.00')
    expect(describeChange(row(3, 'line_added', { to_value: '1 @ 10.00' }))).toBe(
      'added Morgan: 1 @ 10.00',
    )
    expect(describeChange(row(4, 'line_removed', { from_value: '2 @ 189.00' }))).toBe(
      'removed Morgan (was 2 @ 189.00)',
    )
    expect(
      describeChange(
        row(5, 'total', {
          listing_title: null,
          from_value: '378.00',
          to_value: '450.00',
        }),
      ),
    ).toBe('total $378.00 -> $450.00')
    expect(describeChange(row(6, 'placed', { to_value: 'admin@example.com' }))).toBe(
      'placed by admin@example.com',
    )
  })

  it('groups one save into one entry', async () => {
    api.listOrderChanges.mockResolvedValue([
      row(3, 'total', { from_value: '378.00', to_value: '567.00' }),
      row(2, 'quantity', { from_value: '2', to_value: '3' }),
      row(1, 'placed', {
        changed_at: '2026-09-14T10:00:00Z',
        to_value: 'customer@example.com',
      }),
    ])
    render(<OrderHistory order={{ id: 12 }} onClose={vi.fn()} />)
    const entries = await screen.findAllByRole('listitem')
    expect(entries).toHaveLength(2)
    expect(entries[0]).toHaveTextContent(
      'Morgan quantity 2 -> 3; total $378.00 -> $567.00',
    )
  })
})
