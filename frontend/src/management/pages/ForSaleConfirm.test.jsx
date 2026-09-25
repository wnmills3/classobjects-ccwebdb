import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import ForSaleConfirm from './ForSaleConfirm'

describe('ForSaleConfirm', () => {
  it('names the items and says the listing ends', () => {
    render(
      <ForSaleConfirm
        detail="For sale -- CC-000123: listing #3 at 120.00."
        outcome="missing"
        busy={false}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.getByRole('dialog')).toHaveTextContent('CC-000123')
    expect(screen.getByRole('dialog')).toHaveTextContent('withdrawn from sale')
  })

  it('reports a confirmation', async () => {
    const onConfirm = vi.fn()
    render(
      <ForSaleConfirm
        detail="For sale -- CC-000123: listing #3."
        outcome="missing"
        busy={false}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /record it anyway/i }))
    expect(onConfirm).toHaveBeenCalled()
  })
})
