import userEvent from '@testing-library/user-event'
import { screen, waitFor, within } from '@testing-library/react'
import { useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listReports: vi.fn(),
    runReport: vi.fn(),
    downloadReportWorkbook: vi.fn(),
  },
}))

import { api } from '../api'
import { ApiError } from '../../shared/api'
import { dateTime, money } from '../../shared/format'
import { FIELD_HELP } from '../fieldHelp'
import { adminAuth, renderWithProviders } from '../../test/helpers'
import Reports from './Reports'

const CATALOG = [
  {
    id: 'dq_completeness',
    group: 'Data quality',
    title: 'Field completeness',
    purpose: 'How much of each field is filled in, by kind.',
    params: [],
  },
  {
    id: 'cb_holdings',
    group: 'Collection',
    title: 'Holdings',
    purpose: 'What the collection holds, by kind and denomination.',
    params: [
      {
        name: 'status',
        label: 'Status',
        type: 'choice',
        default: 'received',
        choices: ['ordered', 'received', 'all'],
      },
      {
        name: 'disposition',
        label: 'Disposition',
        type: 'choice',
        default: 'held',
        choices: ['held', 'returned_by_buyer', 'all'],
      },
    ],
  },
  {
    id: 'pr_outstanding',
    group: 'Purchasing and receiving',
    title: 'Not yet arrived',
    purpose: 'Purchases with something still to arrive.',
    params: [
      {
        name: 'overdue_days',
        label: 'Overdue after (days)',
        type: 'integer',
        default: 21,
        choices: null,
      },
    ],
  },
  {
    id: 'sl_offered',
    group: 'Selling',
    title: 'On offer',
    purpose: 'Everything listed for sale.',
    params: [],
  },
]

const COLUMNS = [
  { key: 'order', label: 'Order', kind: 'text' },
  { key: 'vendor', label: 'Vendor', kind: 'text' },
  { key: 'seller', label: 'Seller', kind: 'text' },
  { key: 'ordered', label: 'Ordered', kind: 'date' },
  { key: 'days_waiting', label: 'Days waiting', kind: 'count' },
  { key: 'outstanding', label: 'Outstanding', kind: 'count' },
  { key: 'items', label: 'Items', kind: 'count' },
  { key: 'outstanding_cost', label: 'Outstanding cost', kind: 'money' },
  { key: 'overdue', label: 'Overdue', kind: 'text' },
]

function outstanding(params = { overdue_days: 30 }) {
  return {
    id: 'pr_outstanding',
    group: 'Purchasing and receiving',
    title: 'Not yet arrived',
    params,
    run_at: '2026-09-28T10:15:00-04:00',
    columns: COLUMNS,
    rows: [
      {
        order: 'Order-0012',
        vendor: 'Mercer',
        seller: '',
        ordered: '2026-08-01',
        days_waiting: 58,
        outstanding: 2,
        items: 3,
        outstanding_cost: '1234.50',
        overdue: 'Overdue',
      },
      {
        order: 'Order-0020',
        vendor: 'Abbott',
        seller: 'coinhound',
        ordered: '2026-09-20',
        days_waiting: 8,
        outstanding: 1,
        items: 1,
        outstanding_cost: '9.99',
        overdue: '',
      },
      {
        order: 'Order-0015',
        vendor: 'Zeller',
        seller: null,
        ordered: null,
        days_waiting: null,
        outstanding: 4,
        items: 4,
        outstanding_cost: '100.00',
        overdue: '',
      },
    ],
    totals: {
      order: 'All purchases',
      vendor: null,
      seller: null,
      ordered: null,
      days_waiting: null,
      outstanding: 7,
      items: 8,
      outstanding_cost: '1344.49',
      overdue: null,
    },
    drills: ['/receiving?order=12', '/receiving?order=20', '/receiving?order=15'],
    notes: ['Overdue: waiting more than 30 days.'],
  }
}

function completeness() {
  return {
    id: 'dq_completeness',
    group: 'Data quality',
    title: 'Field completeness',
    params: {},
    run_at: '2026-09-28T10:15:00-04:00',
    columns: [
      { key: 'kind', label: 'Kind', kind: 'text' },
      { key: 'live_items', label: 'Live items', kind: 'count' },
      { key: 'year', label: 'Year', kind: 'percent' },
      { key: 'metal', label: 'Metal', kind: 'percent' },
    ],
    rows: [
      { kind: 'Coin', live_items: 40, year: '87.5', metal: '50.0' },
      { kind: 'Currency', live_items: 10, year: '100.0', metal: null },
    ],
    totals: null,
    drills: ['/inventory/coins?kind=coin', '/inventory/currency'],
    notes: [],
  }
}

/** The address the page is at, so a test can read what Run wrote there. */
function Address() {
  const location = useLocation()
  return <output data-testid="address">{location.search}</output>
}

function renderAt(route) {
  return renderWithProviders(
    <>
      <Reports />
      <Address />
    </>,
    { auth: adminAuth(), route },
  )
}

/** The text of each body row's first cell, top to bottom. */
function firstCells() {
  const table = screen.getByRole('table')
  const [body] = table.querySelectorAll('tbody')
  return [...body.querySelectorAll('tr')].map((row) => row.cells[0].textContent)
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listReports.mockResolvedValue(CATALOG)
  api.runReport.mockImplementation((id, params) =>
    Promise.resolve(id === 'dq_completeness' ? completeness() : outstanding(params)),
  )
})

afterEach(() => {
  vi.restoreAllMocks()
  // Undoes every `vi.stubGlobal` (the Export test's `URL.createObjectURL` /
  // `revokeObjectURL`), even if that test failed before reaching its own
  // cleanup -- a `delete` at the end of the test body would not run then,
  // and jsdom's real `URL` has neither, so a later test calling either
  // would throw with no clue why.
  vi.unstubAllGlobals()
})

describe('Reports page', () => {
  it('lists the catalog by group', async () => {
    renderAt('/reports')

    const catalog = await screen.findByRole('navigation', { name: 'Reports' })
    for (const group of [
      'Data quality',
      'Collection',
      'Purchasing and receiving',
      'Selling',
    ]) {
      expect(within(catalog).getByRole('heading', { name: group })).toBeInTheDocument()
    }
    const collection = within(catalog).getByRole('group', { name: 'Collection' })
    expect(within(collection).getByRole('link', { name: 'Holdings' })).toHaveAttribute(
      'href',
      '/reports?report=cb_holdings',
    )
    expect(api.runReport).not.toHaveBeenCalled()
  })

  it('runs the report the address names, with its parameters', async () => {
    renderAt('/reports?report=pr_outstanding&overdue_days=30')

    expect(
      await screen.findByRole('heading', { name: 'Not yet arrived', level: 2 }),
    ).toBeInTheDocument()
    expect(api.runReport).toHaveBeenCalledWith('pr_outstanding', { overdue_days: '30' })
    expect(screen.getByLabelText('Overdue after (days)')).toHaveValue(30)
    expect(screen.getByText('Purchases with something still to arrive.')).toBeVisible()
    expect(await screen.findByRole('cell', { name: 'Order-0012' })).toBeInTheDocument()
  })

  it('writes changed parameters to the address and runs them', async () => {
    const user = userEvent.setup()
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    let box = await screen.findByLabelText('Overdue after (days)')

    await user.clear(box)
    await user.type(box, '45')
    await user.click(screen.getByRole('button', { name: 'Run' }))

    await waitFor(() =>
      expect(screen.getByTestId('address')).toHaveTextContent(
        '?report=pr_outstanding&overdue_days=45',
      ),
    )
    expect(api.runReport).toHaveBeenLastCalledWith('pr_outstanding', {
      overdue_days: '45',
    })

    // The form starts again from the new address, holding what was run.
    box = screen.getByLabelText('Overdue after (days)')
    expect(box).toHaveValue(45)

    // A default needs no place in the address; the report reads the same.
    await user.clear(box)
    await user.type(box, '21')
    await user.click(screen.getByRole('button', { name: 'Run' }))
    await waitFor(() =>
      expect(screen.getByTestId('address').textContent).toBe('?report=pr_outstanding'),
    )
  })

  it('refuses to run with a parameter field left empty', async () => {
    const user = userEvent.setup()
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    const box = await screen.findByLabelText('Overdue after (days)')
    const callsBefore = api.runReport.mock.calls.length

    await user.clear(box)
    await user.click(screen.getByRole('button', { name: 'Run' }))

    expect(
      await screen.findByText('Enter a value for Overdue after (days).'),
    ).toBeInTheDocument()
    expect(api.runReport).toHaveBeenCalledTimes(callsBefore)
    expect(screen.getByTestId('address').textContent).toBe(
      '?report=pr_outstanding&overdue_days=30',
    )
  })

  it('runs again when Run is pressed on an unchanged form', async () => {
    const user = userEvent.setup()
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    await screen.findByRole('cell', { name: 'Order-0012' })

    await user.click(screen.getByRole('button', { name: 'Run' }))

    await waitFor(() => expect(api.runReport).toHaveBeenCalledTimes(2))
  })

  it('runs a choice parameter from a dropdown', async () => {
    const user = userEvent.setup()
    renderAt('/reports?report=cb_holdings')
    const disposition = await screen.findByLabelText('Disposition')
    expect(disposition).toHaveValue('held')

    await user.selectOptions(disposition, 'returned by buyer')
    await user.click(screen.getByRole('button', { name: 'Run' }))

    await waitFor(() =>
      expect(screen.getByTestId('address').textContent).toBe(
        '?report=cb_holdings&disposition=returned_by_buyer',
      ),
    )
  })

  it('shows a refused parameter beside the form, by its label', async () => {
    api.runReport.mockRejectedValue(
      new ApiError(422, 'overdue_days: too small', {
        detail: [
          {
            type: 'greater_than_equal',
            loc: ['overdue_days'],
            msg: 'Input should be greater than or equal to 1',
          },
        ],
      }),
    )
    renderAt('/reports?report=pr_outstanding&overdue_days=0')

    expect(
      await screen.findByText(
        'Overdue after (days): Input should be greater than or equal to 1',
      ),
    ).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('says so when the address names no such report', async () => {
    renderAt('/reports?report=nope')
    expect(
      await screen.findByText('There is no report named nope.'),
    ).toBeInTheDocument()
    expect(api.runReport).not.toHaveBeenCalled()
  })

  it('shows money from its decimal string, right-aligned, totals included', async () => {
    renderAt('/reports?report=pr_outstanding&overdue_days=30')

    const cell = await screen.findByRole('cell', { name: money('1234.50') })
    expect(cell).toHaveClass('num')
    expect(screen.getByRole('cell', { name: money('1344.49') })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'All purchases' })).toBeInTheDocument()
    expect(screen.getByText('Overdue: waiting more than 30 days.')).toBeInTheDocument()
  })

  it('never rounds money through a float', async () => {
    const result = outstanding()
    // Too many digits for a JavaScript number: through parseFloat it reads
    // $12,345,678,901,234,568.00.
    result.rows[0].outstanding_cost = '12345678901234567.89'
    api.runReport.mockResolvedValue(result)
    renderAt('/reports?report=pr_outstanding&overdue_days=30')

    expect(
      await screen.findByRole('cell', { name: '$12,345,678,901,234,567.89' }),
    ).toBeInTheDocument()
  })

  it('marks an overdue row in words', async () => {
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    const row = (await screen.findByRole('cell', { name: 'Order-0012' })).closest('tr')
    expect(within(row).getByText('Overdue')).toBeInTheDocument()
  })

  it('sorts by a header, then back, and keeps each row with its drill-down', async () => {
    const user = userEvent.setup()
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    await screen.findByRole('cell', { name: 'Order-0012' })
    const vendor = screen.getByRole('button', { name: 'Vendor' })

    await user.click(vendor)
    expect(firstCells()).toEqual(['Order-0020', 'Order-0012', 'Order-0015'])
    expect(screen.getByRole('columnheader', { name: /Vendor/ })).toHaveAttribute(
      'aria-sort',
      'ascending',
    )
    expect(screen.getByRole('link', { name: 'Order-0020' })).toHaveAttribute(
      'href',
      '/receiving?order=20',
    )

    await user.click(vendor)
    expect(firstCells()).toEqual(['Order-0015', 'Order-0012', 'Order-0020'])

    await user.click(vendor)
    expect(firstCells()).toEqual(['Order-0012', 'Order-0020', 'Order-0015'])
    expect(screen.getByRole('columnheader', { name: /Vendor/ })).toHaveAttribute(
      'aria-sort',
      'none',
    )
    expect(screen.getByRole('link', { name: 'Order-0015' })).toHaveAttribute(
      'href',
      '/receiving?order=15',
    )
  })

  it('sorts money by amount, not as text', async () => {
    const user = userEvent.setup()
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    await screen.findByRole('cell', { name: 'Order-0012' })

    await user.click(screen.getByRole('button', { name: 'Outstanding cost' }))

    // As text, "9.99" would sort after "100.00" and "1234.50".
    expect(firstCells()).toEqual(['Order-0020', 'Order-0015', 'Order-0012'])
  })

  it('links a row to its drill-down, in the same tab', async () => {
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    const link = await screen.findByRole('link', { name: 'Order-0012' })
    expect(link).toHaveAttribute('href', '/receiving?order=12')
    expect(link).not.toHaveAttribute('target')
  })

  it('links a percent cell to the items missing that field', async () => {
    renderAt('/reports?report=dq_completeness')

    const coin = await screen.findByRole('link', { name: '87.5%' })
    expect(coin).toHaveAttribute('href', '/inventory/coins?kind=coin&missing=year')
    expect(screen.getByRole('link', { name: '100.0%' })).toHaveAttribute(
      'href',
      '/inventory/currency?missing=year',
    )
    // Not applicable to currency: an empty cell, and nothing to follow.
    expect(screen.queryByRole('link', { name: '%' })).not.toBeInTheDocument()
  })

  it('exports the workbook with the same parameters', async () => {
    const user = userEvent.setup()
    const blob = new Blob(['xlsx'])
    api.downloadReportWorkbook.mockResolvedValue({ blob, filename: 'report.xlsx' })
    // jsdom has neither; the browser's are what the page calls. Stubbed,
    // not assigned, so `afterEach`'s `vi.unstubAllGlobals()` removes them
    // even if this test fails before its own cleanup would.
    const createObjectURL = vi.fn(() => 'blob:report')
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL: vi.fn() })
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    await screen.findByRole('cell', { name: 'Order-0012' })

    await user.click(screen.getByRole('button', { name: 'Export workbook' }))

    await waitFor(() => expect(click).toHaveBeenCalled())
    expect(api.downloadReportWorkbook).toHaveBeenCalledWith('pr_outstanding', {
      overdue_days: '30',
    })
    expect(createObjectURL).toHaveBeenCalledWith(blob)
  })

  it('prints from the browser', async () => {
    const user = userEvent.setup()
    const print = vi.spyOn(window, 'print').mockImplementation(() => {})
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    await screen.findByRole('cell', { name: 'Order-0012' })

    await user.click(screen.getByRole('button', { name: 'Print' }))

    expect(print).toHaveBeenCalledTimes(1)
  })

  it('carries a heading for paper: title, parameters in words, run time, rows', async () => {
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    await screen.findByRole('cell', { name: 'Order-0012' })

    const heading = document.querySelector('.print-only')
    expect(heading).not.toBeNull()
    const paper = within(heading)
    expect(paper.getByText('Not yet arrived')).toBeInTheDocument()
    expect(paper.getByText('Overdue after (days): 30')).toBeInTheDocument()
    expect(
      paper.getByText(`Run at ${dateTime('2026-09-28T10:15:00-04:00')}`),
    ).toBeInTheDocument()
    expect(paper.getByText('3 rows')).toBeInTheDocument()
  })

  it('explains every control on the page in the help band', async () => {
    for (const route of [
      '/reports?report=pr_outstanding&overdue_days=30',
      '/reports?report=cb_holdings',
    ]) {
      const { unmount } = renderAt(route)
      await screen.findByRole('cell', { name: 'Order-0012' })
      const controls = document.querySelectorAll('input, select, button, a')
      expect(controls.length).toBeGreaterThan(10)
      const uncovered = [...controls]
        .filter((control) => !FIELD_HELP[control.closest('[data-help]')?.dataset.help])
        .map((control) => control.textContent || control.outerHTML)
      expect(uncovered).toEqual([])
      unmount()
    }
  })

  it('marks a report of more than six columns to print landscape', async () => {
    renderAt('/reports?report=pr_outstanding&overdue_days=30')
    await screen.findByRole('cell', { name: 'Order-0012' })
    expect(document.querySelector('.report')).toHaveClass('report--wide')
  })

  it('leaves a narrow report portrait', async () => {
    renderAt('/reports?report=dq_completeness')
    await screen.findByRole('cell', { name: 'Coin' })
    expect(document.querySelector('.report')).not.toHaveClass('report--wide')
  })
})
