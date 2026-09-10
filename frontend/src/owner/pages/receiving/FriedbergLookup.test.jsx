import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getSignatureCombinations: vi.fn(),
    searchFriedberg: vi.fn(),
    createFriedbergNumber: vi.fn(),
    attachFriedberg: vi.fn(),
  },
}))

import { api } from '../../api'
import FriedbergLookup from './FriedbergLookup'
import { renderWithProviders } from '../../../test/helpers'

// Obviously synthetic, per CLAUDE.md's ban on shipping a publisher's
// Friedberg arrangement -- these codes and numbers are not real catalogue
// entries, not even in a fixture pretending to be one.
const SIGNATURES_ALL = [
  { code: 'FR-TEST-SIG-A', label: 'Test Treasurer A / Test Secretary A' },
  { code: 'FR-TEST-SIG-B', label: 'Test Treasurer B / Test Secretary B' },
]
const SIGNATURES_1963 = [SIGNATURES_ALL[0]]

const ROW_VERIFIED = {
  id: 1,
  fr_number: 'FR-TEST-1',
  note_type: null,
  denomination: null,
  series_year: null,
  series_letter: null,
  seal_color: null,
  signature_combination: null,
  district_letter: null,
  size_class: null,
  description: null,
  source: 'manual',
  verified: true,
  verified_at: '2026-01-01T00:00:00Z',
}

const ROW_UNVERIFIED = {
  ...ROW_VERIFIED,
  id: 2,
  fr_number: 'FR-TEST-2',
  verified: false,
  verified_at: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getSignatureCombinations.mockResolvedValue({
    table: 'signature_combination',
    values: SIGNATURES_ALL,
  })
  api.searchFriedberg.mockResolvedValue([])
  api.createFriedbergNumber.mockResolvedValue({ id: 9, fr_number: 'FR-TEST-1' })
  api.attachFriedberg.mockResolvedValue({
    inventory_item_id: 412,
    friedberg_id: 9,
    friedberg_status: 'proposed',
    fr_number: 'FR-TEST-1',
    verified: false,
    verified_at: null,
  })
})

describe('FriedbergLookup', () => {
  it('offers the unnarrowed signature list, then narrows once a year is entered', async () => {
    api.getSignatureCombinations.mockImplementation((year) =>
      Promise.resolve({
        table: 'signature_combination',
        values: year ? SIGNATURES_1963 : SIGNATURES_ALL,
      }),
    )
    renderWithProviders(<FriedbergLookup itemId={412} />)

    // No year entered yet: the unnarrowed list, not an empty one.
    await waitFor(() =>
      expect(api.getSignatureCombinations).toHaveBeenCalledWith(undefined),
    )
    const select = await screen.findByLabelText(/signature combination/i)
    expect(select).toHaveTextContent('Test Treasurer A / Test Secretary A')
    expect(select).toHaveTextContent('Test Treasurer B / Test Secretary B')

    await userEvent.type(screen.getByLabelText(/series year/i), '1963')

    await waitFor(() => expect(api.getSignatureCombinations).toHaveBeenCalledWith(1963))
    // Would still pass a component that fetched the year but ignored the
    // response: asserting the wider pair is gone, not just that the narrow
    // one is present, is what actually proves the list was replaced.
    await waitFor(() =>
      expect(select).not.toHaveTextContent('Test Treasurer B / Test Secretary B'),
    )
    expect(select).toHaveTextContent('Test Treasurer A / Test Secretary A')
  })

  it('sends the codes the pulldowns hold when the owner looks up a match', async () => {
    renderWithProviders(<FriedbergLookup itemId={412} />)

    await userEvent.type(screen.getByLabelText(/denomination/i), 'usd_note_5_00')
    await userEvent.type(screen.getByLabelText(/note type/i), 'federal_reserve_note')
    await userEvent.type(screen.getByLabelText(/seal color/i), 'green')
    await userEvent.type(screen.getByLabelText(/series year/i), '2017')
    await userEvent.type(screen.getByLabelText(/series letter/i), 'a')
    await waitFor(() => expect(api.getSignatureCombinations).toHaveBeenCalledWith(2017))
    await userEvent.selectOptions(
      screen.getByLabelText(/signature combination/i),
      'FR-TEST-SIG-A',
    )

    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    // Would fail a component that ignored the pulldowns and searched with no
    // filters (or the wrong ones) -- every code here came from what was
    // typed or chosen, not from a default.
    await waitFor(() =>
      expect(api.searchFriedberg).toHaveBeenCalledWith({
        denomination: 'usd_note_5_00',
        note_type: 'federal_reserve_note',
        seal_color: 'green',
        series_year: 2017,
        series_letter: 'A',
        signature_combination: 'FR-TEST-SIG-A',
      }),
    )
  })

  it('shows a verified result and an unverified proposal differently', async () => {
    api.searchFriedberg.mockResolvedValue([ROW_VERIFIED, ROW_UNVERIFIED])
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(2)
    // Would pass a component that labeled every row the same way only if
    // both fixtures agreed -- they deliberately do not, so this fails a
    // component that always says "Verified" or always says "Unverified
    // proposal" regardless of the row's own `verified` flag.
    expect(rows[0]).toHaveTextContent('FR-TEST-1')
    expect(rows[0]).toHaveTextContent('Verified')
    expect(rows[0]).not.toHaveTextContent('Unverified')
    expect(rows[1]).toHaveTextContent('FR-TEST-2')
    expect(rows[1]).toHaveTextContent('Unverified proposal')
  })

  it('surfaces a 409 on recording rather than swallowing it', async () => {
    api.createFriedbergNumber.mockRejectedValue(
      Object.assign(new Error("fr_number 'FR-TEST-1' is already recorded as row 7"), {
        status: 409,
      }),
    )
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    const frInput = await screen.findByLabelText(/fr\. number/i)
    await userEvent.type(frInput, 'FR-TEST-1')
    await userEvent.click(
      screen.getByRole('button', { name: /record & attach as proposed/i }),
    )

    expect(await screen.findByText(/already recorded as row 7/i)).toBeInTheDocument()
    // The 409 must stop the flow, not be swallowed and attached anyway.
    expect(api.attachFriedberg).not.toHaveBeenCalled()
  })

  it('attaches a chosen result with the status the operator picked', async () => {
    api.searchFriedberg.mockResolvedValue([ROW_UNVERIFIED])
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    await userEvent.click(
      await screen.findByRole('button', { name: /attach as confirmed/i }),
    )

    // Would fail a component that always attached as 'proposed' -- the
    // status sent must be the one the operator's button named.
    await waitFor(() =>
      expect(api.attachFriedberg).toHaveBeenCalledWith(412, {
        friedberg_id: 2,
        status: 'confirmed',
      }),
    )
  })

  it('never shows a stale search after a newer one has already landed', async () => {
    let resolveFirst
    api.searchFriedberg
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveFirst = resolve
          }),
      )
      .mockImplementationOnce(() => Promise.resolve([ROW_UNVERIFIED]))
    renderWithProviders(<FriedbergLookup itemId={412} />)

    const lookUp = screen.getByRole('button', { name: /^look up$/i })
    await userEvent.click(lookUp) // slow, abandoned search
    await userEvent.click(lookUp) // fast, current search

    expect(await screen.findByText('FR-TEST-2')).toBeInTheDocument()

    // The abandoned search now resolves -- it must not overwrite the newer
    // result already on screen.
    resolveFirst([ROW_VERIFIED])
    await waitFor(() => expect(api.searchFriedberg).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('FR-TEST-1')).not.toBeInTheDocument()
    expect(screen.getByText('FR-TEST-2')).toBeInTheDocument()
  })
})
