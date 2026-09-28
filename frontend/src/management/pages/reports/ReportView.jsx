import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '../../api'
import { dateTime } from '../../../shared/format'
import {
  NUMERIC_KINDS,
  cellText,
  compareValues,
  isBlank,
  paramText,
  withMissing,
} from './values'

//: More columns than this and the report prints landscape (`report--wide`).
const PORTRAIT_COLUMNS = 6

/**
 * One report's answer: its table, totals and notes, Export workbook and
 * Print -- and, on paper only, a heading saying what was run.
 *
 * Every report prints from this with no report-specific code
 * (`docs/specs/reporting-design.md`, *Printing*): the stylesheet's
 * `@media print` hides the console around it, shows `.print-only`, repeats
 * the header row on every page and keeps a row whole. A report of more than
 * six columns is marked `report--wide`, which the stylesheet prints
 * landscape. Nothing here is carried by color alone.
 *
 * `sent` is the parameters the result was asked for with; the workbook is
 * asked for with exactly the same ones.
 */
export default function ReportView({ report, result, sent }) {
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  const fileUrl = useRef(null)
  const rowCount = result.rows.length
  const rowsText = `${rowCount.toLocaleString('en-US')} ${rowCount === 1 ? 'row' : 'rows'}`
  const wide = result.columns.length > PORTRAIT_COLUMNS

  // The last download's object URL, released on the next one or on leaving
  // -- not at once, which some browsers do before the save has read it.
  useEffect(
    () => () => {
      if (fileUrl.current) URL.revokeObjectURL(fileUrl.current)
    },
    [],
  )

  async function exportWorkbook() {
    setExporting(true)
    setExportError('')
    try {
      const { blob, filename } = await api.downloadReportWorkbook(report.id, sent)
      if (fileUrl.current) URL.revokeObjectURL(fileUrl.current)
      fileUrl.current = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = fileUrl.current
      link.download = filename ?? `${report.id}.xlsx`
      document.body.append(link)
      link.click()
      link.remove()
    } catch (err) {
      setExportError(err.message)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className={wide ? 'report report--wide' : 'report'}>
      <header className="print-only report-print-heading">
        <p className="print-title">{result.title}</p>
        {report.params.map((param) => (
          <p key={param.name}>
            {param.label}: {paramText(param, result.params?.[param.name])}
          </p>
        ))}
        <p>Run at {dateTime(result.run_at)}</p>
        <p>{rowsText}</p>
      </header>

      <div className="row report-actions screen-only">
        <button
          type="button"
          data-help="report_export"
          disabled={exporting}
          onClick={exportWorkbook}
        >
          Export workbook
        </button>
        <button type="button" data-help="report_print" onClick={() => window.print()}>
          Print
        </button>
        <span className="muted">
          {rowsText}, run {dateTime(result.run_at)}
        </span>
      </div>
      {exportError && <p className="error">{exportError}</p>}

      {rowCount > 0 && <ReportTable result={result} />}
      {result.notes.length > 0 && (
        <ul className="report-notes">
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * The rows, sortable by any column in the browser.
 *
 * A header sorts ascending, then descending, then back to the report's own
 * order. Each row is paired with its drill-down before sorting, so a sorted
 * row still links where it did. A row with a drill-down links to it from
 * the cell of the report's `link_column` -- the one naming what the link
 * opens, such as a listing's item code -- or from its first cell when the
 * result names none; a percentage links to the same page narrowed to the
 * items missing that column's field.
 */
function ReportTable({ result }) {
  const [sort, setSort] = useState({ key: null, descending: false })
  const { columns } = result
  const linkKey = result.link_column ?? columns[0]?.key

  const entries = result.rows.map((row, index) => ({
    row,
    drill: result.drills?.[index] ?? null,
    index,
  }))
  const sortColumn = columns.find((column) => column.key === sort.key)
  const shown = sortColumn
    ? [...entries].sort(
        (a, b) =>
          compareValues(
            sortColumn.kind,
            a.row[sortColumn.key],
            b.row[sortColumn.key],
            sort.descending,
          ) || a.index - b.index,
      )
    : entries

  function sortBy(key) {
    setSort((current) => {
      if (current.key !== key) return { key, descending: false }
      if (!current.descending) return { key, descending: true }
      return { key: null, descending: false }
    })
  }

  return (
    <table className="table report-table">
      <thead data-help="report_sort">
        <tr>
          {columns.map((column) => {
            const active = sort.key === column.key
            const direction = sort.descending ? 'descending' : 'ascending'
            return (
              <th
                key={column.key}
                className={alignment(column)}
                aria-sort={active ? direction : 'none'}
              >
                <button
                  type="button"
                  className="link sort-header"
                  onClick={() => sortBy(column.key)}
                >
                  {column.label}
                  {active && (
                    <span aria-hidden="true">{sort.descending ? ' ▼' : ' ▲'}</span>
                  )}
                </button>
              </th>
            )
          })}
        </tr>
      </thead>
      <tbody data-help="report_rows">
        {shown.map(({ row, drill, index }) => (
          <tr key={index}>
            {columns.map((column) => (
              <td key={column.key} className={alignment(column)}>
                <Cell
                  column={column}
                  value={row[column.key]}
                  drill={drill}
                  linked={column.key === linkKey}
                />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
      {result.totals && (
        <tfoot>
          <tr className="report-totals">
            {columns.map((column) => (
              <td key={column.key} className={alignment(column)}>
                {cellText(column.kind, result.totals[column.key])}
              </td>
            ))}
          </tr>
        </tfoot>
      )}
    </table>
  )
}

function alignment(column) {
  return NUMERIC_KINDS.has(column.kind) ? 'num' : undefined
}

function Cell({ column, value, drill, linked }) {
  const text = cellText(column.kind, value)
  if (!drill || isBlank(value)) return text
  if (linked) return <Link to={drill}>{text}</Link>
  if (column.kind === 'percent') {
    return <Link to={withMissing(drill, column.key)}>{text}</Link>
  }
  return text
}
