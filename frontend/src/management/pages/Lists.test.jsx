import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listFriedbergCatalog: vi.fn(),
    createFriedbergNumber: vi.fn(),
    updateFriedbergNumber: vi.fn(),
    deleteFriedbergNumber: vi.fn(),
    listSellers: vi.fn(),
    createSeller: vi.fn(),
    updateSeller: vi.fn(),
    deleteSeller: vi.fn(),
    listVendors: vi.fn(),
    createVendor: vi.fn(),
    updateVendor: vi.fn(),
    deleteVendor: vi.fn(),
    listStorageLocations: vi.fn(),
    createStorageLocation: vi.fn(),
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
      { code: 'consigned', label: 'Consigned', source: 'seeded', extra: {} },
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
      label: 'Consigned',
      kind: 'consigned',
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

  it('puts the cursor in the number on Edit, and saves on Enter', async () => {
    // Typing starts at once, with no click into the box first; Enter is the
    // Save button.
    const user = userEvent.setup()
    api.updateFriedbergNumber.mockResolvedValue(fr({ fr_number: '9928-L' }))
    open()
    const row = (await screen.findByText('9928-')).closest('tr')

    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const number = within(row).getByRole('textbox', { name: 'Number' })
    expect(number).toHaveFocus()
    await user.keyboard('L{Enter}')

    await waitFor(() =>
      expect(api.updateFriedbergNumber).toHaveBeenCalledWith(14, {
        fr_number: '9928-L',
      }),
    )
  })

  it('cancels on Escape: nothing sent, the row shown as it was', async () => {
    const user = userEvent.setup()
    open()
    const row = (await screen.findByText('9928-')).closest('tr')

    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.keyboard('L{Escape}')

    expect(within(row).queryByRole('textbox', { name: 'Number' })).toBeNull()
    expect(within(row).getByText('9928-')).toBeInTheDocument()
    expect(api.updateFriedbergNumber).not.toHaveBeenCalled()
    // Edit again starts from the saved value, not the abandoned one.
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    expect(within(row).getByRole('textbox', { name: 'Number' })).toHaveValue('9928-')
  })

  it('saves on Enter from any box of the row being edited', async () => {
    const user = userEvent.setup()
    api.updateFriedbergNumber.mockResolvedValue(fr({ description: 'A test note' }))
    open()
    const row = (await screen.findByText('9928-')).closest('tr')

    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.type(
      within(row).getByRole('textbox', { name: 'Description' }),
      'A test note{Enter}',
    )

    await waitFor(() =>
      expect(api.updateFriedbergNumber).toHaveBeenCalledWith(14, {
        description: 'A test note',
      }),
    )
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

    // The unused seller is the one that can be deleted, after asking.
    const unused = (await screen.findByText('idle_seler')).closest('tr')
    await user.click(await within(unused).findByRole('button', { name: 'Delete' }))
    expect(api.deleteSeller).not.toHaveBeenCalled()
    await user.click(within(unused).getByRole('button', { name: 'Yes, delete' }))
    await waitFor(() => expect(api.deleteSeller).toHaveBeenCalledWith(2))
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

  // The server takes only an http(s) address: a link corrected here is sent
  // as one, exactly as a link added is.
  it("corrects a vendor's link to a web address", async () => {
    const user = userEvent.setup()
    api.updateVendor.mockResolvedValue({})
    open('vendors')
    const row = (await screen.findByText('ebay.com')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.type(within(row).getByRole('textbox', { name: 'Link' }), ' ebay.com ')
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateVendor).toHaveBeenCalledWith(5, { url: 'https://ebay.com' }),
    )
  })

  it("corrects a seller's store link to a web address, and clears one as null", async () => {
    const user = userEvent.setup()
    api.updateSeller.mockResolvedValue({})
    api.listSellers.mockResolvedValue([
      { id: 1, name: 'busy_seller', store_url: null, order_count: 3 },
      {
        id: 2,
        name: 'linked_seller',
        store_url: 'https://shop.example.com',
        order_count: 1,
      },
    ])
    open('sellers')
    const row = (await screen.findByText('busy_seller')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.type(
      within(row).getByRole('textbox', { name: 'Store link' }),
      'www.example.com/shop',
    )
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateSeller).toHaveBeenCalledWith(1, {
        store_url: 'https://www.example.com/shop',
      }),
    )

    const linked = (await screen.findByText('linked_seller')).closest('tr')
    await user.click(await within(linked).findByRole('button', { name: 'Edit' }))
    await user.clear(within(linked).getByRole('textbox', { name: 'Store link' }))
    await user.click(within(linked).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateSeller).toHaveBeenLastCalledWith(2, { store_url: null }),
    )
  })

  it('leaves a link with its scheme as it was typed', async () => {
    const user = userEvent.setup()
    api.updateVendor.mockResolvedValue({})
    open('vendors')
    const row = (await screen.findByText('ebay.com')).closest('tr')
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.type(
      within(row).getByRole('textbox', { name: 'Link' }),
      'http://www.ebay.com',
    )
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateVendor).toHaveBeenCalledWith(5, { url: 'http://www.ebay.com' }),
    )
  })

  it('shows a vendor with no kind as having none, and saves the kind chosen for it', async () => {
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

  it('counts a location by the item records that name it, not by items', async () => {
    // An item kept there now also has the move that put it there in its
    // history: the count is records, and one item can be two of them.
    open('locations')
    await screen.findByText('20l')
    expect(screen.getByRole('columnheader', { name: 'Item records' })).toBeVisible()
    expect(screen.queryByRole('columnheader', { name: 'Items' })).toBeNull()
  })

  it("corrects a location's identifier, and leaves the auction code's own alone", async () => {
    const user = userEvent.setup()
    api.updateStorageLocation.mockResolvedValue({})
    open('locations')
    const row = (await screen.findByText('20l')).closest('tr')
    expect(
      within(rowFor('Consigned')).queryByRole('button', { name: 'Edit' }),
    ).toBeNull()

    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    await user.clear(within(row).getByRole('textbox', { name: 'Identifier' }))
    await user.type(within(row).getByRole('textbox', { name: 'Identifier' }), '201')
    await user.click(within(row).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(api.updateStorageLocation).toHaveBeenCalledWith(7, { identifier: '201' }),
    )
  })
})

describe('Lists: adding', () => {
  /** Open the add form on the tab shown, and return it. */
  async function openAdd(user, noun) {
    await user.click(await screen.findByRole('button', { name: `Add ${noun}` }))
    return screen.getByRole('form', { name: `New ${noun}` })
  }

  it('adds a Friedberg number, checked before it is sent', async () => {
    const user = userEvent.setup()
    api.createFriedbergNumber.mockResolvedValue({})
    open('friedberg')
    const form = await openAdd(user, 'number')

    await user.type(within(form).getByRole('textbox', { name: 'Number' }), '9928-')
    await user.click(within(form).getByRole('button', { name: 'Add' }))
    expect(await screen.findByText(/ends with a hyphen/)).toBeVisible()
    expect(api.createFriedbergNumber).not.toHaveBeenCalled()

    await user.type(within(form).getByRole('textbox', { name: 'Number' }), 'K')
    await user.type(
      within(form).getByRole('textbox', { name: 'Description' }),
      'Kansas City star',
    )
    await user.click(within(form).getByRole('button', { name: 'Add' }))
    await waitFor(() =>
      expect(api.createFriedbergNumber).toHaveBeenCalledWith({
        fr_number: '9928-K',
        description: 'Kansas City star',
      }),
    )
    // Closed, and the list read again to show what the server kept.
    await waitFor(() =>
      expect(screen.queryByRole('form', { name: 'New number' })).toBeNull(),
    )
    expect(api.listFriedbergCatalog).toHaveBeenCalledTimes(2)
  })

  it('adds a seller by name alone, leaving out the link not given', async () => {
    const user = userEvent.setup()
    api.createSeller.mockResolvedValue({})
    open('sellers')
    const form = await openAdd(user, 'seller')

    await user.click(within(form).getByRole('button', { name: 'Add' }))
    expect(await screen.findByText('Name cannot be blank.')).toBeVisible()
    expect(api.createSeller).not.toHaveBeenCalled()

    await user.type(within(form).getByRole('textbox', { name: 'Name' }), ' coin_shop ')
    await user.click(within(form).getByRole('button', { name: 'Add' }))
    await waitFor(() =>
      expect(api.createSeller).toHaveBeenCalledWith({ name: 'coin_shop' }),
    )
  })

  it('adds a seller with its store link as a web address', async () => {
    const user = userEvent.setup()
    api.createSeller.mockResolvedValue({})
    open('sellers')
    const form = await openAdd(user, 'seller')

    await user.type(within(form).getByRole('textbox', { name: 'Name' }), 'coin_shop')
    await user.type(
      within(form).getByRole('textbox', { name: 'Store link' }),
      'shop.example.com',
    )
    await user.click(within(form).getByRole('button', { name: 'Add' }))
    await waitFor(() =>
      expect(api.createSeller).toHaveBeenCalledWith({
        name: 'coin_shop',
        store_url: 'https://shop.example.com',
      }),
    )
  })

  it('adds a vendor with its kind, and its address as a web address', async () => {
    const user = userEvent.setup()
    api.createVendor.mockResolvedValue({})
    open('vendors')
    const form = await openAdd(user, 'vendor')

    await user.type(within(form).getByRole('textbox', { name: 'Name' }), 'apmex.com')
    await user.type(within(form).getByRole('textbox', { name: 'Link' }), 'apmex.com')
    await user.selectOptions(
      within(form).getByRole('combobox', { name: 'Kind' }),
      'dealer',
    )
    await user.click(within(form).getByRole('button', { name: 'Add' }))
    await waitFor(() =>
      expect(api.createVendor).toHaveBeenCalledWith({
        name: 'apmex.com',
        url: 'https://apmex.com',
        vendor_kind: 'dealer',
      }),
    )
  })

  it('adds a location only with a kind, and never one the auction code makes', async () => {
    const user = userEvent.setup()
    api.createStorageLocation.mockResolvedValue({})
    open('locations')
    const form = await openAdd(user, 'location')
    const kind = within(form).getByRole('combobox', { name: 'Kind' })
    expect(within(kind).queryByRole('option', { name: 'Consigned' })).toBeNull()

    await user.type(within(form).getByRole('textbox', { name: 'Identifier' }), '12')
    await user.click(within(form).getByRole('button', { name: 'Add' }))
    expect(await screen.findByText('Kind cannot be blank.')).toBeVisible()
    expect(api.createStorageLocation).not.toHaveBeenCalled()

    await user.selectOptions(kind, 'safe_deposit_box')
    await user.type(
      within(form).getByRole('textbox', { name: 'Institution' }),
      'Test Bank',
    )
    await user.click(within(form).getByRole('button', { name: 'Add' }))
    await waitFor(() =>
      expect(api.createStorageLocation).toHaveBeenCalledWith({
        kind: 'safe_deposit_box',
        institution: 'Test Bank',
        identifier: '12',
      }),
    )
  })

  it('keeps what was typed when the server refuses it, and Cancel sends nothing', async () => {
    const user = userEvent.setup()
    api.createVendor.mockRejectedValue(new Error('A vendor named ebay.com exists.'))
    open('vendors')
    const form = await openAdd(user, 'vendor')
    await user.type(within(form).getByRole('textbox', { name: 'Name' }), 'ebay.com')
    await user.click(within(form).getByRole('button', { name: 'Add' }))

    expect(await screen.findByText('A vendor named ebay.com exists.')).toBeVisible()
    expect(within(form).getByRole('textbox', { name: 'Name' })).toHaveValue('ebay.com')

    await user.click(within(form).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('form', { name: 'New vendor' })).toBeNull()
    expect(api.createVendor).toHaveBeenCalledTimes(1)
  })
})
