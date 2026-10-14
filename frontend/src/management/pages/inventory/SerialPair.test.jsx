import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: { getSerialPairs: vi.fn() },
}))

import { api } from '../../api'
import SerialPair from './SerialPair'

// Made-up serials and item codes.
const STAR = {
  id: 7,
  item_code: 'CC-000007',
  serial_number: 'F12345678*',
  denomination: '$1 Bill',
  series: '2017',
}
const PLAIN = { ...STAR, id: 8, item_code: 'CC-000008', serial_number: 'B12345678A' }

/**
 * Wait until the search has been answered and the answer shown: an
 * assertion made before then holds whatever the answer would show.
 */
async function answered() {
  await act(async () => {
    await api.getSerialPairs.mock.results[0].value.catch(() => {})
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getSerialPairs.mockResolvedValue([])
})

describe('SerialPair', () => {
  it('names the star note of the serial, with what it is', async () => {
    api.getSerialPairs.mockResolvedValue([STAR])
    render(<SerialPair itemId={412} serial=" B12345678A " />)

    expect(
      await screen.findByText(
        'The collection holds the star note of this serial number: ' +
          'CC-000007 F12345678* ($1 Bill, Series 2017)',
      ),
    ).toBeInTheDocument()
    expect(api.getSerialPairs).toHaveBeenCalledWith(412, 'B12345678A')
  })

  it('names the note without the star when the serial is a star note', async () => {
    api.getSerialPairs.mockResolvedValue([
      PLAIN,
      { ...PLAIN, id: 9, item_code: 'CC-000009', denomination: null, series: null },
    ])
    render(<SerialPair itemId={412} serial="F12345678*" />)

    expect(
      await screen.findByText(
        'The collection holds this serial number without the star: ' +
          'CC-000008 B12345678A ($1 Bill, Series 2017); CC-000009 B12345678A',
      ),
    ).toBeInTheDocument()
  })

  it('says nothing when there is no match', async () => {
    render(<SerialPair itemId={412} serial="B12345678A" />)
    await waitFor(() => expect(api.getSerialPairs).toHaveBeenCalled())
    await answered()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('asks nothing until the serial has its eight digits', async () => {
    api.getSerialPairs.mockResolvedValue([STAR])
    const { rerender } = render(<SerialPair itemId={412} serial="B1234567" />)
    rerender(<SerialPair itemId={412} serial="" />)
    rerender(<SerialPair itemId={412} serial={null} />)
    rerender(<SerialPair itemId={412} serial="B123456789A" />)
    // Long enough for a debounced request to have gone out, had one been due.
    await new Promise((resolve) => setTimeout(resolve, 400))

    expect(api.getSerialPairs).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('drops the match when the serial is changed to one with none', async () => {
    api.getSerialPairs.mockImplementation((_id, serial) =>
      Promise.resolve(serial === 'B12345678A' ? [STAR] : []),
    )
    const { rerender } = render(<SerialPair itemId={412} serial="B12345678A" />)
    expect(await screen.findByRole('status')).toBeInTheDocument()

    rerender(<SerialPair itemId={412} serial="B12345679A" />)

    await waitFor(() =>
      expect(api.getSerialPairs).toHaveBeenCalledWith(412, 'B12345679A'),
    )
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
  })

  it('shows nothing, not an error, when the search fails', async () => {
    api.getSerialPairs.mockRejectedValue(new Error('boom'))
    render(<SerialPair itemId={412} serial="B12345678A" />)
    await waitFor(() => expect(api.getSerialPairs).toHaveBeenCalled())
    await answered()
    expect(screen.queryByText(/boom/)).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
