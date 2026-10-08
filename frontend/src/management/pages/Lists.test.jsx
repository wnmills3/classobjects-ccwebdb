import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listFriedbergCatalog: vi.fn(),
    updateFriedbergNumber: vi.fn(),
    deleteFriedbergNumber: vi.fn(),
    listSellers: vi.fn(),
    updateSeller: vi.fn(),
    deleteSeller: vi.fn(),
    listVendors: vi.fn(),
    updateVendor: vi.fn(),
    deleteVendor: vi.fn(),
    listStorageLocations: vi.fn(),
    updateStorageLocation: vi.fn(),
    deleteStorageLocation: vi.fn(),
  },
}))

import { api } from '../api'
import { emptyReference, renderWithProviders } from '../../test/helpers'
import Lists from './Lists'

const reference = emptyReference({
  tables: {
    vendor_kind: [
      { code: 'marketplace', label: 'Marketplace', source: 'seeded', extra: {} },
      { code: 'dealer', label: 'Dealer', source: 'seeded', extra: {} },
    ],
    storage_location_kind: [
      {
        code: 'safe_deposit_box',
        label: 'Safe deposit box',
        source: 'seeded',
        extra: {},
      },
      { code: 'home', label: 'Home', source: 'seeded', extra: {} },
      { code: 'sold', label: 'Sold', source: 'seeded', extra: {} },
    ],
  },
})

const fr = (overrides) => ({
  id: 14,
  fr_number: '9928-',
  denomination: 'usd_note_1',
  note_type: 'frn',
  series_year: 2021,
  series_letter: null,
  district_letter: 'L',
  seal_color: null,
  signature_combination: null,
  web_press: null,
  printing_facility: null,
  size_class: null,
  description: null,
  source: 'manual',
  verified: false,
  verified_at: null,
  item_count: 0,
  ...overrides,
})

function open(tab) {
  renderWithProviders(<Lists />, {
    reference,
    route: tab ? `/lists?tab=${tab}` : '/lists',
  })
}

function rowFor(text) {
  return screen.getByText(text).closest('tr')
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listFriedbergCatalog.mockResolvedValue([
    fr(),
    fr({
      id: 9,
      fr_number: '9928-G',
      district_letter: 'G',
      verified: true,
      item_count: 2,
    }),
  ])
  api.listSellers.mockResolvedValue([
    { id: 1, name: 'busy_seller', store_url: null, order_count: 3 },
    { id: 2, name: 'idle_seler', store_url: null, order_count: 0 },
  ])
  api.listVendors.mockResolvedValue([
    { id: 5, name: 'ebay.com', url: null, vendor_kind: 'marketplace', order_count: 40 },
  ])
  api.listStorageLocations.mockResolvedValue([
    {
      id: 7,
      label: 'Test Bank 20l',
      kind: 'safe_deposit_box',
      institution: 'Test Bank',
      identifier: '20l',
      notes: null,
      item_count: 0,
    },
    {
      id: 8,
      label: 'Sold',
      kind: 'sold',
      institution: null,
      identifier: null,
      notes: null,
      item_count: 5,
    },
  ])
})

describe('Lists: Friedberg numbers', () => {
  it('lists each number with its type, whether confirmed, and the items using it', async () => {
    open()
    const slip = await screen.findByText('9928-')
    const row = slip.closest('tr')
    expect(row).toHaveTextContent('2021')
    expect(row).toHaveTextContent('L')
    expect(row).toHaveTextContent(/proposed/i)
    expect(rowFor('9928-G')).toHaveTextContent(/confirmed/i)
    expect(rowFor('9928-G')).toHaveTextContent('2')
  })

  it('corrects a mistyped number in place', async () => {
    const user = userEvent.setup()
    api.updateFriedbergNumber.mockResolvedValue(fr({ fr_number: '9928-L' }))
    open()
    const row = (await screen.findByText('9928-')).closest('tr')

    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const number = within(row).getByRole('textbox', { name: 'Number' })
    await user.clear(number)
    await user.type(number, '9928-L')
    await user.click(within(row).getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(api.updateFriedbergNumber).toHaveBeenCalledWith(14, {
        fr_number: '9928-L',
      }),
    )
    // Read again, so what is shown is what the server kept.
    await waitFor(() => expect(api.listFriedbergCatalog).toHaveBeenCalledTimes(2))
  })

  it('confirms a number and undoes a confirmation', async () => {
    const user = userEvent.setup()
    api.updateFriedbergNumber.mockResolvedValue(fr())
    open()
    await user.click(
      within((await screen.findByText('9928-')).closest('tr')).getByRole('button', {
        name: 'Confirm',
      }),
    )
    await user.click(
      within(rowFor('9928-G')).getByRole('button', { name: 'Undo confirm' }),
    )

    expect(api.updateFriedbergNumber).toHaveBeenCalledWith(14, { verified: true })
    expect(api.updateFriedbergNumber).toHaveBeenCalledWith(9, { verified: false })
  })

  it('deletes only a number no item uses, after asking', async () => {
    const user = userEvent.setup()
    api.deleteFriedbergNumber.mockResolvedValue(null)
    open()
    const row = (await screen.findByText('9928-')).closest('tr')
    expect(
      within(rowFor('9928-G')).queryByRole('button', { name: 'Delete' }),
    ).toBeNull()

    await user.click(within(row).getByRole('button', { name: 'Delete' }))
    expect(api.deleteFriedbergNumber).not.toHaveBeenCalled()
    await user.click(within(row).getByRole('button', { name: 'Yes, delete' }))

    await waitFor(() => expect(api.deleteFriedbergNumber).toHaveBeenCalledWith(14))
  })

  it('shows why the server refused a change', async () => {
    const user = userEvent.setup()
    api.updateFriedbergNumber.mockRejectedValue(
      new Error("fr_number '9928-G' is already recorded as row 9"),
    )
    open()
    const row = (await screen.findByText('9928-')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.clear(within(row).getByRole('textbox', { name: 'Number' }))
    await user.type(within(row).getByRole('textbox', { name: 'Number' }), '9928-G')
    await user.click(within(row).getByRole('button', { name: 'Save' }))

    expect(await screen.findByText(/already recorded as row 9/)).toBeInTheDocument()
  })

  it('checks a corrected number before sending it, and sends it cleaned', async () => {
    const user = userEvent.setup()
    api.updateFriedbergNumber.mockResolvedValue(fr())
    open()
    const row = (await screen.findByText('9928-')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const number = within(row).getByRole('textbox', { name: 'Number' })

    await user.clear(number)
    await user.type(number, '99280-L')
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    expect(await screen.findByText(/has 5 digits/)).toBeInTheDocument()
    expect(api.updateFriedbergNumber).not.toHaveBeenCalled()

    await user.clear(number)
    await user.type(number, ' fr 9928-l ')
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateFriedbergNumber).toHaveBeenCalledWith(14, {
        fr_number: '9928-L',
      }),
    )
  })

  it('narrows the list by what is typed in the search box', async () => {
    const user = userEvent.setup()
    open()
    await screen.findByText('9928-')
    await user.type(screen.getByRole('searchbox', { name: 'Search' }), '9928-G')
    expect(screen.queryByText('9928-')).toBeNull()
    expect(screen.getByText('9928-G')).toBeInTheDocument()
  })
})

describe('Lists: sellers, vendors and storage locations', () => {
  it('renames a seller and deletes only an unused one', async () => {
    const user = userEvent.setup()
    api.updateSeller.mockResolvedValue({})
    api.deleteSeller.mockResolvedValue(null)
    open('sellers')
    const row = (await screen.findByText('idle_seler')).closest('tr')
    expect(
      within(rowFor('busy_seller')).queryByRole('button', { name: 'Delete' }),
    ).toBeNull()

    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.clear(within(row).getByRole('textbox', { name: 'Name' }))
    await user.type(within(row).getByRole('textbox', { name: 'Name' }), 'idle_seller')
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateSeller).toHaveBeenCalledWith(2, { name: 'idle_seller' }),
    )
  })

  it("changes a vendor's kind", async () => {
    const user = userEvent.setup()
    api.updateVendor.mockResolvedValue({})
    open('vendors')
    const row = (await screen.findByText('ebay.com')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.selectOptions(
      within(row).getByRole('combobox', { name: 'Kind' }),
      'dealer',
    )
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateVendor).toHaveBeenCalledWith(5, { vendor_kind: 'dealer' }),
    )
  })

  it('shows a vendor with no kind as having none, and takes the first kind for it', async () => {
    const user = userEvent.setup()
    api.updateVendor.mockResolvedValue({})
    api.listVendors.mockResolvedValue([
      { id: 6, name: 'kindless.example', url: null, vendor_kind: null, order_count: 1 },
    ])
    open('vendors')
    const row = (await screen.findByText('kindless.example')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const kind = within(row).getByRole('combobox', { name: 'Kind' })
    // Not the first kind in the list, which nobody chose for it.
    expect(kind).toHaveValue('')

    await user.selectOptions(kind, 'marketplace')
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateVendor).toHaveBeenCalledWith(6, { vendor_kind: 'marketplace' }),
    )
  })

  it('shows a kind the pickers no longer offer as itself', async () => {
    const user = userEvent.setup()
    api.listVendors.mockResolvedValue([
      {
        id: 6,
        name: 'old.example',
        url: null,
        vendor_kind: 'pawnshop',
        order_count: 1,
      },
    ])
    open('vendors')
    const row = (await screen.findByText('old.example')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    expect(within(row).getByRole('combobox', { name: 'Kind' })).toHaveValue('pawnshop')
  })

  it('refuses a blank name, which the server would leave as it was', async () => {
    const user = userEvent.setup()
    open('sellers')
    const row = (await screen.findByText('idle_seler')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.clear(within(row).getByRole('textbox', { name: 'Name' }))
    await user.click(within(row).getByRole('button', { name: 'Save' }))

    expect(screen.getByText('Name cannot be blank.')).toBeInTheDocument()
    expect(api.updateSeller).not.toHaveBeenCalled()
    // Still open, to be typed into again.
    expect(within(row).getByRole('textbox', { name: 'Name' })).toBeInTheDocument()
  })

  it('tells a search that found nothing from a list with nothing in it', async () => {
    const user = userEvent.setup()
    open('sellers')
    await screen.findByText('idle_seler')
    await user.type(screen.getByRole('searchbox', { name: 'Search' }), 'zzz')
    expect(screen.getByText('Nothing matches zzz.')).toBeInTheDocument()
    expect(screen.queryByText('Nothing here yet.')).toBeNull()
  })

  it("corrects a location's identifier, and leaves the sale code's own alone", async () => {
    const user = userEvent.setup()
    api.updateStorageLocation.mockResolvedValue({})
    open('locations')
    const row = (await screen.findByText('20l')).closest('tr')
    expect(within(rowFor('Sold')).queryByRole('button', { name: 'Edit' })).toBeNull()

    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.clear(within(row).getByRole('textbox', { name: 'Identifier' }))
    await user.type(within(row).getByRole('textbox', { name: 'Identifier' }), '201')
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateStorageLocation).toHaveBeenCalledWith(7, { identifier: '201' }),
    )
  })
})
