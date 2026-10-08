import userEvent from '@testing-library/user-event'
import { fireEvent, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import ConsoleMenu from './ConsoleMenu'
import { renderWithProviders } from '../test/helpers'

//: Every group that opens, with its pages in the order they are listed.
const GROUPS = [
  [
    'Inventory',
    [
      ['Coins', '/inventory/coins'],
      ['Currency', '/inventory/currency'],
      ['Photos', '/photos'],
      ['Order lookup', '/order-lookup'],
    ],
  ],
  [
    // Buying, in the order it is done: entered, then received.
    'Procurement',
    [
      ['Purchases', '/purchases'],
      ['Receive', '/receiving'],
    ],
  ],
  [
    'Selling',
    [
      ['Listings', '/listings'],
      ['Lots', '/lots'],
      ['Auctions', '/auctions'],
      ['Sales', '/sales'],
      ['Platforms', '/platforms'],
      ['Spot prices', '/spot-prices'],
    ],
  ],
  [
    'Settings',
    [
      ['People', '/people'],
      ['Vocabularies', '/vocabularies'],
      ['Lists', '/lists'],
    ],
  ],
]

function open(route = '/nowhere') {
  renderWithProviders(<ConsoleMenu />, { route })
  return userEvent.setup()
}

const menu = () => screen.getByRole('navigation', { name: 'Console' })
const group = (name) => screen.getByRole('button', { name: new RegExp(`^${name}`) })
const list = (name) => screen.queryByRole('group', { name })
const pages = (name) =>
  within(list(name))
    .getAllByRole('link')
    .map((link) => [link.textContent, link.getAttribute('href')])

describe('ConsoleMenu', () => {
  it('shows one row of group names, every list closed', () => {
    open()
    expect(
      within(menu())
        .getAllByRole('button')
        .map((button) => button.textContent.replace(/\s*▾$/, '')),
    ).toEqual(['Inventory', 'Procurement', 'Selling', 'Settings'])
    for (const button of within(menu()).getAllByRole('button')) {
      expect(button).toHaveAttribute('aria-expanded', 'false')
    }
    // A group of one page is that page's link, with no list to open.
    const links = within(menu()).getAllByRole('link')
    expect(links.map((link) => [link.textContent, link.getAttribute('href')])).toEqual([
      ['Reports', '/reports'],
    ])
    expect(screen.queryByRole('group')).toBeNull()
  })

  it.each(GROUPS)('opens %s onto its pages, in order', async (name, expected) => {
    const user = open()
    await user.click(group(name))
    expect(group(name)).toHaveAttribute('aria-expanded', 'true')
    expect(pages(name)).toEqual(expected)
  })

  it('holds every page once across the whole menu', async () => {
    const user = open()
    const hrefs = ['/reports']
    for (const [name] of GROUPS) {
      await user.click(group(name))
      hrefs.push(...pages(name).map(([, href]) => href))
    }
    expect(hrefs).toHaveLength(16)
    expect(new Set(hrefs).size).toBe(16)
  })

  it('keeps one list open at a time', async () => {
    const user = open()
    await user.click(group('Inventory'))
    await user.click(group('Selling'))
    expect(list('Inventory')).toBeNull()
    expect(list('Selling')).toBeVisible()
    expect(group('Inventory')).toHaveAttribute('aria-expanded', 'false')
  })

  it('closes an open list when its button is clicked again', async () => {
    const user = open()
    await user.click(group('Selling'))
    await user.click(group('Selling'))
    expect(list('Selling')).toBeNull()
  })

  it('closes the list once a page is chosen, and marks that page', async () => {
    const user = open()
    await user.click(group('Selling'))
    await user.click(screen.getByRole('link', { name: 'Lots' }))
    expect(list('Selling')).toBeNull()

    // The closed menu still says where the console is: the group is marked.
    expect(group('Selling')).toHaveClass('current')
    expect(group('Inventory')).not.toHaveClass('current')
    await user.click(group('Selling'))
    expect(screen.getByRole('link', { name: 'Lots' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(screen.getByRole('link', { name: 'Auctions' })).not.toHaveAttribute(
      'aria-current',
    )
  })

  it.each([
    ['/inventory/coins', 'Inventory'],
    ['/order-lookup', 'Inventory'],
    ['/receiving', 'Procurement'],
    ['/spot-prices', 'Selling'],
    ['/lists', 'Settings'],
  ])('marks the group that %s belongs to, and no other', (route, name) => {
    open(route)
    const marked = within(menu())
      .getAllByRole('button')
      .filter((button) => button.classList.contains('current'))
    expect(marked).toEqual([group(name)])
  })

  it('marks no group on the Reports page, whose link is marked itself', () => {
    open('/reports')
    expect(screen.getByRole('link', { name: 'Reports' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    for (const button of within(menu()).getAllByRole('button')) {
      expect(button).not.toHaveClass('current')
    }
  })

  it('closes on Escape and returns to the button', async () => {
    const user = open()
    await user.click(group('Settings'))
    screen.getByRole('link', { name: 'People' }).focus()
    await user.keyboard('{Escape}')
    expect(list('Settings')).toBeNull()
    expect(group('Settings')).toHaveFocus()
  })

  it('closes on a click anywhere outside the menu', async () => {
    const user = open()
    await user.click(group('Inventory'))
    // A mouse press on the page, which takes no focus from anything.
    fireEvent.mouseDown(document.body)
    expect(list('Inventory')).toBeNull()

    // A press inside the open list is not outside it.
    await user.click(group('Inventory'))
    fireEvent.mouseDown(list('Inventory'))
    expect(list('Inventory')).toBeVisible()
  })

  it('closes when the keyboard tabs on past its last page', async () => {
    const user = open()
    await user.click(group('Procurement'))
    await user.tab()
    await user.tab()
    expect(screen.getByRole('link', { name: 'Receive' })).toHaveFocus()
    expect(list('Procurement')).toBeVisible()
    await user.tab()
    expect(list('Procurement')).toBeNull()
  })

  it('opens from the keyboard onto its first page, and the arrows move through it', async () => {
    const user = open()
    group('Procurement').focus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('link', { name: 'Purchases' })).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('link', { name: 'Receive' })).toHaveFocus()
    // Past the last page is the first again, and back the other way.
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('link', { name: 'Purchases' })).toHaveFocus()
    await user.keyboard('{ArrowUp}')
    expect(screen.getByRole('link', { name: 'Receive' })).toHaveFocus()
  })
})
