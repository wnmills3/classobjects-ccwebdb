import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    getSignatureChoices: vi.fn(),
    searchFriedberg: vi.fn(),
    createFriedbergNumber: vi.fn(),
    attachFriedberg: vi.fn(),
    updateFriedbergNumber: vi.fn(),
  },
}))

import { api } from '../../api'
import FriedbergLookup from './FriedbergLookup'
import { emptyReference, renderWithProviders } from '../../../test/helpers'

// Obviously synthetic, per `docs/reference-data.md` -- these codes and
// numbers are not real catalog entries, not even in a fixture pretending to
// be one.
const SIGNATURES_ALL = [
  { code: 'TEST-SIG-A', label: 'Test Treasurer A / Test Secretary A' },
  { code: 'TEST-SIG-B', label: 'Test Treasurer B / Test Secretary B' },
]
const SIGNATURES_1963 = [SIGNATURES_ALL[0]]

const ROW_VERIFIED = {
  id: 1,
  fr_number: '9903',
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
  fr_number: '9904',
  verified: false,
  verified_at: null,
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
  // jsdom has no window.open, and a Look up with no match opens one.
  vi.spyOn(window, 'open').mockImplementation(() => ({}))
  api.getSignatureChoices.mockResolvedValue({
    table: 'signature_combination',
    values: SIGNATURES_ALL,
  })
  api.searchFriedberg.mockResolvedValue([])
  api.createFriedbergNumber.mockResolvedValue({ id: 9, fr_number: '9903' })
  api.attachFriedberg.mockResolvedValue({
    inventory_item_id: 412,
    friedberg_id: 9,
    friedberg_status: 'proposed',
    fr_number: '9903',
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

  it('asks for the pairs once on opening, and once more per year typed', async () => {
    renderWithProviders(<FriedbergLookup itemId={412} />)
    const select = await screen.findByLabelText(/signature combination/i)
    await waitFor(() =>
      expect(select).toHaveTextContent('Test Treasurer A / Test Secretary A'),
    )
    expect(api.getSignatureChoices).toHaveBeenCalledTimes(1)

    await userEvent.type(screen.getByLabelText(/series year/i), '1963')
    await waitFor(() =>
      expect(api.getSignatureChoices).toHaveBeenCalledWith(
        expect.objectContaining({ series_year: 1963 }),
      ),
    )
    // Not one request per keystroke: "1", "19", "196" were never asked.
    expect(api.getSignatureChoices).toHaveBeenCalledTimes(2)
  })

  it('offers no pairs for a series year that is not a number, and asks nothing', async () => {
    // "19x" names no series. It must not be read as "no year entered", which
    // would offer every pair as though any of them fitted.
    renderWithProviders(<FriedbergLookup itemId={412} />)
    const select = await screen.findByLabelText(/signature combination/i)
    await waitFor(() =>
      expect(select).toHaveTextContent('Test Treasurer A / Test Secretary A'),
    )

    await userEvent.type(screen.getByLabelText(/series year/i), '19x')
    // Past the narrowing delay, so a request it would make has been made.
    await new Promise((resolve) => setTimeout(resolve, 400))

    expect(api.getSignatureChoices).toHaveBeenCalledTimes(1)
    expect(select).not.toHaveTextContent('Test Treasurer A / Test Secretary A')
    expect(select).not.toHaveTextContent('Test Treasurer B / Test Secretary B')
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
      'TEST-SIG-A',
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
        signature_combination: 'TEST-SIG-A',
      }),
    )
  })

  it('shows each match as its number and a Use button, details on hover', async () => {
    api.searchFriedberg.mockResolvedValue([
      { ...ROW_VERIFIED, denomination: 'usd_note_1', note_type: 'frn' },
      ROW_UNVERIFIED,
    ])
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    const rows = await screen.findAllByRole('listitem')
    expect(rows).toHaveLength(2)
    // Only the number and "Use" on the row: codes and status run together
    // read as noise.
    expect(rows[0]).toHaveTextContent(/^9903\s*Use$/)
    expect(rows[1]).toHaveTextContent(/^9904\s*Use$/)
    // Would pass a component that labeled every row the same way only if
    // both fixtures agreed -- they deliberately do not.
    expect(rows[0]).toHaveAttribute('title', 'usd_note_1 · frn -- verified')
    expect(rows[1]).toHaveAttribute('title', 'unverified proposal')
  })

  it('after a failed attach, a retry only attaches -- it does not record again', async () => {
    // Recording it twice is refused as a duplicate, which would leave the
    // owner stuck.
    api.createFriedbergNumber.mockResolvedValue({ id: 9, fr_number: '9903' })
    api.attachFriedberg
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValue({ fr_number: '9903', friedberg_status: 'proposed' })
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await userEvent.type(await screen.findByLabelText(/fr\. number/i), '9903')

    await userEvent.click(screen.getByRole('button', { name: /save as proposed/i }))
    expect(await screen.findByText('network down')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /save as proposed/i }))
    await waitFor(() => expect(api.attachFriedberg).toHaveBeenCalledTimes(2))
    expect(api.createFriedbergNumber).toHaveBeenCalledTimes(1)
    expect(api.attachFriedberg).toHaveBeenLastCalledWith(412, {
      friedberg_id: 9,
      status: 'proposed',
    })
  })

  it('surfaces a 409 on recording rather than swallowing it', async () => {
    api.createFriedbergNumber.mockRejectedValue(
      Object.assign(new Error("fr_number '9903' is already recorded as row 7"), {
        status: 409,
      }),
    )
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    const frInput = await screen.findByLabelText(/fr\. number/i)
    await userEvent.type(frInput, '9903')
    await userEvent.click(screen.getByRole('button', { name: /save as proposed/i }))

    expect(await screen.findByText(/already recorded as row 7/i)).toBeInTheDocument()
    // The 409 must stop the flow, not be swallowed and attached anyway.
    expect(api.attachFriedberg).not.toHaveBeenCalled()
  })

  it('uses a verified match at once, as confirmed', async () => {
    // Confirmed once for these search fields, it needs no second Save: Use
    // is the whole step.
    api.searchFriedberg.mockResolvedValue([ROW_VERIFIED])
    api.attachFriedberg.mockResolvedValue({
      fr_number: '9903',
      friedberg_status: 'confirmed',
    })
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    await userEvent.click(await screen.findByRole('button', { name: 'Use 9903' }))

    await waitFor(() =>
      expect(api.attachFriedberg).toHaveBeenCalledWith(412, {
        friedberg_id: ROW_VERIFIED.id,
        status: 'confirmed',
      }),
    )
    // That catalog row, attached as it is -- never recorded a second time.
    expect(api.createFriedbergNumber).not.toHaveBeenCalled()
  })

  it('uses an unverified match as proposed, never confirming it unasked', async () => {
    api.searchFriedberg.mockResolvedValue([ROW_UNVERIFIED])
    api.attachFriedberg.mockResolvedValue({
      fr_number: '9904',
      friedberg_status: 'proposed',
    })
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    await userEvent.click(await screen.findByRole('button', { name: 'Use 9904' }))

    await waitFor(() =>
      expect(api.attachFriedberg).toHaveBeenCalledWith(412, {
        friedberg_id: 2,
        status: 'proposed',
      }),
    )
    expect(api.createFriedbergNumber).not.toHaveBeenCalled()
  })

  it('in the item editor, hands the choice back instead of attaching it', async () => {
    const onChoose = vi.fn()
    api.searchFriedberg.mockResolvedValue([ROW_UNVERIFIED])
    renderWithProviders(<FriedbergLookup itemId={412} onChoose={onChoose} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await userEvent.click(await screen.findByRole('button', { name: 'Use 9904' }))

    expect(onChoose).toHaveBeenCalledWith({
      friedberg_id: 2,
      status: 'proposed',
      fr_number: '9904',
    })
    expect(api.attachFriedberg).not.toHaveBeenCalled()
    expect(screen.getByText(/attached when you save/i)).toBeInTheDocument()
  })

  it('starts from what the note records, district and web press included', async () => {
    const item = {
      id: 412,
      denomination: 'usd_note_1',
      note_type: 'frn',
      seal_color: 'green',
      series_year: 1995,
      series_letter: null,
      signature_combination: 'TEST-SIG-A',
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
        signature_combination: 'TEST-SIG-A',
        district_letter: 'B',
        web_press: true,
      }),
    )
  })

  it('searches by where the note was printed, from what it records', async () => {
    // A 2017-A $1 is one number from Washington and another from Fort Worth.
    const item = {
      id: 412,
      denomination: 'usd_note_1',
      note_type: 'frn',
      series_year: 2017,
      series_letter: 'A',
      printing_facility: 'fw',
      face_plate_number: 'FW E82',
      back_plate_number: '1234',
      attributes: [],
    }
    renderWithProviders(<FriedbergLookup itemId={412} item={item} />)
    expect(screen.getByLabelText(/printed at/i)).toHaveValue('fw')
    expect(screen.getByLabelText(/face plate/i)).toHaveValue('FW E82')
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await waitFor(() =>
      expect(api.searchFriedberg).toHaveBeenCalledWith({
        denomination: 'usd_note_1',
        note_type: 'frn',
        series_year: 2017,
        series_letter: 'A',
        // The plates go only into the web search's question: they are not
        // catalog fields.
        printing_facility: 'fw',
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
      signature_combination: 'TEST-SIG-B',
      attributes: [],
    }
    renderWithProviders(<FriedbergLookup itemId={412} item={item} />)

    const select = await screen.findByLabelText(/signature combination/i)
    // Would fail a component that cleared a choice the narrowed list does
    // not hold -- silently, and it is usually the right one.
    await waitFor(() =>
      expect(select).toHaveTextContent(
        'Test Treasurer B / Test Secretary B (not listed for this series)',
      ),
    )
    expect(select).toHaveValue('TEST-SIG-B')

    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await waitFor(() =>
      expect(api.searchFriedberg).toHaveBeenCalledWith({
        series_year: 1963,
        signature_combination: 'TEST-SIG-B',
      }),
    )
  })

  it('with no match, Look up opens the web search itself, in a pop-up', async () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => ({}))
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
      signature_combination: 'TEST-SIG-A',
      fed_district: 'B',
      attributes: [],
    }
    renderWithProviders(<FriedbergLookup itemId={412} item={item} />, { reference })
    // Nothing to search the web about until the catalog has said no.
    expect(screen.queryByRole('button', { name: /search the web/i })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    const expected =
      'What is the Friedberg number for Series 1963-A $1 Federal Reserve Note New York Test Treasurer A Test Secretary A?'
    // Pressing Look up alone ends in the web search when the catalog has
    // nothing -- in a named pop-up window, not a tab, so the answer sits
    // beside the form and a second search reuses the same window.
    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        `https://www.google.com/ai?q=${encodeURIComponent(expected)}`,
        'friedberg-web-search',
        expect.stringContaining('popup'),
      ),
    )
    expect(open).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: /^copy/i })).toBeNull()
    expect(screen.getByLabelText(/fr\. number/i)).toHaveValue('')
    expect(screen.queryByText(/blocked the search window/i)).toBeNull()

    // The button stays, to open it again.
    await userEvent.click(screen.getByRole('button', { name: /search the web/i }))
    expect(open).toHaveBeenCalledTimes(2)
    // Searching the web writes nothing: no record, no attach.
    expect(api.createFriedbergNumber).not.toHaveBeenCalled()
    expect(api.attachFriedberg).not.toHaveBeenCalled()
    open.mockRestore()
  })

  it('says so when the browser blocks the search window', async () => {
    window.open.mockImplementation(() => null)
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    expect(await screen.findByText(/blocked the search window/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /search the web/i })).toBeEnabled()
  })

  it('in the item editor, searches at once and shows no fields', async () => {
    api.searchFriedberg.mockResolvedValue([ROW_VERIFIED])
    const item = { id: 412, series_year: 1963, series_letter: 'A', attributes: [] }
    renderWithProviders(<FriedbergLookup itemId={412} item={item} searchNow />)

    expect(await screen.findByRole('button', { name: 'Use 9903' })).toBeEnabled()
    expect(api.searchFriedberg).toHaveBeenCalledTimes(1)
    expect(api.searchFriedberg).toHaveBeenCalledWith({
      series_year: 1963,
      series_letter: 'A',
    })
    expect(screen.queryByLabelText(/series year/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /^look up$/i })).toBeNull()

    // A note recorded wrongly can still be searched differently.
    await userEvent.click(screen.getByRole('button', { name: /change search fields/i }))
    expect(screen.getByLabelText(/series year/i)).toHaveValue('1963')
    expect(screen.getByRole('button', { name: /^look up$/i })).toBeInTheDocument()
  })

  it('in the item editor, searches once even in StrictMode', async () => {
    renderWithProviders(
      <FriedbergLookup itemId={412} item={{ id: 412, attributes: [] }} searchNow />,
      { strict: true },
    )
    await waitFor(() => expect(window.open).toHaveBeenCalledTimes(1))
  })

  it('with a match, offers Use and still Search the web', async () => {
    // A match can be the wrong number recorded earlier: the owner then needs
    // the web search more than ever, so it is not taken away. It does not open by itself, though -- only a miss does that.
    api.searchFriedberg.mockResolvedValue([ROW_VERIFIED])
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    expect(await screen.findByRole('button', { name: 'Use 9903' })).toBeEnabled()
    expect(window.open).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /search the web/i }))
    expect(window.open).toHaveBeenCalledTimes(1)
    // The field is for a number not in the catalog; a match fills nothing.
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

    await userEvent.type(await screen.findByLabelText(/fr\. number/i), '9903')
    await userEvent.click(screen.getByRole('button', { name: /save as proposed/i }))

    await waitFor(() =>
      expect(api.createFriedbergNumber).toHaveBeenCalledWith({
        fr_number: '9903',
        district_letter: 'B',
        web_press: false,
      }),
    )
    await waitFor(() =>
      expect(onAttached).toHaveBeenCalledWith(
        expect.objectContaining({ fr_number: '9903' }),
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

    expect(await screen.findByText('9904')).toBeInTheDocument()

    // The abandoned search now resolves -- it must not overwrite the newer
    // result already on screen.
    resolveFirst([ROW_VERIFIED])
    await waitFor(() => expect(api.searchFriedberg).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('9903')).not.toBeInTheDocument()
    expect(screen.getByText('9904')).toBeInTheDocument()
  })
})

describe('FriedbergLookup: a series year that is not a number', () => {
  const REFUSAL = /is not a series year/i

  it('does not look it up, and says why', async () => {
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.type(screen.getByLabelText(/series year/i), '1963A')

    const lookUp = screen.getByRole('button', { name: /^look up$/i })
    expect(lookUp).toBeEnabled()
    await userEvent.click(lookUp)

    expect(await screen.findByText(REFUSAL)).toBeInTheDocument()
    expect(api.searchFriedberg).not.toHaveBeenCalled()
  })

  it('does not record a number without its year, and says why', async () => {
    // Recorded with the year dropped, the row would be a type that a search
    // for its year no longer finds by it.
    renderWithProviders(<FriedbergLookup itemId={412} />)
    const year = screen.getByLabelText(/series year/i)
    await userEvent.type(year, '1963')
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await userEvent.type(await screen.findByLabelText(/fr\. number/i), '9903')
    await userEvent.type(year, 'A')

    const save = screen.getByRole('button', { name: /save as proposed/i })
    expect(save).toBeEnabled()
    await userEvent.click(save)

    expect(await screen.findByText(REFUSAL)).toBeInTheDocument()
    expect(api.createFriedbergNumber).not.toHaveBeenCalled()
    expect(api.attachFriedberg).not.toHaveBeenCalled()
    expect(screen.getByLabelText(/fr\. number/i)).toHaveValue('9903')
  })

  it('looks up and records again once the year is corrected', async () => {
    renderWithProviders(<FriedbergLookup itemId={412} />)
    const year = screen.getByLabelText(/series year/i)
    await userEvent.type(year, '1963A')
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await screen.findByText(REFUSAL)

    await userEvent.type(year, '{Backspace}')
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))

    await waitFor(() =>
      expect(api.searchFriedberg).toHaveBeenCalledWith({ series_year: 1963 }),
    )
    expect(screen.queryByText(REFUSAL)).toBeNull()
  })
})

describe('FriedbergLookup: the signature pairs', () => {
  it('says so when they cannot be read', async () => {
    // An empty pulldown would read as "no pair fits this note".
    api.getSignatureChoices.mockRejectedValue(new Error('lost connection'))
    renderWithProviders(<FriedbergLookup itemId={412} />)
    expect(await screen.findByText(/lost connection/)).toBeInTheDocument()
  })
})

describe('FriedbergLookup: the form of a number typed in', () => {
  it('names a slip and keeps Save from sending it', async () => {
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await userEvent.type(await screen.findByLabelText(/fr\. number/i), '9928-')

    expect(screen.getByText(/ends with a hyphen/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /save as proposed/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /save as confirmed/i })).toBeDisabled()
  })

  it('saves the number as it is kept: trimmed, unprefixed, district in capitals', async () => {
    api.createFriedbergNumber.mockResolvedValue({ id: 30, fr_number: '9928-L' })
    api.attachFriedberg.mockResolvedValue({
      fr_number: '9928-L',
      friedberg_status: 'proposed',
    })
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await userEvent.type(await screen.findByLabelText(/fr\. number/i), ' Fr. 9928-l ')

    await userEvent.click(screen.getByRole('button', { name: /save as proposed/i }))

    await waitFor(() =>
      expect(api.createFriedbergNumber).toHaveBeenCalledWith(
        expect.objectContaining({ fr_number: '9928-L' }),
      ),
    )
  })

  it('offers to correct the row that already holds the combination, then uses it', async () => {
    // A number recorded without its district letter by a slip, then the
    // right one refused because its type is already on file under the slip.
    api.createFriedbergNumber.mockRejectedValue(
      Object.assign(
        new Error('That combination is already recorded as 9928- (row 14).'),
        {
          status: 409,
          body: {
            detail: 'That combination is already recorded as 9928- (row 14).',
            existing: { id: 14, fr_number: '9928-' },
          },
        },
      ),
    )
    api.updateFriedbergNumber.mockResolvedValue({ id: 14, fr_number: '9928-L' })
    api.attachFriedberg.mockResolvedValue({
      fr_number: '9928-L',
      friedberg_status: 'confirmed',
    })
    renderWithProviders(<FriedbergLookup itemId={412} />)
    await userEvent.click(screen.getByRole('button', { name: /^look up$/i }))
    await userEvent.type(await screen.findByLabelText(/fr\. number/i), '9928-L')
    await userEvent.click(screen.getByRole('button', { name: /save as confirmed/i }))

    expect(await screen.findByText(/already recorded as 9928-/)).toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('button', { name: 'Correct 9928- to 9928-L' }),
    )

    await waitFor(() =>
      expect(api.attachFriedberg).toHaveBeenCalledWith(412, {
        friedberg_id: 14,
        status: 'confirmed',
      }),
    )
    expect(api.updateFriedbergNumber).toHaveBeenCalledWith(14, { fr_number: '9928-L' })
    expect(api.updateFriedbergNumber.mock.invocationCallOrder[0]).toBeLessThan(
      api.attachFriedberg.mock.invocationCallOrder[0],
    )
  })
})
