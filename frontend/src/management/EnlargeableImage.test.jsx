import { fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import EnlargeableImage from './EnlargeableImage'
import { renderWithProviders } from '../test/helpers'

const THUMB = '/api/images/abc/thumb'
const LARGE = '/api/images/abc/web'

function open() {
  renderWithProviders(<EnlargeableImage src={THUMB} largeSrc={LARGE} alt="Obverse" />)
}

const dialog = () => screen.queryByRole('dialog', { name: 'Obverse', hidden: true })

describe('EnlargeableImage', () => {
  it('shows the small picture and no dialog until it is clicked', () => {
    open()
    expect(screen.getByRole('img', { name: 'Obverse' })).toHaveAttribute('src', THUMB)
    expect(dialog()).toBeNull()
  })

  it('opens the large rendition in a dialog when the picture is clicked', async () => {
    const user = userEvent.setup()
    open()
    await user.click(screen.getByRole('button', { name: 'Enlarge Obverse' }))
    const large = within(dialog()).getByRole('img', { name: 'Obverse', hidden: true })
    // The large rendition, not the thumbnail scaled up.
    expect(large).toHaveAttribute('src', LARGE)
  })

  it('closes on Cancel', async () => {
    const user = userEvent.setup()
    open()
    await user.click(screen.getByRole('button', { name: 'Enlarge Obverse' }))
    await user.click(
      within(dialog()).getByRole('button', { name: 'Cancel', hidden: true }),
    )
    expect(dialog()).toBeNull()
  })

  it('closes on Escape', async () => {
    const user = userEvent.setup()
    open()
    await user.click(screen.getByRole('button', { name: 'Enlarge Obverse' }))
    // Escape reaches a native dialog as its `cancel` event.
    fireEvent(dialog(), new Event('cancel', { cancelable: true }))
    expect(dialog()).toBeNull()
  })

  it('links to where the photograph was fetched from, when that is known', async () => {
    const user = userEvent.setup()
    const from = 'https://i.ebayimg.com/images/g/abc/s-l500.jpg'
    renderWithProviders(
      <EnlargeableImage src={THUMB} largeSrc={LARGE} alt="Obverse" sourceUrl={from} />,
    )
    await user.click(screen.getByRole('button', { name: 'Enlarge Obverse' }))
    const link = within(dialog()).getByRole('link', {
      name: 'Where it came from',
      hidden: true,
    })
    expect(link).toHaveAttribute('href', from)
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('offers no such link for a photograph uploaded from a file', async () => {
    const user = userEvent.setup()
    open()
    await user.click(screen.getByRole('button', { name: 'Enlarge Obverse' }))
    expect(within(dialog()).queryByRole('link', { hidden: true })).toBeNull()
  })

  it('is a plain picture when there is no large rendition to show', () => {
    renderWithProviders(<EnlargeableImage src={THUMB} alt="Obverse" />)
    expect(screen.getByRole('img', { name: 'Obverse' })).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
