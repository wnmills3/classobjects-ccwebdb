import { useState } from 'react'

import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./api', () => ({
  api: { listStorageLocations: vi.fn(), createStorageLocation: vi.fn() },
}))

import { api } from './api'
import LocationSelect from './LocationSelect'
import { emptyReference, renderWithProviders } from '../test/helpers'

const KINDS = [
  'safe_deposit_box',
  'safe',
  'home',
  'in_transit',
  'consigned',
  'sold',
].map((code, n) => ({
  code,
  label: code,
  sort_order: n,
  source: 'seeded',
  aliases: [],
  extra: {},
}))

function Holder({ onValue }) {
  const [value, setValue] = useState('')
  return (
    <label>
      Storage location
      <LocationSelect
        value={value}
        onChange={(v) => {
          setValue(v)
          onValue(v)
        }}
      />
    </label>
  )
}

function renderPicker(onValue = vi.fn()) {
  renderWithProviders(<Holder onValue={onValue} />, {
    reference: emptyReference({ tables: { storage_location_kind: KINDS } }),
  })
  return onValue
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listStorageLocations.mockResolvedValue([
    { id: 3, label: 'First Bank 804', kind: 'safe_deposit_box' },
  ])
})

describe('LocationSelect', () => {
  it('offers the locations there are, and none', async () => {
    renderPicker()
    const pick = await screen.findByRole('combobox', { name: /storage location/i })
    await waitFor(() =>
      expect(
        screen.getByRole('option', { name: 'First Bank 804' }),
      ).toBeInTheDocument(),
    )
    expect(pick).toHaveValue('')
  })

  it('adds a location by kind, place and box, and chooses it', async () => {
    const user = userEvent.setup()
    api.createStorageLocation.mockResolvedValue({
      id: 9,
      label: 'First Bank 809',
      kind: 'safe_deposit_box',
    })
    const onValue = renderPicker()
    const pick = await screen.findByRole('combobox', { name: /storage location/i })
    await user.selectOptions(pick, '__add__')

    await user.selectOptions(
      screen.getByLabelText('storage_location_kind'),
      'safe_deposit_box',
    )
    await user.type(screen.getByPlaceholderText('Bank or place'), 'First Bank')
    await user.type(screen.getByPlaceholderText('Box or number'), '809')
    await user.click(screen.getByRole('button', { name: 'Add location' }))

    await waitFor(() =>
      expect(api.createStorageLocation).toHaveBeenCalledWith({
        kind: 'safe_deposit_box',
        institution: 'First Bank',
        identifier: '809',
      }),
    )
    expect(onValue).toHaveBeenLastCalledWith('9')
    expect(
      await screen.findByRole('option', { name: 'First Bank 809' }),
    ).toBeInTheDocument()
  })

  it('does not offer the kinds other code makes', async () => {
    const user = userEvent.setup()
    renderPicker()
    await user.selectOptions(
      await screen.findByRole('combobox', { name: /storage location/i }),
      '__add__',
    )
    const kinds = [...screen.getByLabelText('storage_location_kind').options].map(
      (o) => o.value,
    )
    expect(kinds).toContain('safe')
    expect(kinds).not.toContain('consigned')
    expect(kinds).not.toContain('sold')
  })

  it('shows a refusal and keeps what was typed', async () => {
    const user = userEvent.setup()
    api.createStorageLocation.mockRejectedValue(
      new Error('That storage location already exists'),
    )
    renderPicker()
    await user.selectOptions(
      await screen.findByRole('combobox', { name: /storage location/i }),
      '__add__',
    )
    await user.selectOptions(screen.getByLabelText('storage_location_kind'), 'home')
    await user.click(screen.getByRole('button', { name: 'Add location' }))

    expect(
      await screen.findByText('That storage location already exists'),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('storage_location_kind')).toHaveValue('home')
  })
})
