import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import EndOfferConfirm from './EndOfferConfirm'

// The dialog reads its listing and nothing else -- no API, no router, no
// context -- so it is rendered bare. That is the point of it being its own
// component: the wording can be read off it directly.

// An ordinary item listing on eBay.
const ITEM = {
  id: 7,
  item_code: 'C-0007',
  item_title: '1881-S Morgan Dollar',
  sales_lot_id: null,
  member_count: null,
  venue_name: 'eBay',
  status: 'active',
  price: '189.00',
  currency: 'USD',
}

// A LOT listing on eBay: three coins offered as one thing. `item_code` is
// null -- a lot is not an item -- and `item_title` carries the lot's title.
const LOT = {
  ...ITEM,
  id: 7,
  item_code: null,
  item_title: 'Three Morgans',
  sales_lot_id: 4,
  member_count: 3,
  price: '1000.00',
}

const show = (listing) =>
  render(
    <EndOfferConfirm
      listing={listing}
      busy={false}
      onConfirm={vi.fn()}
      onCancel={vi.fn()}
    />,
  )

describe('EndOfferConfirm', () => {
  it('names the item, the listing and the platform', () => {
    show(ITEM)
    expect(
      screen.getByRole('heading', { name: 'End listing #7 for C-0007 on eBay?' }),
    ).toBeVisible()
  })

  it('names the lot and its size rather than a null item code', () => {
    // The whole reason this dialog names anything: "a person who has two
    // offers open needs to know which one this is". It asked "End listing #7
    // for null on eBay?" for every lot, which answers that question with
    // nothing at all.
    show(LOT)
    expect(
      screen.getByRole('heading', {
        name: 'End listing #7 for Three Morgans (3 items) on eBay?',
      }),
    ).toBeVisible()
    expect(screen.queryByText(/null/)).toBeNull()
  })

  it('says what is withdrawn, at what price, by name', () => {
    show(LOT)
    expect(
      screen.getByText(/Three Morgans \(3 items\) is withdrawn from eBay at 1000\.00/),
    ).toBeVisible()
  })

  it('warns that ending a lot listing dissolves the lot', () => {
    // `offering_writes._end` ends the lot with its listing and releases every
    // membership, and "a dissolved lot never comes back". Withdrawing one
    // coin from sale and breaking up a group are not the same decision, and
    // the confirmation is where the difference has to be said.
    show(LOT)
    expect(screen.getByText(/lot is dissolved with the listing/)).toBeVisible()
    expect(
      screen.getByText(/grouping the same coins again starts a new one/),
    ).toBeVisible()
  })

  it('does not tell an item it is a lot', () => {
    show(ITEM)
    expect(screen.queryByText(/dissolved/)).toBeNull()
    expect(screen.getByText(/offering the item again makes a new one/)).toBeVisible()
  })

  it('still says a paused row resumes nothing', () => {
    // The pre-existing split, kept: ending a paused store listing destroys
    // the row that was set aside, and leaves the offer that paused it alone.
    show({ ...ITEM, status: 'paused' })
    expect(screen.getByText(/that offer is not affected/)).toBeVisible()
  })
})
