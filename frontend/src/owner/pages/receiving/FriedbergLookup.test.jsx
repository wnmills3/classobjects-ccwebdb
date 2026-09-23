import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getSignatureChoices: vi.fn(),
    searchFriedberg: vi.fn(),
    createFriedbergNumber: vi.fn(),
    attachFriedberg: vi.fn(),
  },
}))

import { api } from '../../api'
import FriedbergLookup, { webSearchText } from './FriedbergLookup'
import { emptyReference, renderWithProviders } from '../../../test/helpers'

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
  api.getSignatureChoices.mockResolvedValue({
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
    api.getSignatureChoices.mockImplementation((params = {}) =>
      Promise.resolve({
        table: 'signature_combination',
        values: params.series_year ? SIGNATURES_1963 : SIGNATURES_ALL,
      }),
    )
    renderWithProviders(<FriedbergLookup itemId={412} />)

    // No year entered yet: the unnarrowed list, not an empty one.
    await waitFor(() => expect(api.getSignatureChoices).toHaveBeenCalledWith({}))
    const select = await screen.findByLabelText(/signature combination/i)
    expect(select).toHaveTextContent('Test Treasurer A / Test Secretary A')
    expect(select).toHaveTextContent('Test Treasurer B / Test Secretary B')

    await userEvent.type(screen.getByLabelText(/series year/i), '1963')

    await waitFor(() =>
      expect(api.getSignatureChoices).toHaveBeenCalledWith(
        expect.objectContaining({ series_year: 1963 }),
      ),
    )
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
    await waitFor(() =>
      expect(api.getSignatureChoices).toHaveBeenCalledWith(
        expect.objectContaining({ series_year: 2017 }),
      ),
    )
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

  it('shows each match as its number and a Copy button, details on hover', async () => {
    api.searchFriedberg.mockResolvedValue([
      { ...ROW_VERIFIED, denomination: 'usd_note_1', note_type: 'frn' },
      ROW_UNVERIFIED,
    ])
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(2)
    // Only the number and "Copy" on the row -- the owner asked for exactly
    // that (2026-09-23): codes and status run together read as noise.
    expect(rows[0]).toHaveTextContent(/^FR-TEST-1\s*Copy$/)
    expect(rows[1]).toHaveTextContent(/^FR-TEST-2\s*Copy$/)
    // Would pass a component that labeled every row the same way only if
    // both fixtures agreed -- they deliberately do not.
    expect(rows[0]).toHaveAttribute('title', 'usd_note_1 · frn -- verified')
    expect(rows[1]).toHaveAttribute('title', 'unverified proposal')
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
    await userEvent.click(screen.getByRole('button', { name: /save as proposed/i }))

    expect(await screen.findByText(/already recorded as row 7/i)).toBeInTheDocument()
    // The 409 must stop the flow, not be swallowed and attached anyway.
    expect(api.attachFriedberg).not.toHaveBeenCalled()
  })

  it('copies a match into the field, then saves it with the status picked', async () => {
    api.searchFriedberg.mockResolvedValue([ROW_UNVERIFIED])
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    await userEvent.click(await screen.findByRole('button', { name: 'Copy FR-TEST-2' }))
    expect(screen.getByLabelText(/fr\. number/i)).toHaveValue('FR-TEST-2')
    await userEvent.click(screen.getByRole('button', { name: /save as confirmed/i }))

    // Would fail a component that always attached as 'proposed' -- the
    // status sent must be the one the operator's button named.
    await waitFor(() =>
      expect(api.attachFriedberg).toHaveBeenCalledWith(412, {
        friedberg_id: 2,
        status: 'confirmed',
      }),
    )
    // The copied number is that catalogue row: attached as it is, never
    // recorded a second time (which would be a 409).
    expect(api.createFriedbergNumber).not.toHaveBeenCalled()
  })

  it('starts from what the note records, district and web press included', async () => {
    const item = {
      id: 412,
      denomination: 'usd_note_1',
      note_type: 'frn',
      seal_color: 'green',
      series_year: 1995,
      series_letter: null,
      signature_combination: 'FR-TEST-SIG-A',
      fed_district: 'B',
      attributes: [{ code: 'web_press', label: 'Web Press Note' }],
    }
    renderWithProviders(<FriedbergLookup itemId={412} item={item} />)
    await waitFor(() =>
      expect(api.getSignatureChoices).toHaveBeenCalledWith(
        expect.objectContaining({ series_year: 1995 }),
      ),
    )

    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    // Would fail a form that started blank, or that dropped the district or
    // the press: nothing here was typed.
    await waitFor(() =>
      expect(api.searchFriedberg).toHaveBeenCalledWith({
        denomination: 'usd_note_1',
        note_type: 'frn',
        seal_color: 'green',
        series_year: 1995,
        signature_combination: 'FR-TEST-SIG-A',
        district_letter: 'B',
        web_press: true,
      }),
    )
  })

  it('narrows signatures by the whole series, letter included', async () => {
    const item = {
      id: 412,
      denomination: 'usd_note_1',
      note_type: 'frn',
      seal_color: 'green',
      series_year: 1963,
      series_letter: 'A',
      attributes: [],
    }
    renderWithProviders(<FriedbergLookup itemId={412} item={item} />)
    // Would fail a component that narrowed by year alone -- the letter is
    // what moves 1963 to 1963-A and its later signers (CC-007656).
    await waitFor(() =>
      expect(api.getSignatureChoices).toHaveBeenCalledWith({
        denomination: 'usd_note_1',
        note_type: 'frn',
        seal_color: 'green',
        series_year: 1963,
        series_letter: 'A',
      }),
    )
  })

  it("keeps the note's own signatures even when the list leaves them out", async () => {
    // The narrowed list offers only pair A; the note records pair B.
    api.getSignatureChoices.mockImplementation((params = {}) =>
      Promise.resolve({
        values: params.series_year ? SIGNATURES_1963 : SIGNATURES_ALL,
        source: params.series_year ? 'note_issue' : 'all',
      }),
    )
    const item = {
      id: 412,
      series_year: 1963,
      signature_combination: 'FR-TEST-SIG-B',
      attributes: [],
    }
    renderWithProviders(<FriedbergLookup itemId={412} item={item} />)

    const select = await screen.findByLabelText(/signature combination/i)
    // Would fail the old behaviour, which cleared a choice the narrowed list
    // did not hold -- silently, and it was usually the right one.
    await waitFor(() =>
      expect(select).toHaveTextContent(
        'Test Treasurer B / Test Secretary B (not listed for this series)',
      ),
    )
    expect(select).toHaveValue('FR-TEST-SIG-B')

    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await waitFor(() =>
      expect(api.searchFriedberg).toHaveBeenCalledWith({
        series_year: 1963,
        signature_combination: 'FR-TEST-SIG-B',
      }),
    )
  })

  it('with no match, offers Search the web instead of Copy, in a pop-up', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    const reference = emptyReference({
      tables: {
        denomination: [{ code: 'usd_note_1', label: '$1 Bill' }],
        note_type: [{ code: 'frn', label: 'Federal Reserve Note' }],
        fed_district: [{ code: 'B', label: 'B - New York' }],
      },
    })
    const item = {
      id: 412,
      denomination: 'usd_note_1',
      note_type: 'frn',
      series_year: 1963,
      series_letter: 'A',
      signature_combination: 'FR-TEST-SIG-A',
      fed_district: 'B',
      attributes: [],
    }
    renderWithProviders(<FriedbergLookup itemId={412} item={item} />, { reference })
    // Nothing to search the web about until the catalogue has said no.
    expect(screen.queryByRole('button', { name: /search the web/i })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    const searchButton = await screen.findByRole('button', { name: /search the web/i })
    expect(screen.queryByRole('button', { name: /^copy/i })).toBeNull()
    expect(screen.getByLabelText(/fr\. number/i)).toHaveValue('')

    await userEvent.click(searchButton)
    const expected =
      'What is the Friedberg number for Series 1963-A $1 Federal Reserve Note New York Test Treasurer A Test Secretary A?'
    // A named pop-up window, not a tab: the results sit beside the form, and
    // a second search reuses the same window.
    expect(open).toHaveBeenCalledWith(
      `https://www.google.com/ai?q=${encodeURIComponent(expected)}`,
      'friedberg-web-search',
      expect.stringContaining('popup'),
    )
    // Searching the web writes nothing: no record, no attach.
    expect(api.createFriedbergNumber).not.toHaveBeenCalled()
    expect(api.attachFriedberg).not.toHaveBeenCalled()
    open.mockRestore()
  })

  it('with a match, offers Copy instead of Search the web', async () => {
    api.searchFriedberg.mockResolvedValue([ROW_VERIFIED])
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    expect(await screen.findByRole('button', { name: 'Copy FR-TEST-1' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /search the web/i })).toBeNull()
    // The field stays blank until Copy is pressed.
    expect(screen.getByLabelText(/fr\. number/i)).toHaveValue('')
    expect(screen.getByRole('button', { name: /save as proposed/i })).toBeDisabled()
  })

  it('does not read a missing Web Press attribute as sheet-fed', async () => {
    renderWithProviders(
      <FriedbergLookup itemId={412} item={{ id: 412, attributes: [] }} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await waitFor(() => expect(api.searchFriedberg).toHaveBeenCalledWith({}))
  })

  it('records the district and press it was given, and says it attached', async () => {
    const onAttached = vi.fn()
    renderWithProviders(<FriedbergLookup itemId={412} onAttached={onAttached} />)
    await userEvent.type(screen.getByLabelText(/district/i), 'B')
    await userEvent.selectOptions(screen.getByLabelText(/web press/i), 'no')
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    await userEvent.type(await screen.findByLabelText(/fr\. number/i), 'FR-TEST-1')
    await userEvent.click(screen.getByRole('button', { name: /save as proposed/i }))

    await waitFor(() =>
      expect(api.createFriedbergNumber).toHaveBeenCalledWith({
        fr_number: 'FR-TEST-1',
        district_letter: 'B',
        web_press: false,
      }),
    )
    await waitFor(() =>
      expect(onAttached).toHaveBeenCalledWith(
        expect.objectContaining({ fr_number: 'FR-TEST-1' }),
      ),
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

describe('webSearchText', () => {
  const labels = {
    denomination: { usd_note_1: '$1 Bill' },
    note_type: { frn: 'Federal Reserve Note' },
    fed_district: { B: 'B - New York' },
    signature_combination: { granahan_fowler: 'Granahan / Fowler' },
  }

  it('reads as a listing does: series, bill, type, city, signers', () => {
    expect(
      webSearchText(
        {
          seriesYear: '1963',
          seriesLetter: 'A',
          denomination: 'usd_note_1',
          noteType: 'frn',
          district: 'B',
          signatureCombination: 'granahan_fowler',
        },
        labels,
      ),
    ).toBe(
      'What is the Friedberg number for Series 1963-A $1 Federal Reserve Note New York Granahan Fowler?',
    )
  })

  it('names web press only when it is known, and no letter when there is none', () => {
    const fields = { seriesYear: '1995', denomination: 'usd_note_1', press: 'yes' }
    expect(webSearchText(fields, labels)).toBe(
      'What is the Friedberg number for Series 1995 $1 web press?',
    )
    expect(webSearchText({ ...fields, press: 'no' }, labels)).toBe(
      'What is the Friedberg number for Series 1995 $1?',
    )
  })

  it('falls back to the code while a vocabulary is still loading', () => {
    expect(webSearchText({ noteType: 'frn' })).toBe(
      'What is the Friedberg number for frn?',
    )
  })

  it('still asks a question when nothing is known yet', () => {
    expect(webSearchText({})).toBe('What is the Friedberg number for this US banknote?')
  })
})
