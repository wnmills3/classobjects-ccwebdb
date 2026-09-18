import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import ForSaleNotice from './ForSaleNotice'

describe('ForSaleNotice', () => {
  it('renders nothing when the item is not for sale', () => {
    const { container } = render(
      <ForSaleNotice uses={[]} checked={false} onChange={vi.fn()} action="Anyway" />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows on request with no reasons to list, for a multi-item caller', () => {
    render(
      <ForSaleNotice
        show
        heading="Some of the selected items are for sale"
        checked={false}
        onChange={vi.fn()}
        action="Anyway"
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Some of the selected items are for sale',
    )
  })

  it('names every reason the item is for sale', () => {
    render(
      <ForSaleNotice
        uses={[
          { kind: 'listing', id: 3, text: 'listing #3 at 120.00' },
          { kind: 'order', id: 7, text: 'order #7 (paid)' },
        ]}
        checked={false}
        onChange={vi.fn()}
        action="Anyway"
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('listing #3 at 120.00')
    expect(screen.getByRole('alert')).toHaveTextContent('order #7 (paid)')
  })

  it('reports a tick to its caller', async () => {
    const onChange = vi.fn()
    render(
      <ForSaleNotice
        uses={[{ kind: 'listing', id: 3, text: 'listing #3' }]}
        checked={false}
        onChange={onChange}
        action="Change it anyway"
      />,
    )
    await userEvent.click(screen.getByLabelText('Change it anyway'))
    expect(onChange).toHaveBeenCalledWith(true)
  })
})
