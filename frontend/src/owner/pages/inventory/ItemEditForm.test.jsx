import userEvent from '@testing-library/user-event'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getInventoryItem: vi.fn(),
    getItemSales: vi.fn(),
    getItemHistory: vi.fn(),
    updateInventoryItem: vi.fn(),
    setItemReview: vi.fn(),
    getItemErrors: vi.fn(),
    setItemErrors: vi.fn(),
    // OffersPanel's own calls: it reads the item's offers on mount, and its
    // Offer button opens the dialog that starts one.
    listListings: vi.fn(),
    endListing: vi.fn(),
    listSalesVenues: vi.fn(),
    getOfferTitles: vi.fn(),
    createOffers: vi.fn(),
    // PhotosPanel's own calls: it reads the item's photographs on mount.
    listItemImages: vi.fn(),
    uploadImage: vi.fn(),
    updateImageLink: vi.fn(),
    detachImage: vi.fn(),
  },
}))

// ReferenceSelect's "add a value" posts through shared/api.js, not owner/api.js
// -- see the module boundary note in reference.jsx -- so it needs its own mock.
vi.mock('../../../shared/api', () => ({
  api: { addReferenceValue: vi.fn() },
}))

import { api } from '../../api'
import { api as sharedApi } from '../../../shared/api'
import { emptyReference, renderWithProviders } from '../../../test/helpers'
import ItemEditForm from './ItemEditForm'

const item = {
  id: 12,
  item_code: 'C-012',
  description: 'Mercury Dime',
  reviewed: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getOfferTitles.mockResolvedValue({ titles: {} })
  api.getItemSales.mockResolvedValue([])
  api.getItemHistory.mockResolvedValue([])
  api.listListings.mockResolvedValue([])
  // The offers panel reads the platforms too, to tell the shop from the rest.
  api.listSalesVenues.mockResolvedValue([])
  api.getInventoryItem.mockResolvedValue(item)
  api.setItemReview.mockResolvedValue({ reviewed: ['description'] })
  api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
  api.listItemImages.mockResolvedValue([])
})

describe('ItemEditForm', () => {
  it('loads the item it was given', async () => {
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    expect(api.getInventoryItem).toHaveBeenCalledWith(12)
  })

  it('offers a confirmed checkbox per reviewable field', async () => {
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    expect(screen.getAllByLabelText(/confirmed/i).length).toBeGreaterThan(0)
  })

  it('records a field as reviewed when its box is ticked', async () => {
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')

    // By role rather than by label: the label's title also contains
    // "confirmed", so a text query can return the label instead of the input.
    // By name as well: "Range of years" is a checkbox too, ahead of these.
    const box = screen.getAllByRole('checkbox', { name: /confirmed/ })[0]
    expect(box).not.toBeChecked()
    await user.click(box)

    await waitFor(() =>
      // replace:true -- unticking must remove the record, not leave a
      // confirmation nobody stands behind.
      expect(api.setItemReview).toHaveBeenCalledWith(12, expect.any(Array), true),
    )
  })

  it('reports a failed load rather than showing an empty form', async () => {
    api.getInventoryItem.mockRejectedValue(new Error('item 12 is gone'))
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    expect(await screen.findByText('item 12 is gone')).toBeInTheDocument()
  })
})

describe('Changes made elsewhere while the form is open', () => {
  // The owner's request (2026-09-23): warn and refresh, field by field --
  // a change elsewhere to a field not being edited here is simply taken in.
  const opened = {
    ...item,
    version: 1,
    source_title: 'Dime',
    description: 'Mercury Dime',
  }

  async function openAndEditDescription(user) {
    api.getInventoryItem.mockResolvedValueOnce(opened)
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    const description = await screen.findByDisplayValue('Mercury Dime')
    await user.clear(description)
    await user.type(description, 'Winged Liberty dime')
  }

  async function somebodyElseSaves(changes) {
    api.getInventoryItem.mockResolvedValue({ ...opened, version: 2, ...changes })
    // What the form does when the window gets focus back.
    fireEvent.focus(window)
    await waitFor(() => expect(api.getInventoryItem).toHaveBeenCalledTimes(2))
  }

  it('takes in a change to another field without a word of conflict', async () => {
    const user = userEvent.setup()
    await openAndEditDescription(user)
    await somebodyElseSaves({ source_title: 'Mercury Dime 1943' })

    expect(await screen.findByDisplayValue('Mercury Dime 1943')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Winged Liberty dime')).toBeInTheDocument()
    expect(screen.queryByText(/changed elsewhere while you were editing/i)).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent(
      /updated with changes made elsewhere/i,
    )
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('stops to ask when the same field was changed, and saves mine on Keep mine', async () => {
    const user = userEvent.setup()
    api.updateInventoryItem.mockResolvedValue({})
    await openAndEditDescription(user)
    await somebodyElseSaves({ description: 'Mercury dime, cleaned' })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      'Description: now Mercury dime, cleaned; yours Winged Liberty dime',
    )
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Keep mine' }))
    expect(screen.queryByText(/changed elsewhere while you were editing/i)).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    // Based on their value now, so the server takes mine knowingly.
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(12, {
        description: 'Winged Liberty dime',
        version: 2,
        base: { description: 'Mercury dime, cleaned' },
      }),
    )
  })

  it('says who made the other change, from the change log', async () => {
    const user = userEvent.setup()
    await openAndEditDescription(user)
    await somebodyElseSaves({
      description: 'Mercury dime, cleaned',
      last_changes: {
        description: { by: 'Pat Buyer', at: '2026-09-23T21:41:00Z' },
      },
    })
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(
      /now Mercury dime, cleaned, changed by Pat Buyer at /,
    )
    expect(alert).toHaveTextContent(/; yours Winged Liberty dime/)
  })

  it('drops my edit on Use theirs', async () => {
    const user = userEvent.setup()
    await openAndEditDescription(user)
    await somebodyElseSaves({ description: 'Mercury dime, cleaned' })

    await user.click(await screen.findByRole('button', { name: 'Use theirs' }))
    expect(screen.getByDisplayValue('Mercury dime, cleaned')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled() // nothing left to save
  })

  it('shows the conflict when the server finds it at save time', async () => {
    const user = userEvent.setup()
    await openAndEditDescription(user)
    api.updateInventoryItem.mockRejectedValue(
      Object.assign(new Error('Dime was changed by someone else'), {
        status: 409,
        body: {
          detail: 'Dime was changed by someone else',
          conflicts: [{ field: 'description' }],
        },
      }),
    )
    api.getInventoryItem.mockResolvedValue({
      ...opened,
      version: 2,
      description: 'cleaned',
    })

    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(
      await screen.findByText(/changed elsewhere while you were editing/i),
    ).toBeInTheDocument()
  })
})

describe('No sales tax charged', () => {
  const taxed = {
    ...item,
    version: 3,
    item_cost: '179.00',
    shipping_cost: '0.00',
    sales_tax: '11.37',
    total_cost: '190.37',
    tax_rate: '0.0635',
    tax_includes_shipping: true,
    default_tax_rate: '0.0635',
  }
  const untaxed = {
    ...taxed,
    sales_tax: '0.00',
    total_cost: '179.00',
    tax_rate: '0.0000',
  }

  async function saveAfterClicking(loaded) {
    const user = userEvent.setup()
    api.getInventoryItem.mockResolvedValue(loaded)
    api.updateInventoryItem.mockResolvedValue({})
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await user.click(
      await screen.findByRole('checkbox', { name: /no sales tax charged/i }),
    )
    await user.click(screen.getByRole('button', { name: /save/i }))
  }

  it('sets the rate to zero when ticked', async () => {
    await saveAfterClicking(taxed)
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ tax_rate: '0', version: 3 }),
      ),
    )
  })

  it('shows an item already recorded as untaxed as ticked', async () => {
    api.getInventoryItem.mockResolvedValue(untaxed)
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    expect(
      await screen.findByRole('checkbox', { name: /no sales tax charged/i }),
    ).toBeChecked()
  })

  it('ticking and unticking again leaves the rate as it was', async () => {
    const user = userEvent.setup()
    // Stamped at 6.35% when bought; the setting has since moved to 7%.
    api.getInventoryItem.mockResolvedValue({ ...taxed, default_tax_rate: '0.0700' })
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    const box = await screen.findByRole('checkbox', { name: /no sales tax charged/i })

    await user.click(box)
    await user.click(box)

    // Back to the rate it was bought at, not today's setting -- so nothing
    // changed and there is nothing to save.
    expect(screen.getByRole('button', { name: /save/i })).toBeDisabled()
  })

  it('restores the configured rate when unticked, not a rate of its own', async () => {
    await saveAfterClicking({ ...untaxed, default_tax_rate: '0.0700' })
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ tax_rate: '0.0700' }),
      ),
    )
  })
})

describe('Grading: designation, service and certificate', () => {
  const designation = (code, applies_to) => ({
    code,
    label: code,
    source: 'seeded',
    extra: { applies_to },
  })
  const vocabularies = emptyReference({
    tables: {
      grade_designation: [
        designation('DCAM', 'coin'),
        designation('FBL', 'coin'),
        designation('EPQ', 'currency'),
        designation('PPQ', 'currency'),
      ],
      grading_service: [
        { code: 'PCGS', label: 'PCGS', source: 'seeded', extra: {} },
        { code: 'PMG', label: 'PMG', source: 'seeded', extra: {} },
      ],
    },
  })

  async function open(overrides) {
    api.getInventoryItem.mockResolvedValue({ ...item, version: 3, ...overrides })
    api.updateInventoryItem.mockResolvedValue({})
    renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      { reference: vocabularies },
    )
    await screen.findByDisplayValue('Mercury Dime')
  }

  const optionsOf = (name) =>
    Array.from(screen.getByRole('combobox', { name }).querySelectorAll('option')).map(
      (o) => o.textContent,
    )

  it('offers a note only paper designations, and a coin only strike ones', async () => {
    await open({ item_kind: 'currency' })
    expect(optionsOf('grade_designation')).toEqual(
      expect.arrayContaining(['EPQ', 'PPQ']),
    )
    expect(optionsOf('grade_designation')).not.toContain('DCAM')
  })

  it('offers a coin only strike designations', async () => {
    await open({ item_kind: 'coin' })
    expect(optionsOf('grade_designation')).toEqual(
      expect.arrayContaining(['DCAM', 'FBL']),
    )
    expect(optionsOf('grade_designation')).not.toContain('EPQ')
  })

  it('saves a designation, a grading service and certificate numbers', async () => {
    const user = userEvent.setup()
    await open({ item_kind: 'currency', cert_numbers: ['111'] })

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'grade_designation' }),
      'EPQ',
    )
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'grading_service' }),
      'PMG',
    )
    const certs = screen.getByDisplayValue('111')
    await user.type(certs, ', 8061234-005,')
    expect(certs).toHaveValue('111, 8061234-005, ')
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({
          grade_designation: 'EPQ',
          grading_service: 'PMG',
          // The trailing comma's empty entry is not sent.
          cert_numbers: ['111', '8061234-005'],
          base: expect.objectContaining({ cert_numbers: ['111'] }),
        }),
      ),
    )
  })

  it('clears a coin designation when the item becomes a note', async () => {
    const user = userEvent.setup()
    const kinds = emptyReference({
      tables: {
        ...vocabularies.tables,
        item_kind: [
          { code: 'coin', label: 'Coin', source: 'seeded', extra: {} },
          { code: 'currency', label: 'Currency', source: 'seeded', extra: {} },
        ],
      },
    })
    api.getInventoryItem.mockResolvedValue({
      ...item,
      version: 3,
      item_kind: 'coin',
      grade_designation: 'DCAM',
    })
    renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      { reference: kinds },
    )
    await screen.findByDisplayValue('Mercury Dime')
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'item_kind' }),
      'currency',
    )
    expect(screen.getByRole('status')).toHaveTextContent('Grade designation (DCAM)')
  })
})

describe('Grade choices', () => {
  // One grade from each scale, as the reference context holds them.
  const vocabularies = emptyReference({
    tables: {
      grade: [
        {
          code: 'MS65',
          label: 'MS-65',
          source: 'seeded',
          extra: { grade_scale: 'sheldon' },
        },
        {
          code: 'UNC',
          label: 'UNC',
          source: 'seeded',
          extra: { grade_scale: 'adjectival' },
        },
        {
          code: 'N64',
          label: 'Choice Uncirculated 64',
          source: 'seeded',
          extra: { grade_scale: 'note' },
        },
      ],
      strike_type: [
        { code: 'business', label: 'Business Strike', source: 'seeded', extra: {} },
        { code: 'proof', label: 'Proof', source: 'seeded', extra: {} },
      ],
      // Present so the metal picker renders as a dropdown: with no values
      // `ReferenceSelect` falls back to a plain input, and a test asking for
      // a combobox would pass for a note whether or not the field was shown.
      metal: [
        { code: 'silver', label: 'Silver', source: 'seeded', extra: {} },
        { code: 'gold', label: 'Gold', source: 'seeded', extra: {} },
      ],
    },
  })

  async function gradeOptions(kind) {
    api.getInventoryItem.mockResolvedValue({ ...item, item_kind: kind })
    renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      {
        reference: vocabularies,
      },
    )
    await screen.findByDisplayValue('Mercury Dime')
    const select = screen.getByRole('combobox', { name: 'grade' })
    return Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
  }

  it('offers a note only paper-money grades', async () => {
    const options = await gradeOptions('currency')
    expect(options).toContain('Choice Uncirculated 64')
    expect(options).not.toContain('MS-65')
    expect(options).not.toContain('UNC')
  })

  it('offers a coin only coin grades', async () => {
    const options = await gradeOptions('coin')
    expect(options).toContain('MS-65')
    expect(options).toContain('UNC')
    expect(options).not.toContain('Choice Uncirculated 64')
  })

  // 65 is MS65 or PR65 by its strike type; a note has none to choose.
  it('asks a coin for its strike type', async () => {
    await gradeOptions('coin')
    expect(screen.getByRole('combobox', { name: 'strike_type' })).toBeInTheDocument()
  })

  it('does not ask a note for a strike type', async () => {
    await gradeOptions('currency')
    expect(screen.queryByRole('combobox', { name: 'strike_type' })).toBeNull()
  })

  // Paper has no metal. The field belongs to the coin view on the API too:
  // `metal` is a coin-view column and filter, absent from the currency view.
  it('asks a coin for its metal', async () => {
    await gradeOptions('coin')
    expect(screen.getByRole('combobox', { name: 'metal' })).toBeInTheDocument()
  })

  it('does not ask a note for a metal', async () => {
    await gradeOptions('currency')
    expect(screen.queryByRole('combobox', { name: 'metal' })).toBeNull()
  })
})

describe('Denomination choices', () => {
  // One denomination from each side, as the reference context holds them.
  const vocabularies = emptyReference({
    tables: {
      denomination: [
        {
          code: 'usd_note_1_00',
          label: '$1 Bill',
          source: 'seeded',
          extra: { kind: 'note' },
        },
        {
          code: 'usd_coin_0_25',
          label: 'Quarter',
          source: 'seeded',
          extra: { kind: 'coin' },
        },
      ],
    },
  })

  async function denominationOptions(kind) {
    api.getInventoryItem.mockResolvedValue({ ...item, item_kind: kind })
    renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      {
        reference: vocabularies,
      },
    )
    await screen.findByDisplayValue('Mercury Dime')
    const select = screen.getByRole('combobox', { name: 'denomination' })
    return Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
  }

  it("offers a note's denomination picker $1 Bill and not Quarter", async () => {
    const options = await denominationOptions('currency')
    expect(options).toContain('$1 Bill')
    expect(options).not.toContain('Quarter')
  })

  it("offers a coin's denomination picker Quarter and not $1 Bill", async () => {
    const options = await denominationOptions('coin')
    expect(options).toContain('Quarter')
    expect(options).not.toContain('$1 Bill')
  })
})

describe('Changing the kind', () => {
  const vocabularies = emptyReference({
    tables: {
      item_kind: [
        { code: 'coin', label: 'Coin', source: 'seeded', extra: {} },
        { code: 'currency', label: 'Currency', source: 'seeded', extra: {} },
      ],
      metal: [{ code: 'silver', label: 'Silver', source: 'seeded', extra: {} }],
      denomination: [
        {
          code: 'usd_coin_1_00',
          label: 'Dollar',
          source: 'seeded',
          extra: { kind: 'coin' },
        },
        {
          code: 'usd_note_1',
          label: '$1 Bill',
          source: 'seeded',
          extra: { kind: 'note' },
        },
      ],
    },
  })

  function open(fields) {
    api.getInventoryItem.mockResolvedValue({ ...item, ...fields })
    api.updateInventoryItem.mockResolvedValue({})
    renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      { reference: vocabularies },
    )
    return screen.findByDisplayValue('Mercury Dime')
  }

  const kindBox = () => screen.getByRole('combobox', { name: 'item_kind' })

  it("shows a note's fields at once, and saves the coin's metal cleared", async () => {
    const user = userEvent.setup()
    await open({ item_kind: 'coin', metal: 'silver', denomination: 'usd_coin_1_00' })
    expect(screen.queryByRole('textbox', { name: 'Serial number' })).toBeNull()

    await user.selectOptions(kindBox(), 'currency')

    // Before any save: the note's fields are here, the coin's metal is not,
    // and the notice names what the note cannot keep.
    expect(screen.getByRole('textbox', { name: 'Serial number' })).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'metal' })).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('silver')

    await user.type(
      screen.getByRole('textbox', { name: 'Serial number' }),
      'F06566560R',
    )
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
    const [, payload] = api.updateInventoryItem.mock.calls[0]
    expect(payload).toMatchObject({
      item_kind: 'currency',
      metal: null,
      denomination: null,
      serial_number: 'F06566560R',
    })
  })

  it('puts everything back when the kind is changed back', async () => {
    const user = userEvent.setup()
    await open({ item_kind: 'coin', metal: 'silver' })

    await user.selectOptions(kindBox(), 'currency')
    await user.selectOptions(kindBox(), 'coin')

    expect(screen.getByRole('combobox', { name: 'metal' })).toHaveValue('silver')
    expect(screen.queryByRole('status')).toBeNull()
    // Nothing left to save: the round trip is no change at all.
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it("clears a note's serial number when it stops being a note, and says so", async () => {
    const user = userEvent.setup()
    await open({ item_kind: 'currency', serial_number: 'F06566560R' })

    await user.selectOptions(kindBox(), 'coin')

    expect(screen.getByRole('status')).toHaveTextContent('F06566560R')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
    const [, payload] = api.updateInventoryItem.mock.calls[0]
    expect(payload).toMatchObject({ item_kind: 'coin', serial_number: null })
  })
})

describe('ItemEditForm years', () => {
  // A single year is stored as start == end; a range is for a multi-year set
  // or a coin dated only to an era. 13 items in the collection have a range,
  // so the form asks for one year unless the item has, or is given, a range.
  async function open(years) {
    api.getInventoryItem.mockResolvedValue({ ...item, ...years })
    api.updateInventoryItem.mockResolvedValue({})
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    return user
  }

  const rangeBox = () => screen.getByRole('checkbox', { name: 'Range of years' })

  it('asks for one year when the item has one', async () => {
    await open({ year_start: 1878, year_end: 1878 })
    expect(screen.getByRole('spinbutton', { name: 'Year' })).toHaveValue(1878)
    expect(screen.queryByRole('spinbutton', { name: 'Year to' })).toBeNull()
    expect(rangeBox()).not.toBeChecked()
  })

  it('treats a start year with no end as one year', async () => {
    await open({ year_start: 1881, year_end: null })
    expect(screen.getByRole('spinbutton', { name: 'Year' })).toHaveValue(1881)
    expect(rangeBox()).not.toBeChecked()
  })

  it('opens a stored range as a range, so it cannot be collapsed unseen', async () => {
    await open({ year_start: 1999, year_end: 2008 })
    expect(rangeBox()).toBeChecked()
    expect(screen.getByRole('spinbutton', { name: 'Year from' })).toHaveValue(1999)
    expect(screen.getByRole('spinbutton', { name: 'Year to' })).toHaveValue(2008)
  })

  it('saves one year as both ends', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    const year = screen.getByRole('spinbutton', { name: 'Year' })
    await user.clear(year)
    await user.type(year, '1964')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ year_start: '1964', year_end: '1964' }),
      ),
    )
  })

  it('clears both ends when the one year is emptied', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    const year = screen.getByRole('spinbutton', { name: 'Year' })
    await user.clear(year)
    // Empty, not the stored year shown back: a cleared draft is null.
    expect(year).toHaveValue(null)
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ year_start: null, year_end: null }),
      ),
    )
  })

  it('reveals the end year when Range of years is ticked', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    await user.click(rangeBox())
    const to = screen.getByRole('spinbutton', { name: 'Year to' })
    expect(to).toHaveValue(1878)
    await user.clear(to)
    await user.type(to, '1885')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ year_end: '1885' }),
      ),
    )
  })

  it('collapses a stored range to its start year when unticked', async () => {
    const user = await open({ year_start: 1999, year_end: 2008 })
    await user.click(rangeBox())
    expect(screen.queryByRole('spinbutton', { name: 'Year to' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ year_end: 1999 }),
      ),
    )
  })

  it('keeps focus on Range of years while the end year comes and goes', async () => {
    // Found in a real browser: the two layouts were separate branches, so
    // ticking replaced the checkbox itself and a keyboard user lost their place.
    const user = await open({ year_start: 1878, year_end: 1878 })
    const box = rangeBox()
    await user.click(box)
    expect(rangeBox()).toBe(box)
    expect(document.activeElement).toBe(box)
    await user.click(box)
    expect(rangeBox()).toBe(box)
  })

  it('ticking and unticking on a single year is no change at all', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    await user.click(rangeBox())
    await user.click(rangeBox())
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('confirms one year as both ends with one box', async () => {
    const user = await open({ year_start: 1878, year_end: 1878 })
    api.setItemReview.mockResolvedValue({})
    const boxes = screen.getAllByRole('checkbox', { name: /confirmed/ })
    await user.click(boxes[0])
    await waitFor(() =>
      expect(api.setItemReview).toHaveBeenCalledWith(
        12,
        expect.arrayContaining(['year_start', 'year_end']),
        true,
      ),
    )
  })
})

describe('ItemEditForm keyboard accelerators', () => {
  it('gives each field and Save an access key, shown in its label', async () => {
    api.getInventoryItem.mockResolvedValue({
      ...item,
      year_start: 1878,
      year_end: 1878,
    })
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')
    const expected = [
      [screen.getByRole('textbox', { name: /Title/ }), 't'],
      [screen.getByRole('textbox', { name: /Description/ }), 'c'],
      [screen.getByRole('spinbutton', { name: 'Year' }), 'y'],
      [screen.getByRole('checkbox', { name: 'Range of years' }), 'r'],
      [screen.getByRole('button', { name: 'Save' }), 'v'],
    ]
    for (const [element, key] of expected) {
      expect(element).toHaveAttribute('accesskey', key)
      expect(element).toHaveAttribute('aria-keyshortcuts', `Alt+${key.toUpperCase()}`)
    }
  })

  it('saves with Ctrl+S once there is something to save', async () => {
    api.updateInventoryItem.mockResolvedValue({})
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')

    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    expect(api.updateInventoryItem).not.toHaveBeenCalled()

    await user.type(screen.getByRole('textbox', { name: /Description/ }), '!')
    fireEvent.keyDown(document, { key: 's', ctrlKey: true })
    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalled())
  })
})

describe('field labels in the grid', () => {
  it('keeps each label whole as one grid item, accelerator letter and all', async () => {
    // `.field` is `display: grid` with four columns, and every direct child
    // is a grid item. A label built from loose text around a `<u>` put each
    // piece of the word in a different column -- the owner saw
    // "T      i      tle" and "Des   c   ription". Asserted here, on the real
    // form, and not only on AccessLabel in isolation: the bug lived in the
    // relationship between the two.
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    const title = await screen.findByRole('textbox', { name: /Title/ })
    const field = title.closest('.field')

    // The whole word in the first cell, with the accelerator letter marked
    // inside it rather than beside it.
    expect(field.children[0].textContent).toBe('Title')
    expect(field.children[0].querySelector('u')).toHaveTextContent('T')
    // ...and the input is the next cell, not the fourth.
    expect(field.children[1]).toBe(title)
  })
})

describe('changing an item status', () => {
  const statuses = emptyReference({
    tables: {
      item_status: [
        { code: 'ordered', label: 'Ordered', source: 'seeded' },
        { code: 'received', label: 'Received', source: 'seeded' },
      ],
    },
  })

  function renderReceived() {
    api.getInventoryItem.mockResolvedValue({ ...item, status: 'received' })
    return renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      { reference: statuses },
    )
  }

  it('shows the status of an item that has already arrived', async () => {
    renderReceived()
    expect(await screen.findByRole('combobox', { name: 'item_status' })).toHaveValue(
      'received',
    )
  })

  it('sends the new status, so something received in error can be put back', async () => {
    // Receiving only ever moves an item forward. Before this field there was
    // no way to undo a receipt recorded against the wrong row.
    api.updateInventoryItem.mockResolvedValue({})
    const user = userEvent.setup()
    renderReceived()

    await user.selectOptions(
      await screen.findByRole('combobox', { name: 'item_status' }),
      'ordered',
    )
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ status: 'ordered' }),
      ),
    )
  })

  it('does not let a new status code be invented from the picker', async () => {
    // Receiving, the outstanding lists and the inventory views all branch on
    // the known status codes; an invented one is a row nothing downstream can
    // reason about. Descriptive vocabularies still grow with use.
    renderReceived()
    const select = await screen.findByRole('combobox', { name: 'item_status' })

    expect(
      within(select).queryByRole('option', { name: /add a new value/i }),
    ).not.toBeInTheDocument()
    // Status is NOT NULL, so there is no blank option to clear it with either.
    expect(within(select).queryByRole('option', { name: '--' })).not.toBeInTheDocument()
  })
})

describe('a banknote in the editor', () => {
  const note = {
    ...item,
    item_kind: 'currency',
    denomination: 'usd_note_1',
    note_type: 'silver_certificate',
    seal_color: 'blue',
    signature_combination: null,
    fed_district: null,
    series_year: 1957,
    series_letter: null,
    serial_number: 'A12345678B',
    derived: { note_type_id: 'note_issue', seal_color_id: 'note_issue' },
  }

  it('shows the note fields, marking what the facts filled in', async () => {
    api.getInventoryItem.mockResolvedValue(note)
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)

    expect(await screen.findByDisplayValue('A12345678B')).toBeInTheDocument()
    expect(screen.getByLabelText('note_type')).toHaveValue('silver_certificate')
    const marks = screen.getAllByText('suggested')
    expect(marks).toHaveLength(2)
    expect(marks[0]).toHaveAttribute(
      'title',
      "Filled from the note's denomination and series",
    )
    expect(screen.getByLabelText('note_type')).toHaveAttribute('accesskey', 'a')
  })

  it('explains the note field that has focus in the help area', async () => {
    api.getInventoryItem.mockResolvedValue(note)
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)

    await user.click(await screen.findByDisplayValue('1957'))
    expect(screen.getByText(/year the design was adopted/)).toBeInTheDocument()
    await user.click(screen.getByDisplayValue('A12345678B'))
    expect(screen.getByText(/star in place of the last letter/)).toBeInTheDocument()
    await user.click(screen.getByLabelText('fed_district'))
    expect(screen.getByText(/letter in the black seal/)).toBeInTheDocument()
  })

  it('drops the mark once the field is changed, and saves the change', async () => {
    api.getInventoryItem.mockResolvedValue(note)
    api.updateInventoryItem.mockResolvedValue({})
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('A12345678B')

    await user.clear(screen.getByLabelText('seal_color'))
    await user.type(screen.getByLabelText('seal_color'), 'red')
    expect(screen.getAllByText('suggested')).toHaveLength(1)

    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(
        12,
        expect.objectContaining({ seal_color: 'red' }),
      ),
    )
  })

  it('can save twice in a row: it reads the item back, new version and all', async () => {
    // A form that stays open after saving -- the last item of a review, or
    // Receiving's one-item review -- sent its old version the second time
    // and was refused as a conflict with itself (code review, 2026-09-23).
    api.getInventoryItem
      .mockResolvedValueOnce({ ...note, version: 3 })
      .mockResolvedValue({ ...note, version: 4, seal_color: 'red' })
    api.updateInventoryItem.mockResolvedValue({})
    const user = userEvent.setup()
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('A12345678B')

    await user.clear(screen.getByLabelText('seal_color'))
    await user.type(screen.getByLabelText('seal_color'), 'red')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.getInventoryItem).toHaveBeenCalledTimes(2))
    // The draft is spent: nothing left to save until something changes.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled(),
    )

    await user.clear(screen.getByDisplayValue('A12345678B'))
    await user.type(screen.getByRole('textbox', { name: /serial number/i }), 'B1')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.updateInventoryItem).toHaveBeenCalledTimes(2))
    expect(api.updateInventoryItem.mock.calls[0][1].version).toBe(3)
    expect(api.updateInventoryItem.mock.calls[1][1].version).toBe(4)
  })

  it('keeps the note fields off a coin', async () => {
    api.getInventoryItem.mockResolvedValue({ ...item, item_kind: 'coin' })
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')

    expect(screen.queryByLabelText('note_type')).toBeNull()
    expect(screen.queryByText('Serial number')).toBeNull()
  })
})

describe('Attributes', () => {
  const attribute = (code, label, applies_to, attribute_group) => ({
    code,
    label,
    source: 'seeded',
    aliases: [],
    extra: { applies_to, attribute_group },
  })
  const vocabularies = emptyReference({
    tables: {
      item_attribute: [
        attribute('star', 'Star Note', 'currency', 'serial'),
        attribute('no_motto', 'No Motto', 'any', 'variety'),
        attribute('first_strike', 'First Strike', 'coin', 'release'),
        attribute('cac', 'CAC (green sticker)', 'coin', 'verification'),
      ],
    },
  })

  async function open(overrides) {
    api.getInventoryItem.mockResolvedValue({ ...item, version: 3, ...overrides })
    renderWithProviders(<ItemEditForm itemId={12} />, { reference: vocabularies })
    await screen.findByDisplayValue('Mercury Dime')
  }

  const offered = () =>
    within(screen.getByRole('combobox', { name: 'item_attribute' }))
      .getAllByRole('option')
      .map((o) => o.textContent)

  it('lists what the item carries and marks what a rule read', async () => {
    await open({
      item_kind: 'currency',
      attributes: [
        {
          code: 'star',
          label: 'Star Note',
          group: 'serial',
          source: 'derived',
          derived_by: 'serial_pattern',
        },
      ],
    })
    expect(screen.getByText('Star Note')).toBeVisible()
    expect(screen.getByTitle('Read from the serial number')).toHaveTextContent('read')
  })

  it('offers only attributes for this kind of item, and not the ones it has', async () => {
    await open({
      item_kind: 'coin',
      attributes: [
        {
          code: 'cac',
          label: 'CAC (green sticker)',
          group: 'verification',
          source: 'manual',
        },
      ],
    })
    expect(offered()).toEqual([
      '--',
      'No Motto',
      'First Strike',
      '+ Add a new value...',
    ])
  })

  it('saves the whole set with the item version', async () => {
    const user = userEvent.setup()
    api.updateInventoryItem.mockResolvedValue({})
    await open({
      item_kind: 'coin',
      attributes: [
        {
          code: 'cac',
          label: 'CAC (green sticker)',
          group: 'verification',
          source: 'manual',
        },
      ],
    })

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'item_attribute' }),
      'first_strike',
    )
    await user.click(screen.getByRole('button', { name: 'Remove CAC (green sticker)' }))
    expect(screen.getByText('First Strike')).toBeVisible()
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(12, {
        attributes: ['first_strike'],
        version: 3,
        base: { attributes: ['cac'] },
      }),
    )
  })

  it('offers nothing until the vocabulary has loaded', async () => {
    api.getInventoryItem.mockResolvedValue({
      ...item,
      item_kind: 'coin',
      attributes: [],
    })
    renderWithProviders(<ItemEditForm itemId={12} />, { reference: emptyReference() })
    await screen.findByDisplayValue('Mercury Dime')
    expect(screen.queryByRole('combobox', { name: 'item_attribute' })).toBeNull()
    expect(screen.queryByRole('textbox', { name: 'item_attribute' })).toBeNull()
  })

  it("adds a value by its label and a chosen group, marked for this item's kind, and keeps it selected", async () => {
    const user = userEvent.setup()
    sharedApi.addReferenceValue.mockResolvedValue({})
    await open({ item_kind: 'coin', attributes: [] })

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'item_attribute' }),
      '__add__',
    )
    await user.type(screen.getByPlaceholderText('label'), 'Gold Toned')
    // The owner chose to ask for the group rather than have one picked
    // silently: Add stays disabled until it is.
    expect(screen.getByRole('button', { name: 'Add' })).toBeDisabled()
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'group' }),
      'verification',
    )
    await user.click(screen.getByRole('button', { name: 'Add' }))

    // extra columns nested under `extra` -- ReferenceValueCreate has no
    // top-level applies_to/attribute_group field (backend/app/schemas.py).
    expect(sharedApi.addReferenceValue).toHaveBeenCalledWith('item_attribute', {
      code: 'gold_toned',
      label: 'Gold Toned',
      extra: { applies_to: 'coin', attribute_group: 'verification' },
    })
    // Selected immediately: the new value does not vanish just because the
    // vocabulary that would otherwise offer it has not refetched yet.
    expect(screen.getByText('gold_toned')).toBeVisible()
    expect(
      screen.getByRole('button', { name: 'Remove gold_toned' }),
    ).toBeInTheDocument()
  })
})

describe('Photographs panel', () => {
  it("mounts in the editor, loading the item's own photographs", async () => {
    // The wiring itself, asserted the same way the errors panel's is: the
    // call `PhotosPanel` makes on mount is what proves the editor still
    // renders it. Deleting the <PhotosPanel .../> line left the whole suite
    // green before this existed, while the feature's stated purpose -- an
    // item gaining a photograph at any time -- was dead.
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')

    // The panel's own effect is awaited before the call is asserted.
    // `findByDisplayValue` above only waits for the FORM to load; the panel
    // is a child with its own effect, and under a loaded suite that effect
    // had not always run by the time the assertion did.
    expect(
      await screen.findByRole('heading', { name: 'Photographs' }),
    ).toBeInTheDocument()
    expect(api.listItemImages).toHaveBeenCalledWith(12)
  })
})

describe('Errors panel', () => {
  it("mounts beside Attributes, loading the item's own errors", async () => {
    render(<ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />)
    await screen.findByDisplayValue('Mercury Dime')

    // Panel first, call second -- see the note in the Photographs test. This
    // one really did fail intermittently in a full run.
    expect(await screen.findByRole('button', { name: 'Add error' })).toBeInTheDocument()
    expect(api.getItemErrors).toHaveBeenCalledWith(12)
  })

  it("passes the item's kind through, so the picker offers only that kind's errors", async () => {
    const vocabularies = emptyReference({
      tables: {
        error_type: [
          {
            code: 'off_center_coin',
            label: 'Off Center',
            source: 'seeded',
            aliases: [],
            extra: { applies_to: 'coin' },
          },
          {
            code: 'inverted_overprint',
            label: 'Inverted Overprint',
            source: 'seeded',
            aliases: [],
            extra: { applies_to: 'currency' },
          },
        ],
      },
    })
    api.getInventoryItem.mockResolvedValue({ ...item, item_kind: 'coin' })
    renderWithProviders(
      <ItemEditForm itemId={12} onSaved={vi.fn()} onClose={vi.fn()} />,
      { reference: vocabularies },
    )
    await screen.findByDisplayValue('Mercury Dime')

    // findBy, not getBy: the item having loaded says nothing about
    // ErrorsPanel, which renders "Loading..." in place of its picker until
    // its OWN `getItemErrors` promise resolves. Waiting on the item alone
    // made this test flake (twice in about sixteen runs).
    const options = within(
      await screen.findByRole('combobox', { name: 'error_type' }),
    ).getAllByRole('option')
    const labels = options.map((o) => o.textContent)
    expect(labels).toContain('Off Center')
    expect(labels).not.toContain('Inverted Overprint')
  })
})

// Task 8 gave ErrorsPanel its own `ForSaleNotice`, so a form for an item that
// is for sale now shows two alerts with the same wording: this form's own,
// above the fields, and the errors panel's, above its list. They read alike,
// so tests here find the one that belongs to the form by the action on its
// checkbox -- "Change it anyway" is this form's own wording, distinct from
// the errors panel's "Record it anyway".
function formForSaleNotice() {
  return screen
    .getAllByRole('alert')
    .find((el) => within(el).queryByRole('checkbox', { name: 'Change it anyway' }))
}

describe('An item for sale', () => {
  const forSale = {
    ...item,
    version: 4,
    sale_state: [{ kind: 'listing', id: 3, text: 'listing #3 at 189.00' }],
  }

  it('says so, and saves only once the change is confirmed', async () => {
    const user = userEvent.setup()
    api.getInventoryItem.mockResolvedValue(forSale)
    api.updateInventoryItem.mockResolvedValue({})
    render(<ItemEditForm itemId={12} />)
    await screen.findByDisplayValue('Mercury Dime')

    expect(formForSaleNotice()).toHaveTextContent(
      'This item is for sale: listing #3 at 189.00.',
    )
    const description = screen.getByDisplayValue('Mercury Dime')
    await user.clear(description)
    await user.type(description, 'Winged Liberty dime')
    const save = screen.getByRole('button', { name: /save/i })
    expect(save).toBeDisabled()

    await user.click(screen.getByRole('checkbox', { name: 'Change it anyway' }))
    await user.click(save)
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(12, {
        description: 'Winged Liberty dime',
        version: 4,
        base: { description: 'Mercury Dime' },
        acknowledge_for_sale: true,
      }),
    )
  })

  it('warns that a new status ends the offer before it is confirmed', async () => {
    // The owner's ruling (2026-09-23): warn, and end the offer once the
    // change is accepted -- the server does the ending.
    const user = userEvent.setup()
    api.getInventoryItem.mockResolvedValue({ ...forSale, status: 'received' })
    render(<ItemEditForm itemId={12} />)
    await screen.findByDisplayValue('Mercury Dime')
    expect(screen.queryByText(/saving ends its offer/)).toBeNull()

    await user.clear(screen.getByLabelText('item_status'))
    await user.type(screen.getByLabelText('item_status'), 'missing')
    expect(screen.getByText(/saving ends its offer/)).toBeInTheDocument()
    expect(
      screen.getByRole('checkbox', { name: 'Change the status and end the offer' }),
    ).toBeInTheDocument()
  })

  // Offering the item from the panel makes it for sale, and the server then
  // refuses any save that does not acknowledge that. Nothing on this form
  // could explain such a refusal, so the form reads the item again.
  it('asks for a change to be acknowledged after the panel offers the item', async () => {
    const user = userEvent.setup()
    const notYet = { ...item, version: 4, sale_state: [] }
    api.getInventoryItem.mockResolvedValueOnce(notYet).mockResolvedValue({
      ...forSale,
      version: 5,
      sale_state: [{ kind: 'listing', id: 14, text: 'listing #14 at 19.00' }],
    })
    api.listListings.mockResolvedValue([])
    api.listSalesVenues.mockResolvedValue([
      { code: 'store', name: 'Web store', is_own_store: true, is_active: true },
    ])
    api.createOffers.mockResolvedValue({ listings: [{ id: 14 }] })
    api.updateInventoryItem.mockResolvedValue({})
    render(<ItemEditForm itemId={12} />)

    await screen.findByDisplayValue('Mercury Dime')
    expect(screen.queryByRole('alert')).toBeNull()

    await user.click(await screen.findByRole('button', { name: 'Offer for sale...' }))
    await screen.findByRole('option', { name: 'Web store' })
    await user.selectOptions(screen.getByLabelText('Platform'), 'store')
    await user.type(screen.getByLabelText('Price for C-012'), '19.00')
    await user.click(screen.getByRole('button', { name: 'Offer 1 for sale' }))

    // Read again, so the form knows the item is spoken for now.
    await screen.findByRole('checkbox', { name: 'Change it anyway' })
    expect(formForSaleNotice()).toHaveTextContent(
      'This item is for sale: listing #14 at 19.00.',
    )
    expect(api.getInventoryItem).toHaveBeenCalledTimes(2)

    const description = screen.getByDisplayValue('Mercury Dime')
    await user.clear(description)
    await user.type(description, 'Winged Liberty dime')
    await user.click(screen.getByRole('checkbox', { name: 'Change it anyway' }))
    await user.click(screen.getByRole('button', { name: /save/i }))

    // The version from the fresh read, and the acknowledgement the server
    // requires. Without the reload this save is a refusal the owner cannot
    // account for.
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(12, {
        description: 'Winged Liberty dime',
        version: 5,
        base: { description: 'Mercury Dime' },
        acknowledge_for_sale: true,
      }),
    )
  })

  it('asks nothing for an item that is not for sale', async () => {
    const user = userEvent.setup()
    api.getInventoryItem.mockResolvedValue({ ...item, version: 4, sale_state: [] })
    api.updateInventoryItem.mockResolvedValue({})
    render(<ItemEditForm itemId={12} />)
    const description = await screen.findByDisplayValue('Mercury Dime')
    expect(screen.queryByRole('alert')).toBeNull()
    await user.type(description, '!')
    await user.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() =>
      expect(api.updateInventoryItem).toHaveBeenCalledWith(12, {
        description: 'Mercury Dime!',
        version: 4,
        base: { description: 'Mercury Dime' },
      }),
    )
  })

  it('lists each sale as it was sold', async () => {
    api.getInventoryItem.mockResolvedValue(item)
    api.getItemSales.mockResolvedValue([
      {
        order_id: 9,
        status: 'shipped',
        placed_at: '2026-09-17T10:00:00Z',
        customer_name: 'Ada',
        quantity: 1,
        unit_price: '189.00',
        snapshot: { item: { source_title: '1881-S Morgan', grade_display: 'MS64' } },
      },
    ])
    render(<ItemEditForm itemId={12} />)
    expect(await screen.findByRole('heading', { name: 'Sales' })).toBeVisible()
    expect(api.getItemSales).toHaveBeenCalledWith(12)
    expect(
      screen.getByText(/Order #9, 2026-09-17, shipped: 1 at 189.00 to Ada/),
    ).toBeVisible()
    expect(screen.getByText(/sold as 1881-S Morgan, MS64/)).toBeVisible()
  })

  it('shows the item history and reads it again after a save', async () => {
    const user = userEvent.setup()
    const saved = { ...item, version: 5, sale_state: [], description: 'Mercury Dime!' }
    api.getInventoryItem
      .mockResolvedValueOnce({ ...item, version: 4, sale_state: [] })
      .mockResolvedValue(saved)
    api.updateInventoryItem.mockResolvedValue(saved)
    render(<ItemEditForm itemId={12} />)
    expect(await screen.findByRole('heading', { name: 'History' })).toBeVisible()
    expect(api.getItemHistory).toHaveBeenCalledWith(12)
    expect(api.getItemHistory).toHaveBeenCalledTimes(1)

    await user.type(screen.getByDisplayValue('Mercury Dime'), '!')
    await user.click(screen.getByRole('button', { name: /save/i }))
    await waitFor(() => expect(api.getItemHistory).toHaveBeenCalledTimes(2))
  })

  // A lot line's `quantity` and `unit_price` describe the whole group, so
  // showing them here would credit each coin with the lot's whole price.
  it('shows a sale made inside a lot as this coin’s own share', async () => {
    api.getInventoryItem.mockResolvedValue(item)
    api.getItemSales.mockResolvedValue([
      {
        order_id: 9,
        status: 'shipped',
        placed_at: '2026-09-17T10:00:00Z',
        customer_name: 'Ada',
        quantity: 1,
        unit_price: '1000.00',
        sales_lot_id: 7,
        share_amount: '200.00',
        snapshot: { lot: { title: 'Three Morgans' } },
      },
    ])
    render(<ItemEditForm itemId={12} />)
    expect(await screen.findByRole('heading', { name: 'Sales' })).toBeVisible()
    expect(
      screen.getByText(/Order #9, 2026-09-17, shipped: 200.00 of lot #7 to Ada/),
    ).toBeVisible()
    // The lot's own price must not appear against one coin.
    expect(screen.queryByText(/1000\.00/)).toBeNull()
  })

  // Beside the sale history: where the item has been offered, and the place
  // an offer is started from without leaving the editor.
  it('shows the item its offers, ended ones included', async () => {
    api.listListings.mockResolvedValue([
      {
        id: 14,
        item_id: 12,
        item_code: 'C-012',
        item_title: 'Mercury Dime',
        venue: 'ebay',
        venue_name: 'eBay',
        format: 'fixed_price',
        status: 'active',
        price: '42.00',
        currency: 'USD',
        quantity_available: 1,
        title: 'Mercury Dime',
        description: '',
        external_id: null,
        external_url: null,
        listed_at: '2026-09-17T12:00:00Z',
        ended_at: null,
        paused_by_listing_id: null,
        cost_basis: '20.00',
        version: 1,
      },
    ])
    render(<ItemEditForm itemId={12} />)

    // Waited for the row, not for the heading: the panel shows its heading
    // while it is still loading, so finding that proves nothing arrived.
    expect(await screen.findByText('42.00 USD')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Offers' })).toBeVisible()
    expect(api.listListings).toHaveBeenCalledWith({ item_id: 12, status: 'all' })
  })
})
